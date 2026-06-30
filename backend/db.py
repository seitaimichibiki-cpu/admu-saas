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
        if hasattr(self, "_is_closed") and self._is_closed:
            return
        self._is_closed = True
        get_pg_pool().putconn(self._conn)

    def cursor(self, **kwargs):
        return self._conn.cursor(**kwargs)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self._conn.rollback()
        else:
            self._conn.commit()
        self.close()
        return False


_pg_pool = None
def get_pg_pool():
    global _pg_pool
    if _pg_pool is None:
        from psycopg2.pool import SimpleConnectionPool
        _pg_pool = SimpleConnectionPool(1, 20, DATABASE_URL)
    return _pg_pool


def get_conn():
    if USE_PG:
        pool = get_pg_pool()
        pg_conn = pool.getconn()
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
            plan_status TEXT DEFAULT 'active',
            representative_name TEXT, email TEXT, address TEXT, line_uid TEXT,
            created_at {TS})""",
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
            budget_safety_brake_enabled INTEGER DEFAULT 1,
            ltv_conversion_action_id TEXT,
            logiction_integration_key TEXT,
            logiction_base_url TEXT,
            is_demo INTEGER DEFAULT 0,
            demo_expires_at TEXT,
            sitelink_price_url TEXT,
            sitelink_reviews_url TEXT,
            sitelink_reserve_url TEXT,
            line_harness_url TEXT,
            line_harness_api_key TEXT,
            line_harness_account_id TEXT,
            created_at {TS}, FOREIGN KEY (clinic_id) REFERENCES clinics(id))""",
        f"""CREATE TABLE IF NOT EXISTS campaigns (
            id {PK}, clinic_id INTEGER NOT NULL, google_campaign_id TEXT,
            name TEXT NOT NULL, status TEXT DEFAULT 'ENABLED',
            budget_micros BIGINT DEFAULT 0, budget_locked INTEGER DEFAULT 0,
            campaign_type TEXT DEFAULT 'SEARCH', target_region TEXT,
            youtube_video_id TEXT DEFAULT '',
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
        # オンボーディング離脱分析用
        f"""CREATE TABLE IF NOT EXISTS onboarding_progress (
            id {PK}, clinic_id INTEGER NOT NULL,
            step_reached INTEGER DEFAULT 1,
            step1_done INTEGER DEFAULT 0,
            step2_done INTEGER DEFAULT 0,
            step3_done INTEGER DEFAULT 0,
            step4_done INTEGER DEFAULT 0,
            step5_done INTEGER DEFAULT 0,
            completed INTEGER DEFAULT 0,
            gemini_set INTEGER DEFAULT 0,
            google_ads_set INTEGER DEFAULT 0,
            persona_set INTEGER DEFAULT 0,
            started_at {TS},
            completed_at TEXT,
            FOREIGN KEY (clinic_id) REFERENCES clinics(id))""",
        # LOGICTION患者データ連携テーブル
        f"""CREATE TABLE IF NOT EXISTS logiction_patients (
            id {PK},
            clinic_id INTEGER NOT NULL,
            patient_id TEXT NOT NULL,
            gender TEXT,
            age INTEGER,
            age_group TEXT,
            address_pref TEXT,
            address_city TEXT,
            symptoms TEXT,
            visit_count INTEGER DEFAULT 0,
            total_revenue INTEGER DEFAULT 0,
            ltv_yen INTEGER DEFAULT 0,
            acquisition_channel TEXT,
            gclid TEXT,
            first_visit_date TEXT,
            synced_at {TS},
            FOREIGN KEY (clinic_id) REFERENCES clinics(id),
            UNIQUE(clinic_id, patient_id))""",
        f"""CREATE TABLE IF NOT EXISTS logiction_sync_log (
            id {PK},
            clinic_id INTEGER NOT NULL,
            synced_count INTEGER DEFAULT 0,
            updated_count INTEGER DEFAULT 0,
            sync_source TEXT DEFAULT 'api',
            synced_at {TS},
            FOREIGN KEY (clinic_id) REFERENCES clinics(id))""",
        # キャンペーン永久ブラックリスト（削除後に再同期されても無視するため）
        f"""CREATE TABLE IF NOT EXISTS campaign_blacklist (
            id {PK},
            clinic_id INTEGER NOT NULL,
            google_campaign_id TEXT NOT NULL,
            campaign_name TEXT,
            reason TEXT DEFAULT 'user_deleted',
            created_at {TS},
            UNIQUE(clinic_id, google_campaign_id))""",
    ]
    for ddl in tables:
        conn.execute(ddl)
    conn.commit()

    # ── カラム追加マイグレーション（既存DB対応）──
    # 全マイグレーションをSAVEPOINTで保護して実行（PostgreSQLでトランザクションが壊れない）
    migrations = [
        # --- 早期追加分（以前は別ブロックで実行していたがSAVEPOINT保護ブロックに統合）---
        "ALTER TABLE ads_accounts ADD COLUMN logiction_integration_key TEXT",
        "ALTER TABLE ads_accounts ADD COLUMN logiction_base_url TEXT",
        "ALTER TABLE logiction_patients ADD COLUMN IF NOT EXISTS synced_at TIMESTAMPTZ DEFAULT NOW()",
        # --- 既存マイグレーション ---
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
        # Yahoo関連カラムは削除済み（旧DBの互換性のためマイグレーション履歴は残すが新規作成時には使用不可）
        # contractsテーブル: AI利用上限（プラン別設定用、-1=無制限）
        "ALTER TABLE contracts ADD COLUMN ai_quota_monthly INTEGER DEFAULT 30",
        # 顧客自身のGemini APIキー（BYOK: Bring Your Own Key）
        "ALTER TABLE ads_accounts ADD COLUMN gemini_api_key TEXT",
        # 月間AI呼び出し上限（0=AI機能無効, -1=無制限）
        "ALTER TABLE ads_accounts ADD COLUMN ai_monthly_limit INTEGER DEFAULT 0",
        # オンボーディング進捗カラム追加（既存DBへの追加）
        "ALTER TABLE onboarding_progress ADD COLUMN gemini_set INTEGER DEFAULT 0",
        "ALTER TABLE onboarding_progress ADD COLUMN google_ads_set INTEGER DEFAULT 0",
        "ALTER TABLE onboarding_progress ADD COLUMN persona_set INTEGER DEFAULT 0",
        # 利用規約同意ログ（外販対応・法的クレーム防止）
        "ALTER TABLE users ADD COLUMN accepted_terms_at TEXT DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN terms_version TEXT DEFAULT NULL",
        # 支払い失敗カウンター（猶予期間管理）
        "ALTER TABLE clinics ADD COLUMN payment_failed_count INTEGER DEFAULT 0",
        "ALTER TABLE clinics ADD COLUMN payment_grace_until TEXT DEFAULT NULL",
        # Google Ads アクセス権リンクステータス
        "ALTER TABLE ads_accounts ADD COLUMN google_link_status TEXT DEFAULT NULL",
        "ALTER TABLE ads_accounts ADD COLUMN google_link_requested_at TEXT DEFAULT NULL",
        # 自動セーフティブレーキおよびLTVコンバージョン同期用のカラム
        "ALTER TABLE ads_accounts ADD COLUMN budget_safety_brake_enabled INTEGER DEFAULT 1",
        "ALTER TABLE ads_accounts ADD COLUMN ltv_conversion_action_id TEXT",
        "ALTER TABLE ads_accounts ADD COLUMN is_demo INTEGER DEFAULT 0",
        "ALTER TABLE ads_accounts ADD COLUMN demo_expires_at TEXT",
        "ALTER TABLE clinics ADD COLUMN representative_name TEXT",
        "ALTER TABLE clinics ADD COLUMN email TEXT",
        "ALTER TABLE clinics ADD COLUMN address TEXT",
        "ALTER TABLE clinics ADD COLUMN line_uid TEXT",
        "ALTER TABLE ads_accounts ADD COLUMN sitelink_price_url TEXT DEFAULT NULL",
        "ALTER TABLE ads_accounts ADD COLUMN sitelink_reviews_url TEXT DEFAULT NULL",
        "ALTER TABLE ads_accounts ADD COLUMN sitelink_reserve_url TEXT DEFAULT NULL",
        "ALTER TABLE ads_accounts ADD COLUMN line_harness_url TEXT DEFAULT NULL",
        "ALTER TABLE ads_accounts ADD COLUMN line_harness_api_key TEXT DEFAULT NULL",
        "ALTER TABLE ads_accounts ADD COLUMN line_harness_account_id TEXT DEFAULT NULL",
        # YouTube広告編集内容のDB永続化（GAQL取得失敗時の復元用）
        "ALTER TABLE campaigns ADD COLUMN youtube_video_id TEXT DEFAULT ''",
        "ALTER TABLE campaigns ADD COLUMN ad_content_json TEXT DEFAULT ''",
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

    # 初期データが存在しなければ作成（ID:1となる）
    has_clinics = conn.execute("SELECT id FROM clinics LIMIT 1").fetchone()
    if not has_clinics:
        conn.execute("INSERT INTO clinics (name, license_key, plan_status) VALUES ('システム管理者', 'DEMO-0000-0000-0000', 'active')")
        conn.commit()
        demo = conn.execute("SELECT id FROM clinics LIMIT 1").fetchone()
        clinic_id = demo["id"]
        conn.execute("INSERT INTO ads_accounts (clinic_id, customer_id, mock_mode) VALUES (?, 'DEMO-CUSTOMER-ID', 1)", (clinic_id,))
        conn.commit()

    # 管理者ユーザーが存在しなければ作成
    has_admin = conn.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
    if not has_admin:
        import auth
        admin_pass_hash = auth.hash_password("gai1124714")
        demo = conn.execute("SELECT id FROM clinics ORDER BY id ASC LIMIT 1").fetchone()
        if demo:
            clinic_id = demo[0] if isinstance(demo, tuple) else demo["id"]
            has_user = conn.execute("SELECT id FROM users WHERE email='seitaimichibiki@gmail.com'").fetchone()
            if not has_user:
                conn.execute(
                    "INSERT INTO users (clinic_id, email, password_hash, role) VALUES (?, ?, ?, 'admin')",
                    (clinic_id, "seitaimichibiki@gmail.com", admin_pass_hash)
                )
            else:
                conn.execute(
                    "UPDATE users SET role='admin', password_hash=?, clinic_id=? WHERE email='seitaimichibiki@gmail.com'",
                    (admin_pass_hash, clinic_id)
                )
            conn.commit()
            conn.execute("UPDATE clinics SET plan_status='active' WHERE id=?", (clinic_id,))
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
        SECRET_FIELDS = ["developer_token", "client_secret", "refresh_token", "line_channel_token", "ga4_api_secret", "smtp_pass", "gemini_api_key", "line_harness_api_key"]
        for field in SECRET_FIELDS:
            if data.get(field):
                try:
                    data[field] = crypto_utils.decrypt(data[field])
                except Exception:
                    pass  # 復号失敗時はそのまま（平文で保存されている場合の後方互換）
        return data

def save_ads_account(clinic_id: int, data: dict):
    import crypto_utils
    secure_data = dict(data)

    # IDフィールドのハイフン・スペースを除去して数字のみに正規化
    for id_field in ["customer_id", "login_customer_id"]:
        if id_field in secure_data and secure_data[id_field]:
            secure_data[id_field] = str(secure_data[id_field]).replace("-", "").replace(" ", "").strip()

    SECRET_FIELDS = ["developer_token", "client_secret", "refresh_token", "line_channel_token", "ga4_api_secret", "smtp_pass", "yahoo_client_secret", "yahoo_refresh_token", "gemini_api_key", "line_harness_api_key"]
    for field in SECRET_FIELDS:
        if field in secure_data and secure_data[field]:
            secure_data[field] = crypto_utils.encrypt(secure_data[field])

    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM ads_accounts WHERE clinic_id=?", (clinic_id,)).fetchone()
        fields = ["customer_id", "developer_token", "client_id", "client_secret", "refresh_token",
                  "login_customer_id", "mock_mode", "line_channel_token", "line_user_id",
                  "target_age_gender", "target_job_lifestyle", "target_pain_point", "target_desired_outcome",
                  "notification_email", "smtp_user", "smtp_pass", "ga4_property_id", "ga4_api_secret",
                  "monthly_budget_yen", "budget_safety_brake_enabled", "ltv_conversion_action_id",
                  "gemini_api_key", "ai_monthly_limit",
                  "google_link_status", "google_link_requested_at",
                  "logiction_integration_key", "logiction_base_url",
                  "is_demo", "demo_expires_at",
                  "sitelink_price_url", "sitelink_reviews_url", "sitelink_reserve_url",
                  "line_harness_url", "line_harness_api_key", "line_harness_account_id"]
        if existing:
            sets = ", ".join(f"{f}=?" for f in fields if f in secure_data)
            vals = [secure_data[f] for f in fields if f in secure_data] + [clinic_id]
            if sets:
                conn.execute(f"UPDATE ads_accounts SET {sets} WHERE clinic_id=?", vals)
        else:
            # 新規レコードを作成してから全フィールドをUPDATE
            conn.execute(
                "INSERT INTO ads_accounts (clinic_id, customer_id, mock_mode) VALUES (?, ?, ?)",
                (clinic_id, secure_data.get("customer_id", ""), secure_data.get("mock_mode", 1))
            )
            # INSERT直後に残りの全フィールドもUPDATE
            remaining_fields = [f for f in fields if f in secure_data and f not in ("customer_id", "mock_mode")]
            if remaining_fields:
                sets = ", ".join(f"{f}=?" for f in remaining_fields)
                vals = [secure_data[f] for f in remaining_fields] + [clinic_id]
                conn.execute(f"UPDATE ads_accounts SET {sets} WHERE clinic_id=?", vals)
        conn.commit()

def get_gemini_api_key(clinic_id: int) -> str:
    """クリニック自身のGemini APIキーを取得（DB優先→環境変数フォールバック）"""
    account = get_ads_account(clinic_id)
    if account and account.get("gemini_api_key"):
        return account["gemini_api_key"]
    # フォールバック: システム管理者共有キー（石川さんのキー）
    return os.environ.get("GEMINI_API_KEY", "")


def check_ai_limit(clinic_id: int) -> tuple[bool, str]:
    """AI呼び出しが可能かチェック。(ok: bool, reason: str) を返す。"""
    account = get_ads_account(clinic_id)
    if not account:
        return False, "アカウント情報が見つかりません"

    has_global_key = bool(os.environ.get("GEMINI_API_KEY", ""))
    has_tenant_key = bool(account.get("gemini_api_key", ""))

    if not (has_global_key or has_tenant_key):
        return False, "AI機能が無効です。管理者にGemini APIキーの設定を依頼するか、環境変数を確認してください。"

    limit = account.get("ai_monthly_limit", 0)

    # 1. 管理者（clinic_id=1）は常に制限なしで利用可能とする
    if clinic_id == 1:
        return True, ""

    # 2. 制限値が 0 であってもシステム共通キーが有効なら、デフォルトの上限値（30回）を割り当てる
    if limit == 0:
        limit = 30

    if limit == -1:
        return True, ""  # 無制限

    # 今月の使用回数チェック
    from datetime import datetime
    ym = datetime.now().strftime("%Y-%m")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT usage_count FROM ai_usage_logs WHERE clinic_id=? AND year_month=? AND feature_name='gemini'",
            (clinic_id, ym)
        ).fetchone()
    count = row["usage_count"] if row else 0
    if count >= limit:
        return False, f"今月のAI利用上限（{limit}回）に達しました。設定から上限を変更するか、来月までお待ちください。"
    return True, ""


def increment_ai_usage(clinic_id: int):
    """AI機能を1回使用したときに呼び出すカウンター"""
    from datetime import datetime
    ym = datetime.now().strftime("%Y-%m")
    with get_conn() as conn:
        if USE_PG:
            conn.execute("""
                INSERT INTO ai_usage_logs (clinic_id, year_month, feature_name, usage_count)
                VALUES (%s, %s, 'gemini', 1)
                ON CONFLICT (clinic_id, year_month, feature_name)
                DO UPDATE SET usage_count = ai_usage_logs.usage_count + 1
            """, (clinic_id, ym))
        else:
            conn.execute("""
                INSERT INTO ai_usage_logs (clinic_id, year_month, feature_name, usage_count)
                VALUES (?, ?, 'gemini', 1)
                ON CONFLICT (clinic_id, year_month, feature_name)
                DO UPDATE SET usage_count = usage_count + 1
            """, (clinic_id, ym))
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
        # NOTE: youtube_video_id, ad_content_jsonカラムはinit_db()のマイグレーションで保証済み
        if data.get("id"):
            conn.execute("""
                UPDATE campaigns SET name=?,status=?,budget_micros=?,target_region=?,updated_at=?
                WHERE id=? AND clinic_id=?
            """, (data["name"], data.get("status","ENABLED"), data.get("budget_micros",0), data.get("target_region",""),
                  datetime.now().strftime("%Y-%m-%d %H:%M:%S"), data["id"], clinic_id))
            conn.commit()
            return data["id"]
        else:
            cur = conn.execute("""
                INSERT INTO campaigns (clinic_id,name,status,budget_micros,campaign_type,target_region,google_campaign_id,youtube_video_id)
                VALUES (?,?,?,?,?,?,?,?)
            """, (clinic_id, data["name"], data.get("status","ENABLED"),
                  data.get("budget_micros",0), data.get("campaign_type","SEARCH"),
                  data.get("target_region",""), data.get("google_campaign_id",""),
                  data.get("youtube_video_id","")))
            conn.commit()
            return cur.lastrowid


def save_youtube_ad_content(clinic_id: int, google_campaign_id: str, content: dict):
    """YouTube広告の編集内容（見出し・説明文等）をDBに保存（次回復元用）"""
    import json
    with get_conn() as conn:
        conn.execute("""
            UPDATE campaigns SET ad_content_json=?, updated_at=?
            WHERE clinic_id=? AND google_campaign_id=?
        """, (json.dumps(content, ensure_ascii=False),
              datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              clinic_id, str(google_campaign_id)))
        conn.commit()


def get_youtube_ad_content(clinic_id: int, google_campaign_id: str) -> dict:
    """DBに保存されたYouTube広告内容を返す"""
    import json
    with get_conn() as conn:
        try:
            row = conn.execute(
                "SELECT ad_content_json FROM campaigns WHERE clinic_id=? AND google_campaign_id=?",
                (clinic_id, str(google_campaign_id))
            ).fetchone()
            if row and row[0]:
                try:
                    return json.loads(row[0])
                except Exception:
                    pass
        except Exception:
            pass
    return {}

# ---- キャンペーン永久ブラックリスト ----
def add_campaign_blacklist(clinic_id: int, google_campaign_id: str, campaign_name: str = None, reason: str = "user_deleted"):
    """削除されたキャンペーンをDBに永続的に記録し、Google Ads APIからの再同期を防止する"""
    if not google_campaign_id:
        return
    with get_conn() as conn:
        try:
            if USE_PG:
                conn.execute("""
                    INSERT INTO campaign_blacklist (clinic_id, google_campaign_id, campaign_name, reason)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (clinic_id, google_campaign_id) DO NOTHING
                """, (clinic_id, str(google_campaign_id), campaign_name, reason))
            else:
                conn.execute("""
                    INSERT OR IGNORE INTO campaign_blacklist (clinic_id, google_campaign_id, campaign_name, reason)
                    VALUES (?, ?, ?, ?)
                """, (clinic_id, str(google_campaign_id), campaign_name, reason))
            conn.commit()
        except Exception as e:
            print(f"[DB] campaign_blacklist insert error: {e}")

def get_campaign_blacklist(clinic_id: int) -> set:
    """ブラックリストに登録された google_campaign_id のセットを返す"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT google_campaign_id FROM campaign_blacklist WHERE clinic_id=?",
            (clinic_id,)
        ).fetchall()
        return {r["google_campaign_id"] for r in rows}



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
    ctr = round(float(clicks) / float(impressions), 4) if impressions > 0 else 0
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
            if "representative_name" in data and data["representative_name"] is not None:
                sets.append("representative_name=?")
                vals.append(data["representative_name"])
            if "email" in data and data["email"] is not None:
                sets.append("email=?")
                vals.append(data["email"])
            if "address" in data and data["address"] is not None:
                sets.append("address=?")
                vals.append(data["address"])
            if "line_uid" in data and data["line_uid"] is not None:
                sets.append("line_uid=?")
                vals.append(data["line_uid"])
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
                """INSERT INTO clinics 
                   (name, license_key, parent_clinic_id, representative_name, email, address, line_uid) 
                   VALUES (?,?,?,?,?,?,?)""",
                (data.get("name"),
                 data.get("license_key", f"KEY-{int(__import__('time').time())}"),
                 requesting_clinic_id,
                 data.get("representative_name"),
                 data.get("email"),
                 data.get("address"),
                 data.get("line_uid"))
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
        clinics = conn.execute("SELECT id, name, max_sub_accounts, plan_status, representative_name, email, address, line_uid FROM clinics").fetchall()
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
                p7  = _perf("date >= to_char(CURRENT_DATE - INTERVAL '7 days', 'YYYY-MM-DD')")
                p30 = _perf("date >= to_char(CURRENT_DATE - INTERVAL '30 days', 'YYYY-MM-DD')")
                pm  = _perf("substring(date from 1 for 7) = to_char(NOW(), 'YYYY-MM')")
                p_ty = _perf("substring(date from 1 for 4) = to_char(NOW(), 'YYYY')")
                p_ly = _perf("substring(date from 1 for 4) = to_char(NOW() - INTERVAL '1 year', 'YYYY')")
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
                cost   = float(row["cost"] or 0)
                clicks = int(row["clicks"] or 0)
                imps   = int(row["imps"] or 0)
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
                "representative_name": c["representative_name"],
                "email": c["email"],
                "address": c["address"],
                "line_uid": c["line_uid"],
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

CURRENT_TERMS_VERSION = "2026-05-25-v1"

def create_user(clinic_id: int, email: str, password_hash: str, role: str = "user", accepted_terms: bool = False) -> int:
    """ユーザーを作成してIDを返す。accepted_terms=Trueで利用規約同意を記録"""
    now = datetime.now().isoformat()
    terms_at = now if accepted_terms else None
    terms_ver = CURRENT_TERMS_VERSION if accepted_terms else None
    with get_conn() as conn:
        if USE_PG:
            cur = conn.execute(
                "INSERT INTO users (clinic_id, email, password_hash, role, accepted_terms_at, terms_version) VALUES (%s,%s,%s,%s,%s,%s)",
                (clinic_id, email.lower().strip(), password_hash, role, terms_at, terms_ver)
            )
        else:
            cur = conn.execute(
                "INSERT INTO users (clinic_id, email, password_hash, role, accepted_terms_at, terms_version) VALUES (?,?,?,?,?,?)",
                (clinic_id, email.lower().strip(), password_hash, role, terms_at, terms_ver)
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
        
        now = datetime.now().isoformat()
        if USE_PG:
            conn.execute(
                "INSERT INTO users (clinic_id, email, password_hash, role, accepted_terms_at, terms_version) VALUES (%s,%s,%s,%s,%s,%s)",
                (clinic_id, email.lower().strip(), password_hash, "user", now, CURRENT_TERMS_VERSION)
            )
        else:
            conn.execute(
                "INSERT INTO users (clinic_id, email, password_hash, role, accepted_terms_at, terms_version) VALUES (?,?,?,?,?,?)",
                (clinic_id, email.lower().strip(), password_hash, "user", now, CURRENT_TERMS_VERSION)
            )
        conn.commit()
        return {"clinic_id": clinic_id, "email": email, "plan_status": "pending", "terms_accepted_at": now}


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


def create_demo_account(clinic_name: str, email: str, password_hash: str, demo_expires_at: Optional[str] = None) -> dict:
    """デモ用のクリニック、デモユーザー、デモ設定、およびダミーデータを生成する"""
    import json
    import random
    from datetime import datetime, timedelta

    with get_conn() as conn:
        # 1. クリニックの作成
        cur = conn.execute(
            "INSERT INTO clinics (name, plan_status) VALUES (?, 'active')",
            (clinic_name,)
        )
        clinic_id = cur.lastrowid

        # 2. デモユーザーの作成
        u_res = conn.execute(
            "INSERT INTO users (clinic_id, email, password_hash, role) VALUES (?, ?, ?, 'user')",
            (clinic_id, email, password_hash)
        )
        user_id = u_res.lastrowid

        # 3. デモ用 ads_accounts の作成 (mock_mode=1, is_demo=1)
        conn.execute("""
            INSERT INTO ads_accounts (
                clinic_id, customer_id, mock_mode, is_demo, monthly_budget_yen,
                target_age_gender, target_job_lifestyle, target_pain_point, target_desired_outcome,
                line_channel_token, line_user_id, notification_email, gemini_api_key, demo_expires_at
            ) VALUES (?, 'DEMO-999-999-9999', 1, 1, 150000, 
                      '30代〜50代の男女、主婦、デスクワーカー', 
                      '立ち仕事が多い、長時間の運転、パソコン作業が多い',
                      '慢性的な腰痛、肩こりでどこに行っても治らない。手術を勧められている。',
                      '痛みから解放されて、趣味の旅行やスポーツを全力で楽しみたい。',
                      'mock_line_token_12345', 'mock_line_user_67890', 'demo_notify@admu.jp', 'mock_gemini_key_abcde', ?)
        """, (clinic_id, demo_expires_at))

        # 4. オンボーディング進捗を完了（スキップ済み）として登録
        conn.execute("""
            INSERT INTO onboarding_progress (
                clinic_id, step_reached, step1_done, step2_done, step3_done, step4_done, step5_done, completed,
                gemini_set, google_ads_set, persona_set
            ) VALUES (?, 6, 1, 1, 1, 1, 1, 1, 1, 1, 1)
        """, (clinic_id,))

        # 5. ダミーキャンペーンの作成 (2件)
        c1_res = conn.execute("""
            INSERT INTO campaigns (clinic_id, google_campaign_id, name, status, budget_micros, campaign_type, target_region)
            VALUES (?, '1111111111', 'デモ_検索_渋谷駅前腰痛専門', 'ENABLED', 3000000000, 'SEARCH', '東京都渋谷区')
        """, (clinic_id,))
        c1_id = c1_res.lastrowid

        c2_res = conn.execute("""
            INSERT INTO campaigns (clinic_id, google_campaign_id, name, status, budget_micros, campaign_type, target_region)
            VALUES (?, '2222222222', 'デモ_検索_渋谷産後骨盤矯正', 'ENABLED', 2000000000, 'SEARCH', '東京都渋谷区')
        """, (clinic_id,))
        c2_id = c2_res.lastrowid

        # 6. ダミーパフォーマンスログの作成 (直近7日間分)
        now = datetime.now()
        for i in range(7):
            d = (now - timedelta(days=7-i)).strftime("%Y-%m-%d")
            # キャンペーン1 (腰痛)
            c1_imp = random.randint(150, 300)
            c1_click = random.randint(15, 30)
            c1_ctr = (c1_click / c1_imp) * 100
            c1_cost = c1_click * random.randint(150, 220) * 1000000 # micros
            c1_conv = random.randint(1, 3) if random.random() > 0.3 else 0
            c1_cvr = (c1_conv / c1_click) * 100 if c1_click > 0 else 0
            conn.execute("""
                INSERT INTO performance_logs
                (clinic_id, campaign_id, date, impressions, clicks, ctr, avg_cpc_micros, cost_micros, conversions, cvr)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (clinic_id, c1_id, d, c1_imp, c1_click, c1_ctr, int(c1_cost / c1_click) if c1_click > 0 else 0, c1_cost, c1_conv, c1_cvr))

            # キャンペーン2 (骨盤矯正)
            c2_imp = random.randint(100, 200)
            c2_click = random.randint(8, 18)
            c2_ctr = (c2_click / c2_imp) * 100
            c2_cost = c2_click * random.randint(120, 180) * 1000000 # micros
            c2_conv = random.randint(1, 2) if random.random() > 0.5 else 0
            c2_cvr = (c2_conv / c2_click) * 100 if c2_click > 0 else 0
            conn.execute("""
                INSERT INTO performance_logs
                (clinic_id, campaign_id, date, impressions, clicks, ctr, avg_cpc_micros, cost_micros, conversions, cvr)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (clinic_id, c2_id, d, c2_imp, c2_click, c2_ctr, int(c2_cost / c2_click) if c2_click > 0 else 0, c2_cost, c2_conv, c2_cvr))

        # 7. ダミーのアラートログの作成
        conn.execute("""
            INSERT INTO alerts (clinic_id, campaign_id, level, message, notified)
            VALUES (?, ?, 'INFO', ?, 1)
        """, (clinic_id, c1_id, '【AI入札】キャンペーン「デモ_検索_渋谷駅前腰痛専門」の入札単価を自動調整しました（CVR良好のため+10%）。'))
        conn.execute("""
            INSERT INTO alerts (clinic_id, campaign_id, level, message, notified)
            VALUES (?, ?, 'INFO', ?, 1)
        """, (clinic_id, c1_id, '【LTV同期】Logictionから最新 of LTVデータ（2件、総額12万円）をGoogle広告へ自動Push同期しました。'))
        conn.execute("""
            INSERT INTO alerts (clinic_id, campaign_id, level, message, notified)
            VALUES (?, ?, 'INFO', ?, 1)
        """, (clinic_id, c1_id, '【無駄除外】来院患者のいない遠方エリア（千葉県浦安市）を無駄エリアとして自動除外登録しました。'))

        conn.commit()

        return {"clinic_id": clinic_id, "user_id": user_id, "email": email}


def delete_clinic(clinic_id: int) -> None:
    """クリニックとそれに関連するすべてのデータを安全に削除する"""
    if clinic_id == 1:
        raise ValueError("システム管理者は削除できません")
    
    with get_conn() as conn:
        # まずクリニック名を確認して「システム管理者」であれば削除不可
        c = conn.execute("SELECT name FROM clinics WHERE id=?", (clinic_id,)).fetchone()
        if c and c["name"] == "システム管理者":
            raise ValueError("システム管理者は削除できません")

        # 関連データの削除
        tables = [
            "password_reset_tokens WHERE user_id IN (SELECT id FROM users WHERE clinic_id=?)",
            "users WHERE clinic_id=?",
            "contracts WHERE clinic_id=?",
            "campaign_personas WHERE clinic_id=?",
            "campaign_blacklist WHERE clinic_id=?",
            "invitations WHERE clinic_id=?",
            "logiction_sync_log WHERE clinic_id=?",
            "logiction_patients WHERE clinic_id=?",
            "onboarding_progress WHERE clinic_id=?",
            "ai_usage_logs WHERE clinic_id=?",
            "audit_logs WHERE clinic_id=?",
            "negative_keywords WHERE clinic_id=?",
            "ad_copies WHERE clinic_id=?",
            "alerts WHERE clinic_id=?",
            "bid_rules WHERE clinic_id=?",
            "performance_logs WHERE clinic_id=?",
            "ad_strategy_archives WHERE clinic_id=?",
            "campaigns WHERE clinic_id=?",
            "ads_accounts WHERE clinic_id=?",
            "personas WHERE clinic_id=?",
        ]
        
        for t_spec in tables:
            try:
                if USE_PG:
                    conn.execute("SAVEPOINT del_sp")
                conn.execute(f"DELETE FROM {t_spec}", (clinic_id,))
                if USE_PG:
                    conn.execute("RELEASE SAVEPOINT del_sp")
            except Exception as e:
                if USE_PG:
                    conn.execute("ROLLBACK TO SAVEPOINT del_sp")
                # 存在しないテーブル等のエラーは無視
                print(f"[delete_clinic] Skip table delete error: {e}")
                pass

        # 最後に親である clinics 内の自分を削除
        conn.execute("DELETE FROM clinics WHERE id=?", (clinic_id,))
        conn.commit()


