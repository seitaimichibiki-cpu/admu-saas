"""
db.py - SQLite / PostgreSQL 自動切替 マルチクリニック対応データベース
DATABASE_URL 環境変数があれば PostgreSQL、なければ SQLite を使用。
"""
import sqlite3
import os
from datetime import datetime, timedelta
from typing import Optional

DATABASE_URL = os.environ.get("DATABASE_URL", "")
USE_PG = bool(DATABASE_URL)
DB_PATH = os.path.join(os.path.dirname(__file__), "ads_system.db")

if USE_PG:
    import psycopg2
    import psycopg2.extras


def _q(sql: str) -> str:
    """SQLite の ? プレースホルダを PostgreSQL の %s に変換"""
    if not USE_PG:
        return sql
    # ? を %s に変換（ただし ?? はエスケープとして扱わない）
    return sql.replace("?", "%s")


class _PGCursorWrapper:
    """psycopg2のカーソルをsqlite3.Cursor互換にするラッパー"""
    def __init__(self, cursor):
        self._cur = cursor

    def fetchone(self):
        row = self._cur.fetchone()
        return dict(row) if row else None

    def fetchall(self):
        rows = self._cur.fetchall()
        return [dict(r) for r in rows]

    @property
    def lastrowid(self):
        """PGではINSERT ... RETURNING idで取得する必要があるため、
        execute時にRETURNINGを付与している場合のみ有効"""
        return self._lastrowid

    @lastrowid.setter
    def lastrowid(self, val):
        self._lastrowid = val

    @property
    def rowcount(self):
        return self._cur.rowcount


class _PGConnWrapper:
    """psycopg2コネクションをsqlite3.Connection互換にするラッパー。
    既存の conn.execute(sql, params).fetchone() パターンをそのまま使える。"""

    def __init__(self, pg_conn):
        self._conn = pg_conn

    def execute(self, sql, params=()):
        sql = _q(sql)
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # INSERT文でRETURNING idを自動付与（lastrowid対応）
        wrapper = _PGCursorWrapper(cur)
        wrapper.lastrowid = None
        is_insert = sql.strip().upper().startswith("INSERT")
        if is_insert and "RETURNING" not in sql.upper():
            sql_with_ret = sql.rstrip().rstrip(";") + " RETURNING id"
            try:
                cur.execute(sql_with_ret, params)
                row = cur.fetchone()
                wrapper.lastrowid = row["id"] if row else None
                return wrapper
            except Exception:
                # RETURNING idが使えないテーブル（idカラムがない等）の場合はフォールバック
                self._conn.rollback()
                cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                wrapper = _PGCursorWrapper(cur)
                wrapper.lastrowid = None

        # INSERT OR IGNORE → INSERT ... ON CONFLICT DO NOTHING (PostgreSQL)
        if "INSERT OR IGNORE" in sql:
            sql = sql.replace("INSERT OR IGNORE", "INSERT")
            sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        cur.execute(sql, params)
        return wrapper

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def cursor(self, **kwargs):
        return self._conn.cursor(**kwargs)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self._conn.rollback()
        self._conn.close()
        return False


def get_conn():
    if USE_PG:
        pg_conn = psycopg2.connect(DATABASE_URL)
        pg_conn.autocommit = False
        return _PGConnWrapper(pg_conn)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn


def init_db():
    conn = get_conn()

    # DDL方言ヘルパー
    if USE_PG:
        PK = "SERIAL PRIMARY KEY"
        TS = "TIMESTAMPTZ DEFAULT NOW()"
        DT = "DATE DEFAULT CURRENT_DATE"
    else:
        PK = "INTEGER PRIMARY KEY AUTOINCREMENT"
        TS = "TEXT DEFAULT (datetime('now','localtime'))"
        DT = "TEXT DEFAULT (date('now','localtime'))"

    tables = [
        f"""CREATE TABLE IF NOT EXISTS clinics (
            id {PK}, name TEXT NOT NULL, license_key TEXT UNIQUE,
            parent_clinic_id INTEGER DEFAULT NULL, max_sub_accounts INTEGER DEFAULT 1,
            plan_status TEXT DEFAULT 'active', created_at {TS})""",
        f"""CREATE TABLE IF NOT EXISTS ads_accounts (
            id {PK}, clinic_id INTEGER NOT NULL, customer_id TEXT NOT NULL,
            developer_token TEXT, client_id TEXT, client_secret TEXT, refresh_token TEXT,
            login_customer_id TEXT, mock_mode INTEGER DEFAULT 1,
            line_channel_token TEXT, line_user_id TEXT,
            target_age_gender TEXT, target_job_lifestyle TEXT,
            target_pain_point TEXT, target_desired_outcome TEXT,
            notification_email TEXT, smtp_user TEXT, smtp_pass TEXT,
            ga4_property_id TEXT, ga4_api_secret TEXT,
            monthly_budget_yen INTEGER DEFAULT 300000,
            yahoo_account_id TEXT, yahoo_client_id TEXT,
            yahoo_client_secret TEXT, yahoo_refresh_token TEXT,
            yahoo_mock_mode INTEGER DEFAULT 1,
            created_at {TS}, FOREIGN KEY (clinic_id) REFERENCES clinics(id))""",
        f"""CREATE TABLE IF NOT EXISTS campaigns (
            id {PK}, clinic_id INTEGER NOT NULL, google_campaign_id TEXT,
            name TEXT NOT NULL, status TEXT DEFAULT 'ENABLED',
            budget_micros BIGINT DEFAULT 0, budget_locked INTEGER DEFAULT 0,
            campaign_type TEXT DEFAULT 'SEARCH', target_region TEXT,
            created_at {TS}, updated_at {TS},
            FOREIGN KEY (clinic_id) REFERENCES clinics(id))""",
        f"""CREATE TABLE IF NOT EXISTS ad_strategy_archives (
            id {PK}, clinic_id INTEGER NOT NULL, snapshot_date {DT},
            campaigns_json TEXT, adgroups_json TEXT, ads_json TEXT, keywords_json TEXT,
            performance_summary TEXT, notes TEXT, created_at {TS},
            FOREIGN KEY (clinic_id) REFERENCES clinics(id))""",
        f"""CREATE TABLE IF NOT EXISTS performance_logs (
            id {PK}, clinic_id INTEGER NOT NULL, campaign_id INTEGER,
            date TEXT NOT NULL, impressions INTEGER DEFAULT 0, clicks INTEGER DEFAULT 0,
            ctr REAL DEFAULT 0, avg_cpc_micros INTEGER DEFAULT 0,
            cost_micros BIGINT DEFAULT 0, conversions REAL DEFAULT 0, cvr REAL DEFAULT 0,
            recorded_at {TS}, FOREIGN KEY (clinic_id) REFERENCES clinics(id))""",
        f"""CREATE TABLE IF NOT EXISTS bid_rules (
            id {PK}, clinic_id INTEGER NOT NULL, campaign_id INTEGER,
            name TEXT NOT NULL, condition_field TEXT NOT NULL,
            condition_op TEXT NOT NULL, condition_value REAL NOT NULL,
            action TEXT NOT NULL, action_value REAL NOT NULL,
            max_adjustment_pct REAL DEFAULT 20.0, enabled INTEGER DEFAULT 1,
            created_at {TS}, FOREIGN KEY (clinic_id) REFERENCES clinics(id))""",
        f"""CREATE TABLE IF NOT EXISTS alerts (
            id {PK}, clinic_id INTEGER NOT NULL, campaign_id INTEGER,
            level TEXT DEFAULT 'INFO', message TEXT NOT NULL,
            notified INTEGER DEFAULT 0, created_at {TS},
            FOREIGN KEY (clinic_id) REFERENCES clinics(id))""",
        f"""CREATE TABLE IF NOT EXISTS ad_copies (
            id {PK}, clinic_id INTEGER NOT NULL, campaign_id INTEGER,
            headlines TEXT, descriptions TEXT, prompt_context TEXT,
            status TEXT DEFAULT 'draft', variant_group TEXT,
            ctr_score REAL DEFAULT 0, impressions INTEGER DEFAULT 0, clicks INTEGER DEFAULT 0,
            applied_at TEXT, created_at {TS},
            FOREIGN KEY (clinic_id) REFERENCES clinics(id))""",
        f"""CREATE TABLE IF NOT EXISTS negative_keywords (
            id {PK}, clinic_id INTEGER NOT NULL, campaign_id INTEGER,
            keyword TEXT NOT NULL, match_type TEXT DEFAULT 'BROAD',
            source TEXT DEFAULT 'manual', applied INTEGER DEFAULT 0,
            created_at {TS}, FOREIGN KEY (clinic_id) REFERENCES clinics(id))""",
        f"""CREATE TABLE IF NOT EXISTS personas (
            id {PK}, clinic_id INTEGER NOT NULL, name TEXT NOT NULL,
            age_gender TEXT, job_lifestyle TEXT, pain_point TEXT, desired_outcome TEXT,
            is_default INTEGER DEFAULT 0, created_at {TS},
            FOREIGN KEY (clinic_id) REFERENCES clinics(id))""",
        """CREATE TABLE IF NOT EXISTS campaign_personas (
            campaign_id TEXT NOT NULL, persona_id INTEGER NOT NULL,
            clinic_id INTEGER NOT NULL, PRIMARY KEY (campaign_id, persona_id))""",
        f"""CREATE TABLE IF NOT EXISTS contracts (
            id {PK}, clinic_id INTEGER NOT NULL UNIQUE,
            stripe_customer_id TEXT, plan_name TEXT DEFAULT 'スタンダード', monthly_fee INTEGER DEFAULT 0,
            started_at TEXT, renewal_at TEXT, status TEXT DEFAULT 'active',
            notes TEXT, created_at {TS},
            FOREIGN KEY (clinic_id) REFERENCES clinics(id))""",
        f"""CREATE TABLE IF NOT EXISTS users (
            id {PK}, clinic_id INTEGER NOT NULL,
            email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'user', is_active INTEGER DEFAULT 1,
            last_login_at TEXT, created_at {TS},
            FOREIGN KEY (clinic_id) REFERENCES clinics(id))""",
        f"""CREATE TABLE IF NOT EXISTS password_reset_tokens (
            token TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL, created_at {TS},
            FOREIGN KEY (user_id) REFERENCES users(id))""",
        f"""CREATE TABLE IF NOT EXISTS announcements (
            id {PK}, title TEXT NOT NULL, content TEXT NOT NULL,
            published_at {TS}, created_at {TS})""",
        f"""CREATE TABLE IF NOT EXISTS audit_logs (
            id {PK}, clinic_id INTEGER NOT NULL, user_email TEXT,
            action TEXT NOT NULL, entity TEXT, details TEXT,
            created_at {TS}, FOREIGN KEY (clinic_id) REFERENCES clinics(id))""",
        f"""CREATE TABLE IF NOT EXISTS ai_usage_logs (
            id {PK}, clinic_id INTEGER NOT NULL, year_month TEXT NOT NULL,
            feature_name TEXT NOT NULL, usage_count INTEGER DEFAULT 0,
            last_used_at {TS}, FOREIGN KEY (clinic_id) REFERENCES clinics(id),
            UNIQUE(clinic_id, year_month, feature_name))""",
        # Stripe Webhook冪等性チェック用（2重処理防止）
        f"""CREATE TABLE IF NOT EXISTS stripe_processed_events (
            event_id TEXT PRIMARY KEY,
            processed_at {TS})""",
    ]
    for ddl in tables:
        conn.execute(ddl)
    conn.commit()

    # 初期データが存在しなければ作成（ID:1となる）
    has_clinics = conn.execute("SELECT id FROM clinics LIMIT 1").fetchone()
    if not has_clinics:
        conn.execute("INSERT INTO clinics (name, license_key) VALUES ('システム管理者', 'DEMO-0000-0000-0000')")
        conn.commit()
        demo = conn.execute("SELECT id FROM clinics LIMIT 1").fetchone()
        clinic_id = demo["id"]
        conn.execute("INSERT INTO ads_accounts (clinic_id, customer_id, mock_mode) VALUES (?, 'DEMO-CUSTOMER-ID', 1)", (clinic_id,))
        conn.commit()

    # マイグレーション（既存DBへのカラム追加）
    migrations = [
        "ALTER TABLE ads_accounts ADD COLUMN target_age_gender TEXT",
        "ALTER TABLE ads_accounts ADD COLUMN target_job_lifestyle TEXT",
        "ALTER TABLE ads_accounts ADD COLUMN target_pain_point TEXT",
        "ALTER TABLE ads_accounts ADD COLUMN target_desired_outcome TEXT",
        "ALTER TABLE ad_copies ADD COLUMN variant_group TEXT",
        "ALTER TABLE ad_copies ADD COLUMN ctr_score REAL DEFAULT 0",
        "ALTER TABLE ad_copies ADD COLUMN impressions INTEGER DEFAULT 0",
        "ALTER TABLE ad_copies ADD COLUMN clicks INTEGER DEFAULT 0",
        "ALTER TABLE ads_accounts ADD COLUMN notification_email TEXT",
        "ALTER TABLE ads_accounts ADD COLUMN smtp_user TEXT",
        "ALTER TABLE ads_accounts ADD COLUMN smtp_pass TEXT",
        "ALTER TABLE ads_accounts ADD COLUMN ga4_property_id TEXT",
        "ALTER TABLE ads_accounts ADD COLUMN ga4_api_secret TEXT",
        "ALTER TABLE ads_accounts ADD COLUMN monthly_budget_yen INTEGER DEFAULT 300000",
        "ALTER TABLE clinics ADD COLUMN plan_status TEXT DEFAULT 'active'",
        "ALTER TABLE clinics ADD COLUMN max_sub_accounts INTEGER DEFAULT 1",
        "ALTER TABLE clinics ADD COLUMN parent_clinic_id INTEGER DEFAULT NULL",
        "ALTER TABLE ads_accounts ADD COLUMN yahoo_account_id TEXT",
        "ALTER TABLE ads_accounts ADD COLUMN yahoo_client_id TEXT",
        "ALTER TABLE ads_accounts ADD COLUMN yahoo_client_secret TEXT",
        "ALTER TABLE ads_accounts ADD COLUMN yahoo_refresh_token TEXT",
        "ALTER TABLE ads_accounts ADD COLUMN yahoo_mock_mode INTEGER DEFAULT 1",
        # contractsテーブル: AI利用上限（プラン別設定用、-1=無制限）
        "ALTER TABLE contracts ADD COLUMN ai_quota_monthly INTEGER DEFAULT 30",
    ]
    for sql in migrations:
        try:
            if USE_PG:
                conn.execute("SAVEPOINT migration_sp")
            conn.execute(sql)
            if USE_PG:
                conn.execute("RELEASE SAVEPOINT migration_sp")
        except Exception:
            if USE_PG:
                conn.execute("ROLLBACK TO SAVEPOINT migration_sp")
    conn.commit()

    # デフォルト入札ルールの投入（空の場合のみ）
    _insert_default_bid_rules(conn)

    conn.close()
    print(f"[DB] 初期化完了: {'PostgreSQL' if USE_PG else DB_PATH}")


def _insert_default_bid_rules(conn):
    """おすすめ入札ルールをデフォルト投入（既に入っている場合はスキップ）"""
    row = conn.execute("SELECT COUNT(*) as n FROM bid_rules WHERE clinic_id=1").fetchone()
    if row and row["n"] > 0:
        return
    presets = [
        ("🎯 高CTR維持ルール", "ctr", "<", 2.0, "adjust_bid", -10.0, 15.0),
        ("📈 CVR改善ルール", "cvr", "<", 3.0, "adjust_bid", +15.0, 20.0),
        ("💸 予算消化率チェック", "cost_ratio", ">", 80.0, "adjust_bid", -5.0, 10.0),
        ("🔥 高コンバージョン強化", "conversions", ">", 5.0, "adjust_bid", +20.0, 30.0),
    ]
    for name, field, op, val, action, aval, max_adj in presets:
        conn.execute("""
            INSERT INTO bid_rules (clinic_id, name, condition_field, condition_op, condition_value,
                                   action, action_value, max_adjustment_pct, enabled)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (name, field, op, val, action, aval, max_adj))
    conn.commit()


# ---- ペルソナ管理 ----
def list_personas(clinic_id: int):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM personas WHERE clinic_id=? ORDER BY is_default DESC, id",
            (clinic_id,)
        ).fetchall()
        return [dict(r) for r in rows]

def get_persona(persona_id: int, clinic_id: int):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM personas WHERE id=? AND clinic_id=?", (persona_id, clinic_id)
        ).fetchone()
        return dict(row) if row else None

def create_persona(clinic_id: int, data: dict) -> Optional[int]:
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO personas (clinic_id, name, age_gender, job_lifestyle, pain_point, desired_outcome, is_default)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (clinic_id, data.get("name"), data.get("age_gender"), data.get("job_lifestyle"),
              data.get("pain_point"), data.get("desired_outcome"), int(data.get("is_default", 0))))
        conn.commit()
        return cur.lastrowid

def update_persona(persona_id: int, clinic_id: int, data: dict):
    with get_conn() as conn:
        conn.execute("""
            UPDATE personas SET name=?, age_gender=?, job_lifestyle=?, pain_point=?, desired_outcome=?, is_default=?
            WHERE id=? AND clinic_id=?
        """, (data.get("name"), data.get("age_gender"), data.get("job_lifestyle"),
              data.get("pain_point"), data.get("desired_outcome"), int(data.get("is_default", 0)),
              persona_id, clinic_id))
        conn.commit()

def delete_persona(persona_id: int, clinic_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM personas WHERE id=? AND clinic_id=?", (persona_id, clinic_id))
        conn.execute("DELETE FROM campaign_personas WHERE persona_id=? AND clinic_id=?", (persona_id, clinic_id))
        conn.commit()

# ---- キャンペーン↔ペルソナ 紐付け ----
def get_campaign_personas(campaign_id: str, clinic_id: int):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT p.* FROM personas p
            JOIN campaign_personas cp ON cp.persona_id = p.id
            WHERE cp.campaign_id=? AND cp.clinic_id=?
        """, (campaign_id, clinic_id)).fetchall()
        return [dict(r) for r in rows]

def link_persona_to_campaign(campaign_id: str, persona_id: int, clinic_id: int):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO campaign_personas (campaign_id, persona_id, clinic_id)
            VALUES (?, ?, ?)
        """, (campaign_id, persona_id, clinic_id))
        conn.commit()

def unlink_persona_from_campaign(campaign_id: str, persona_id: int, clinic_id: int):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM campaign_personas WHERE campaign_id=? AND persona_id=? AND clinic_id=?",
            (campaign_id, persona_id, clinic_id)
        )
        conn.commit()

# ---- クリニック ----
def get_clinic(clinic_id: int):
    with get_conn() as conn:
        return dict(conn.execute("SELECT * FROM clinics WHERE id=?", (clinic_id,)).fetchone() or {})

def list_clinics():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM clinics ORDER BY id").fetchall()]

# ---- Ads アカウント設定 ----
def get_ads_account(clinic_id: int):
    import crypto_utils
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM ads_accounts WHERE clinic_id=?", (clinic_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        SECRET_FIELDS = ["developer_token", "client_secret", "refresh_token", "line_channel_token", "ga4_api_secret", "smtp_pass", "yahoo_client_secret", "yahoo_refresh_token"]
        for field in SECRET_FIELDS:
            if data.get(field):
                data[field] = crypto_utils.decrypt(data[field])
        return data

def save_ads_account(clinic_id: int, data: dict):
    import crypto_utils
    secure_data = dict(data)
    SECRET_FIELDS = ["developer_token", "client_secret", "refresh_token", "line_channel_token", "ga4_api_secret", "smtp_pass", "yahoo_client_secret", "yahoo_refresh_token"]
    for field in SECRET_FIELDS:
        if field in secure_data and secure_data[field]:
            secure_data[field] = crypto_utils.encrypt(secure_data[field])

    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM ads_accounts WHERE clinic_id=?", (clinic_id,)).fetchone()
        fields = ["customer_id", "developer_token", "client_id", "client_secret", "refresh_token",
                  "login_customer_id", "mock_mode", "line_channel_token", "line_user_id",
                  "target_age_gender", "target_job_lifestyle", "target_pain_point", "target_desired_outcome",
                  "notification_email", "smtp_user", "smtp_pass", "ga4_property_id", "ga4_api_secret",
                  "monthly_budget_yen", "yahoo_account_id", "yahoo_client_id", "yahoo_client_secret",
                  "yahoo_refresh_token", "yahoo_mock_mode"]
        if existing:
            sets = ", ".join(f"{f}=?" for f in fields if f in secure_data)
            vals = [secure_data[f] for f in fields if f in secure_data] + [clinic_id]
            conn.execute(f"UPDATE ads_accounts SET {sets} WHERE clinic_id=?", vals)
        else:
            conn.execute(
                "INSERT INTO ads_accounts (clinic_id,customer_id,mock_mode) VALUES (?,?,?)",
                (clinic_id, secure_data.get("customer_id", ""), secure_data.get("mock_mode", 1))
            )
        conn.commit()

# ---- キャンペーン ----
def list_campaigns(clinic_id: int):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM campaigns WHERE clinic_id=? ORDER BY id DESC", (clinic_id,)).fetchall()]

def get_campaign(campaign_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        return dict(row) if row else None

def upsert_campaign(clinic_id: int, data: dict) -> Optional[int]:
    with get_conn() as conn:
        if data.get("id"):
            conn.execute("""
                UPDATE campaigns SET name=?,status=?,target_region=?,updated_at=?
                WHERE id=? AND clinic_id=?
            """, (data["name"], data.get("status","ENABLED"), data.get("target_region",""),
                  datetime.now().strftime("%Y-%m-%d %H:%M:%S"), data["id"], clinic_id))
            conn.commit()
            return data["id"]
        else:
            cur = conn.execute("""
                INSERT INTO campaigns (clinic_id,name,status,budget_micros,campaign_type,target_region,google_campaign_id)
                VALUES (?,?,?,?,?,?,?)
            """, (clinic_id, data["name"], data.get("status","ENABLED"),
                  data.get("budget_micros",0), data.get("campaign_type","SEARCH"),
                  data.get("target_region",""), data.get("google_campaign_id","")))
            conn.commit()
            return cur.lastrowid

def update_budget(campaign_id: int, clinic_id: int, budget_micros: int):
    """予算は手動設定のみ。budget_locked=1 のものは拒否。"""
    with get_conn() as conn:
        row = conn.execute("SELECT budget_locked FROM campaigns WHERE id=? AND clinic_id=?",
                           (campaign_id, clinic_id)).fetchone()
        if not row:
            raise ValueError("キャンペーンが見つかりません")
        if row["budget_locked"]:
            raise ValueError("このキャンペーンの予算はロックされています")
        conn.execute("UPDATE campaigns SET budget_micros=?,updated_at=? WHERE id=?",
                     (budget_micros, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), campaign_id))
        conn.commit()

# ---- パフォーマンスログ ----
def insert_performance(clinic_id: int, data: dict):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO performance_logs
            (clinic_id,campaign_id,date,impressions,clicks,ctr,avg_cpc_micros,cost_micros,conversions,cvr)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (clinic_id, data.get("campaign_id"), data.get("date",""),
              data.get("impressions",0), data.get("clicks",0), data.get("ctr",0),
              data.get("avg_cpc_micros",0), data.get("cost_micros",0),
              data.get("conversions",0), data.get("cvr",0)))
        conn.commit()

def get_performance_summary(clinic_id: int, days: int = 7):
    with get_conn() as conn:
        if USE_PG:
            date_filter = "date >= (CURRENT_DATE - INTERVAL '%s days')" % days
            rows = conn.execute(f"""
                SELECT date, SUM(impressions) impressions, SUM(clicks) clicks,
                       AVG(ctr) ctr, AVG(avg_cpc_micros) avg_cpc_micros,
                       SUM(cost_micros) cost_micros, SUM(conversions) conversions, AVG(cvr) cvr
                FROM performance_logs
                WHERE clinic_id=? AND {date_filter}
                GROUP BY date ORDER BY date DESC
            """, (clinic_id,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT date, SUM(impressions) impressions, SUM(clicks) clicks,
                       AVG(ctr) ctr, AVG(avg_cpc_micros) avg_cpc_micros,
                       SUM(cost_micros) cost_micros, SUM(conversions) conversions, AVG(cvr) cvr
                FROM performance_logs
                WHERE clinic_id=?
                  AND date >= date('now', ? || ' days', 'localtime')
                GROUP BY date ORDER BY date DESC
            """, (clinic_id, f"-{days}")).fetchall()
        return [dict(r) for r in rows]

# ---- 入札ルール ----
def list_bid_rules(clinic_id: int):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM bid_rules WHERE clinic_id=? ORDER BY id", (clinic_id,)).fetchall()]

def upsert_bid_rule(clinic_id: int, data: dict) -> int:
    with get_conn() as conn:
        if data.get("id"):
            conn.execute("""UPDATE bid_rules
                SET name=?,condition_field=?,condition_op=?,condition_value=?,
                    action=?,action_value=?,max_adjustment_pct=?,enabled=?
                WHERE id=? AND clinic_id=?""",
                (data["name"],data["condition_field"],data["condition_op"],data["condition_value"],
                 data["action"],data["action_value"],data.get("max_adjustment_pct",20),
                 data.get("enabled",1),data["id"],clinic_id))
            conn.commit()
            return data["id"]
        cur = conn.execute("""INSERT INTO bid_rules
            (clinic_id,campaign_id,name,condition_field,condition_op,condition_value,action,action_value,max_adjustment_pct)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (clinic_id,data.get("campaign_id"),data["name"],data["condition_field"],
             data["condition_op"],data["condition_value"],data["action"],
             data["action_value"],data.get("max_adjustment_pct",20)))
        conn.commit()
        return cur.lastrowid

def delete_bid_rule(rule_id: int, clinic_id: int) -> bool:
    """入札ルールを削除する。削除成功でTrueを返す"""
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM bid_rules WHERE id=? AND clinic_id=?", (rule_id, clinic_id)
        )
        conn.commit()
        return cur.rowcount > 0

# ---- アラート ----
def create_alert(clinic_id: int, message: str, level: str = "INFO", campaign_id=None):
    with get_conn() as conn:
        conn.execute("INSERT INTO alerts (clinic_id,campaign_id,level,message) VALUES (?,?,?,?)",
                     (clinic_id, campaign_id, level, message))
        conn.commit()

def list_alerts(clinic_id: int, limit: int = 50):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM alerts WHERE clinic_id=? ORDER BY id DESC LIMIT ?",
            (clinic_id, limit)).fetchall()]

# ---- 監査ログ (Audit Logs) ----
def add_audit_log(clinic_id: int, user_email: str, action: str, entity: str = None, details: str = None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO audit_logs (clinic_id, user_email, action, entity, details) VALUES (?,?,?,?,?)",
            (clinic_id, user_email, action, entity, details)
        )
        conn.commit()

def list_audit_logs(clinic_id: int, limit: int = 100):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM audit_logs WHERE clinic_id=? ORDER BY id DESC LIMIT ?",
            (clinic_id, limit)
        ).fetchall()]

# ---- クリーンアップ (データ消去) ----
def cleanup_old_logs(days_retention: int = 365):
    with get_conn() as conn:
        if USE_PG:
            where_clause = f"created_at < NOW() - INTERVAL '{days_retention} days'"
            where_p_clause = f"date::date < CURRENT_DATE - INTERVAL '{days_retention} days'"
        else:
            where_clause = f"created_at < datetime('now', '-{days_retention} days', 'localtime')"
            where_p_clause = f"date < date('now', '-{days_retention} days', 'localtime')"

        cur_audit = conn.execute(f"DELETE FROM audit_logs WHERE {where_clause}")
        cur_perf = conn.execute(f"DELETE FROM performance_logs WHERE {where_p_clause}")
        cur_alerts = conn.execute(f"DELETE FROM alerts WHERE {where_clause}")

        conn.commit()
        return {
            "deleted_audit_logs": cur_audit.rowcount if hasattr(cur_audit, "rowcount") else 0,
            "deleted_performance_logs": cur_perf.rowcount if hasattr(cur_perf, "rowcount") else 0,
            "deleted_alerts": cur_alerts.rowcount if hasattr(cur_alerts, "rowcount") else 0
        }

# ---- AIクオータ管理 ----
def get_monthly_ai_usage(clinic_id: int) -> int:
    import datetime
    ym = datetime.datetime.now().strftime("%Y-%m")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT SUM(usage_count) as total FROM ai_usage_logs WHERE clinic_id=? AND year_month=?",
            (clinic_id, ym)
        ).fetchone()
        return row["total"] if row and row["total"] else 0

def get_ai_quota_limit(clinic_id: int) -> int:
    """クリニックAI利用上限を返す。契約プランから取得し、未設定の場合は30（デフォルト）。
    ai_quota_monthly = -1 の場合は無制限（パートナー向け等）。
    """
    contract = get_contract(clinic_id)
    if contract and contract.get("ai_quota_monthly") is not None:
        quota = int(contract["ai_quota_monthly"])
        return 999999 if quota == -1 else quota
    return 30  # デフォルト：標準プラン

def check_ai_quota_available(clinic_id: int, limit: int = 30) -> bool:
    """クリニックAIクォータが残っているか確認。プラン別上限を優先（limit引数は後方互換用）。"""
    quota = get_ai_quota_limit(clinic_id)
    count = get_monthly_ai_usage(clinic_id)
    return count < quota

def increment_ai_quota(clinic_id: int, feature_name: str):
    import datetime
    ym = datetime.datetime.now().strftime("%Y-%m")
    with get_conn() as conn:
        if USE_PG:
            conn.execute(
                """INSERT INTO ai_usage_logs (clinic_id, year_month, feature_name, usage_count) 
                   VALUES (?, ?, ?, 1) 
                   ON CONFLICT(clinic_id, year_month, feature_name) DO UPDATE 
                   SET usage_count = ai_usage_logs.usage_count + 1, last_used_at = NOW()""",
                (clinic_id, ym, feature_name)
            )
        else:
            conn.execute(
                """INSERT INTO ai_usage_logs (clinic_id, year_month, feature_name, usage_count) 
                   VALUES (?, ?, ?, 1) 
                   ON CONFLICT(clinic_id, year_month, feature_name) DO UPDATE 
                   SET usage_count = ai_usage_logs.usage_count + 1, last_used_at = datetime('now','localtime')""",
                (clinic_id, ym, feature_name)
            )
        conn.commit()

# ---- 広告文 ----
def save_ad_copy(clinic_id: int, data: dict) -> Optional[int]:
    with get_conn() as conn:
        cur = conn.execute("""INSERT INTO ad_copies
            (clinic_id,campaign_id,headlines,descriptions,prompt_context,status,variant_group)
            VALUES (?,?,?,?,?,?,?)""",
            (clinic_id, data.get("campaign_id"), data.get("headlines",""),
             data.get("descriptions",""), data.get("prompt_context",""),
             "draft", data.get("variant_group")))
        conn.commit()
        return cur.lastrowid

def update_ad_copy_score(copy_id: int, clinic_id: int, impressions: int, clicks: int):
    ctr = round(clicks / impressions, 4) if impressions > 0 else 0
    with get_conn() as conn:
        conn.execute(
            "UPDATE ad_copies SET impressions=?, clicks=?, ctr_score=? WHERE id=? AND clinic_id=?",
            (impressions, clicks, ctr, copy_id, clinic_id))
        conn.commit()

def retire_ad_copy(copy_id: int, clinic_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE ad_copies SET status='retired' WHERE id=? AND clinic_id=?",
                     (copy_id, clinic_id))
        conn.commit()

def list_ad_copies(clinic_id: int, campaign_id=None):
    with get_conn() as conn:
        if campaign_id:
            rows = conn.execute(
                "SELECT * FROM ad_copies WHERE clinic_id=? AND campaign_id=? ORDER BY id DESC",
                (clinic_id, campaign_id)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM ad_copies WHERE clinic_id=? ORDER BY id DESC LIMIT 20",
                (clinic_id,)).fetchall()
        return [dict(r) for r in rows]

# ---- お知らせ ----
def create_announcement(title: str, content: str):
    with get_conn() as conn:
        conn.execute("INSERT INTO announcements (title, content) VALUES (?, ?)", (title, content))
        conn.commit()

def list_announcements(limit: int = 5):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM announcements ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

def delete_announcement(announcement_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM announcements WHERE id=?", (announcement_id,))
        conn.commit()

# ---- 除外キーワード ----
def list_negative_keywords(clinic_id: int, campaign_id=None):
    with get_conn() as conn:
        if campaign_id:
            rows = conn.execute(
                "SELECT * FROM negative_keywords WHERE clinic_id=? AND campaign_id=? ORDER BY id DESC",
                (clinic_id, campaign_id)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM negative_keywords WHERE clinic_id=? ORDER BY id DESC",
                (clinic_id,)).fetchall()
        return [dict(r) for r in rows]

def add_negative_keyword(clinic_id: int, keyword: str, match_type: str = "BROAD",
                         campaign_id=None, source: str = "manual") -> Optional[int]:
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM negative_keywords WHERE clinic_id=? AND keyword=? AND COALESCE(campaign_id,-1)=COALESCE(?,-1)",
            (clinic_id, keyword, campaign_id)).fetchone()
        if existing:
            return existing["id"]
        cur = conn.execute(
            "INSERT INTO negative_keywords (clinic_id, campaign_id, keyword, match_type, source) VALUES (?,?,?,?,?)",
            (clinic_id, campaign_id, keyword, match_type, source))
        conn.commit()
        return cur.lastrowid

def delete_negative_keyword(nkw_id: int, clinic_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM negative_keywords WHERE id=? AND clinic_id=?", (nkw_id, clinic_id))
        conn.commit()


# ---- 契約管理（管理者専用） ----
def list_contracts():
    """全クリニックの契約情報を一覧取得"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT c.id as clinic_id, c.name as clinic_name, c.license_key,
                   co.id as contract_id, co.plan_name, co.monthly_fee,
                   co.started_at, co.renewal_at, co.status, co.notes, co.created_at
            FROM clinics c
            LEFT JOIN contracts co ON co.clinic_id = c.id
            ORDER BY c.id
        """).fetchall()
        return [dict(r) for r in rows]

def get_contract(clinic_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM contracts WHERE clinic_id=?", (clinic_id,)).fetchone()
        return dict(row) if row else None

def upsert_contract(clinic_id: int, data: dict):
    with get_conn() as conn:
        existing = conn.execute("SELECT id, stripe_customer_id FROM contracts WHERE clinic_id=?", (clinic_id,)).fetchone()
        stripe_cust = data.get("stripe_customer_id")
        if existing and existing["stripe_customer_id"] and not stripe_cust:
            stripe_cust = existing["stripe_customer_id"]

        if existing:
            conn.execute("""
                UPDATE contracts 
                SET stripe_customer_id=?, plan_name=?, monthly_fee=?, started_at=?, renewal_at=?,
                    status=?, notes=?
                WHERE clinic_id=?
            """, (stripe_cust, data.get("plan_name"), data.get("monthly_fee", 0),
                  data.get("started_at"), data.get("renewal_at"),
                  data.get("status", "active"), data.get("notes"), clinic_id))
        else:
            conn.execute("""
                INSERT INTO contracts (clinic_id, stripe_customer_id, plan_name, monthly_fee, started_at, renewal_at, status, notes)
                VALUES (?,?,?,?,?,?,?,?)
            """, (clinic_id, stripe_cust, data.get("plan_name", "スタンダード"), data.get("monthly_fee", 0),
                  data.get("started_at"), data.get("renewal_at"),
                  data.get("status", "active"), data.get("notes")))
        conn.commit()

def upsert_clinic(data: dict, requesting_clinic_id: int = None) -> int:
    """管理者がクリニックを追加・更新。requesting_clinic_idがある場合は上限チェックを行う"""
    with get_conn() as conn:
        clinic_id = data.get("id")
        if clinic_id:
            # 更新の場合はmax_sub_accountsも更新可能
            sets = []
            vals = []
            if data.get("name") is not None:
                sets.append("name=?")
                vals.append(data["name"])
            if data.get("max_sub_accounts") is not None:
                sets.append("max_sub_accounts=?")
                vals.append(int(data["max_sub_accounts"]))
            if sets:
                vals.append(clinic_id)
                conn.execute(f"UPDATE clinics SET {', '.join(sets)} WHERE id=?", vals)
        else:
            # 新規追加の場合：requesting_clinic_idのmax_sub_accountsを確認
            if requesting_clinic_id:
                parent = conn.execute(
                    "SELECT max_sub_accounts FROM clinics WHERE id=?", (requesting_clinic_id,)
                ).fetchone()
                max_allowed = parent["max_sub_accounts"] if parent else 1
                if max_allowed != -1:  # -1は無制限
                    # 同じ親クリニックのサブアカウント数を数える
                    current_count = conn.execute(
                        "SELECT COUNT(*) as n FROM clinics WHERE parent_clinic_id=?",
                        (requesting_clinic_id,)
                    ).fetchone()["n"]
                    if current_count >= max_allowed:
                        raise ValueError(f"アカウント追加上限（{max_allowed}件）に達しています")
            cur = conn.execute(
                "INSERT INTO clinics (name, license_key, parent_clinic_id) VALUES (?,?,?)",
                (data.get("name"),
                 data.get("license_key", f"KEY-{int(__import__('time').time())}"),
                 requesting_clinic_id)
            )
            clinic_id = cur.lastrowid
            # ads_accountsも自動作成
            conn.execute(
                "INSERT OR IGNORE INTO ads_accounts (clinic_id, customer_id, mock_mode) VALUES (?,?,1)",
                (clinic_id, "PENDING")
            )
        conn.commit()
        return clinic_id

def set_max_sub_accounts(clinic_id: int, max_accounts: int) -> None:
    """管理者がクリニックのサブアカウント上限を設定する（-1=無制限, 1=追加不可, N=N件まで）"""
    with get_conn() as conn:
        conn.execute("UPDATE clinics SET max_sub_accounts=? WHERE id=?", (max_accounts, clinic_id))
        conn.commit()

def get_admin_overview(start_date: str = None, end_date: str = None):
    """管理者ダッシュボード用：全クリニックの集計データ（詳細広告数値付き）"""
    with get_conn() as conn:
        clinics = conn.execute("SELECT id, name, max_sub_accounts, plan_status FROM clinics").fetchall()
        result = []
        for c in clinics:
            cid = c["id"]
            def _perf(where_sql, params=()):
                return conn.execute(f"""
                    SELECT SUM(cost_micros) as cost, SUM(conversions) as cv,
                           SUM(clicks) as clicks, SUM(impressions) as imps
                    FROM performance_logs WHERE clinic_id=?
                    AND {where_sql}
                """, (cid,) + tuple(params)).fetchone()

            if USE_PG:
                p7  = _perf("date::date >= CURRENT_DATE - INTERVAL '7 days'")
                p30 = _perf("date::date >= CURRENT_DATE - INTERVAL '30 days'")
                pm  = _perf("to_char(date::date, 'YYYY-MM') = to_char(NOW(), 'YYYY-MM')")
                p_ty = _perf("EXTRACT(YEAR FROM date::date) = EXTRACT(YEAR FROM NOW())")
                p_ly = _perf("EXTRACT(YEAR FROM date::date) = EXTRACT(YEAR FROM NOW()) - 1")
            else:
                p7  = _perf("date >= date('now','-7 days')")
                p30 = _perf("date >= date('now','-30 days')")
                pm  = _perf("strftime('%Y-%m', date) = strftime('%Y-%m', 'now')")
                p_ty = _perf("strftime('%Y', date) = strftime('%Y', 'now')")
                p_ly = _perf("strftime('%Y', date) = strftime('%Y', 'now', '-1 year')")
            p_custom = _perf("date >= ? AND date <= ?", (start_date, end_date)) if start_date and end_date else None

            active_cmp = conn.execute(
                "SELECT COUNT(*) as n FROM campaigns WHERE clinic_id=? AND status='ENABLED'", (cid,)
            ).fetchone()["n"]
            rule_count = conn.execute(
                "SELECT COUNT(*) as n FROM bid_rules WHERE clinic_id=? AND enabled=1", (cid,)
            ).fetchone()["n"]
            contract = conn.execute("SELECT * FROM contracts WHERE clinic_id=?", (cid,)).fetchone()
            sub_count = conn.execute(
                "SELECT COUNT(*) as n FROM clinics WHERE parent_clinic_id=?", (cid,)
            ).fetchone()["n"]
            max_sub = c["max_sub_accounts"] if c["max_sub_accounts"] is not None else 1

            def _kpi(row):
                if not row: return {}
                cost   = row["cost"] or 0
                clicks = row["clicks"] or 0
                imps   = row["imps"] or 0
                cv     = float(row["cv"] or 0)
                ctr    = round(clicks / imps * 100, 2) if imps else 0
                cvr    = round(cv / clicks * 100, 2) if clicks else 0
                cpc    = round(cost / clicks / 1e6, 0) if clicks else 0
                cpa    = round(cost / cv / 1e6, 0) if cv else 0
                return {
                    "cost_micros": cost, "clicks": clicks, "impressions": imps,
                    "conversions": round(cv, 1), "ctr": ctr, "cvr": cvr,
                    "cpc_yen": int(cpc), "cpa_yen": int(cpa),
                }

            result.append({
                "clinic_id": cid,
                "clinic_name": c["name"],
                "plan_name": contract["plan_name"] if contract else "未契約",
                "renewal_at": contract["renewal_at"] if contract else None,
                "status": c["plan_status"] if c["plan_status"] else (contract["status"] if contract else "inactive"),
                "max_sub_accounts": max_sub,
                "sub_accounts_used": sub_count,
                "sub_accounts_label": "無制限" if max_sub == -1 else f"{sub_count}/{max_sub}",
                "active_campaigns": active_cmp,
                "active_bid_rules": rule_count,
                "kpi_7d":    _kpi(p7),
                "kpi_30d":   _kpi(p30),
                "kpi_month": _kpi(pm),
                "kpi_this_year": _kpi(p_ty),
                "kpi_last_year": _kpi(p_ly),
                "kpi_custom": _kpi(p_custom) if p_custom else {},
            })
        return result



# ============================================================
# ユーザー認証 CRUD
# ============================================================

def create_user(clinic_id: int, email: str, password_hash: str, role: str = "user") -> int:
    """ユーザーを作成して IDを返す"""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (clinic_id, email, password_hash, role) VALUES (?,?,?,?)",
            (clinic_id, email.lower().strip(), password_hash, role)
        )
        conn.commit()
        return cur.lastrowid

def register_clinic_and_user(clinic_name: str, email: str, password_hash: str) -> dict:
    """サインアップ時: pending状態でクリニックとユーザーを作成"""
    import uuid
    with get_conn() as conn:
        # トランザクション
        license_key = "TMP-" + str(uuid.uuid4()).upper()[:16]
        cur = conn.execute(
            "INSERT INTO clinics (name, license_key, plan_status) VALUES (?,?,?)",
            (clinic_name, license_key, "pending")
        )
        clinic_id = cur.lastrowid
        
        conn.execute(
            "INSERT INTO users (clinic_id, email, password_hash, role) VALUES (?,?,?,?)",
            (clinic_id, email.lower().strip(), password_hash, "user")
        )
        conn.commit()
        return {"clinic_id": clinic_id, "email": email, "plan_status": "pending"}


def get_user_by_email(email: str) -> Optional[dict]:
    """メールアドレスでユーザーを取得"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email=? AND is_active=1",
            (email.lower().strip(),)
        ).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> Optional[dict]:
    """IDでユーザーを取得"""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def list_users(clinic_id: Optional[int] = None) -> list:
    """ユーザー一覧（管理者用）"""
    with get_conn() as conn:
        if clinic_id:
            rows = conn.execute(
                "SELECT u.*, c.name as clinic_name FROM users u JOIN clinics c ON u.clinic_id=c.id WHERE u.clinic_id=?",
                (clinic_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT u.*, c.name as clinic_name FROM users u JOIN clinics c ON u.clinic_id=c.id ORDER BY u.created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]


def update_user_password(user_id: int, password_hash: str) -> None:
    """パスワードを更新"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (password_hash, user_id)
        )
        conn.commit()

def update_user_last_login(user_id: int):
    with get_conn() as conn:
        now_sql = "NOW()" if USE_PG else "datetime('now','localtime')"
        conn.execute(f"UPDATE users SET last_login_at={now_sql} WHERE id=?", (user_id,))
        conn.commit()

# ---- パスワードリセット ----
def create_password_reset_token(user_id: int, token: str, expires_in_hours: int = 24):
    with get_conn() as conn:
        conn.execute("DELETE FROM password_reset_tokens WHERE user_id=?", (user_id,))
        expires_at = (datetime.now() + timedelta(hours=expires_in_hours)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO password_reset_tokens (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires_at)
        )
        conn.commit()

def verify_password_reset_token(token: str) -> Optional[int]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id, expires_at FROM password_reset_tokens WHERE token=?",
            (token,)
        ).fetchone()
        if not row:
            return None
        if row["expires_at"] < datetime.now().strftime("%Y-%m-%d %H:%M:%S"):
            conn.execute("DELETE FROM password_reset_tokens WHERE token=?", (token,))
            conn.commit()
            return None
        return row["user_id"]

def consume_password_reset_token(token: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM password_reset_tokens WHERE token=?", (token,))
        conn.commit()

def delete_user(user_id: int) -> None:
    """ユーザーを無効化（論理削除）"""
    with get_conn() as conn:
        conn.execute("UPDATE users SET is_active=0 WHERE id=?", (user_id,))
        conn.commit()


def get_clinic_plan_status(clinic_id: int) -> str:
    """クリニックのプランステータスを返す（active / suspended / cancelled）"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT plan_status FROM clinics WHERE id=?", (clinic_id,)
        ).fetchone()
        if not row:
            return "active"  # デフォルトはアクティブ
        return row["plan_status"] or "active"


def update_clinic_plan_status(clinic_id: int, status: str) -> None:
    """クリニックのプランステータスを更新（管理者専用）"""
    assert status in ("active", "suspended", "cancelled"), f"Invalid status: {status}"
    with get_conn() as conn:
        conn.execute(
            "UPDATE clinics SET plan_status=? WHERE id=?",
            (status, clinic_id)
        )
        conn.execute(
            "UPDATE contracts SET status=? WHERE clinic_id=?",
            (status, clinic_id)
        )
        conn.commit()

# =========================================================
# 広告詳細アーカイブ機能（多店舗展開・データ保管用）
# =========================================================

def archive_ad_strategy(clinic_id: int, campaigns: list, adgroups: list, ads: list, keywords: list, performance: dict, notes: str = "") -> int:
    """広告構成データ一式をJSONとしてアーカイブ保存する"""
    import json
    with get_conn() as conn:
        cursor = conn.execute("""
            INSERT INTO ad_strategy_archives
            (clinic_id, campaigns_json, adgroups_json, ads_json, keywords_json, performance_summary, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            clinic_id,
            json.dumps(campaigns, ensure_ascii=False),
            json.dumps(adgroups, ensure_ascii=False),
            json.dumps(ads, ensure_ascii=False),
            json.dumps(keywords, ensure_ascii=False),
            json.dumps(performance, ensure_ascii=False),
            notes
        ))
        conn.commit()
        return cursor.lastrowid

def get_ad_strategy_archives(clinic_id: int) -> list:
    """指定クリニックのアーカイブ履歴を取得"""
    import json
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, snapshot_date, notes, created_at, performance_summary
            FROM ad_strategy_archives
            WHERE clinic_id = ?
            ORDER BY created_at DESC
        """, (clinic_id,)).fetchall()
        
        res = []
        for r in rows:
            res.append({
                "id": r[0],
                "snapshot_date": r[1],
                "notes": r[2],
                "created_at": r[3],
                "performance_summary": json.loads(r[4] or "{}")
            })
        return res


# ---- Stripe 冪等性チェック ----
def is_stripe_event_processed(event_id: str) -> bool:
    """Stripe Webhookの同一イベントが既に処理済みかチェックする（2重課金防止）"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT event_id FROM stripe_processed_events WHERE event_id=?",
            (event_id,)
        ).fetchone()
        return row is not None


def mark_stripe_event_processed(event_id: str):
    """Stripe Webhookイベントを処理済みとしてDBに記録する"""
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO stripe_processed_events (event_id) VALUES (?)",
                (event_id,)
            )
            conn.commit()
        except Exception as e:
            print(f"[DB] stripe_processed_events 記録失敗: {e}")
