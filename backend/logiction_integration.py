"""
logiction_integration.py
LOGICTION患者データ → AdMu ペルソナ分析 → Google Ads入札最適化
"""
import os
import json
from typing import Optional
from fastapi import HTTPException, Request
from pydantic import BaseModel


# ============================================================
# Pydantic モデル
# ============================================================
class LogictionPatient(BaseModel):
    patient_id: str
    gender: Optional[str] = None          # "male" / "female"
    age: Optional[int] = None
    address_pref: Optional[str] = None    # 都道府県
    address_city: Optional[str] = None    # 市区町村
    symptoms: Optional[list] = []         # ["腰痛", "肩こり"]
    visit_count: int = 0
    total_revenue: int = 0                # 総売上（円）
    ltv_yen: Optional[int] = None         # 明示的LTV（未指定時は自動計算）
    acquisition_channel: Optional[str] = None  # "Google広告","Instagram","紹介"等
    gclid: Optional[str] = None
    first_visit_date: Optional[str] = None

class LogictionSyncReq(BaseModel):
    clinic_id: int = 1
    patients: list[LogictionPatient]
    secret_key: Optional[str] = None


# ============================================================
# ユーティリティ
# ============================================================
def _age_to_group(age: Optional[int]) -> str:
    """年齢をGoogle Ads対応のターゲティンググループに変換"""
    if age is None: return "不明"
    if age < 25: return "18-24歳"
    if age < 35: return "25-34歳"
    if age < 45: return "35-44歳"
    if age < 55: return "45-54歳"
    if age < 65: return "55-64歳"
    return "65歳以上"


def _auto_update_persona_from_patients(clinic_id: int, db):
    """
    蓄積した患者データからads_accountsのペルソナフィールドを自動更新。
    来院者の実態（高LTV層）に基づいてAI広告精度を向上させる。
    """
    with db.get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) as c FROM logiction_patients WHERE clinic_id=?",
            (clinic_id,)
        ).fetchone()["c"]
        if total < 3:
            return

        top_gender = conn.execute("""
            SELECT gender, COUNT(*) as c FROM logiction_patients
            WHERE clinic_id=? AND gender IS NOT NULL
            GROUP BY gender ORDER BY c DESC LIMIT 1
        """, (clinic_id,)).fetchone()

        top_age = conn.execute("""
            SELECT age_group, AVG(ltv_yen) as avg_ltv FROM logiction_patients
            WHERE clinic_id=? AND age_group != '不明'
            GROUP BY age_group ORDER BY avg_ltv DESC LIMIT 1
        """, (clinic_id,)).fetchone()

        all_symptoms = conn.execute(
            "SELECT symptoms FROM logiction_patients WHERE clinic_id=?",
            (clinic_id,)
        ).fetchall()
        symptom_count = {}
        for row in all_symptoms:
            try:
                for s in json.loads(row["symptoms"] or "[]"):
                    symptom_count[s] = symptom_count.get(s, 0) + 1
            except Exception:
                pass
        top_symptoms = sorted(symptom_count.items(), key=lambda x: x[1], reverse=True)[:3]
        top_symptoms_str = "・".join([s[0] for s in top_symptoms]) if top_symptoms else ""

        top_area = conn.execute("""
            SELECT address_pref, COUNT(*) as c FROM logiction_patients
            WHERE clinic_id=? AND address_pref IS NOT NULL
            GROUP BY address_pref ORDER BY c DESC LIMIT 1
        """, (clinic_id,)).fetchone()

    gender_label = {"male": "男性", "female": "女性"}.get(
        (top_gender["gender"] if top_gender else ""), ""
    )
    age_label = top_age["age_group"] if top_age else ""
    area_label = top_area["address_pref"] if top_area else ""

    acc = db.get_ads_account(clinic_id)
    if acc:
        updates = {}
        if age_label or gender_label:
            updates["target_age_gender"] = f"{age_label} {gender_label}".strip() + "（来院データから自動設定）"
        if top_symptoms_str:
            updates["target_pain_point"] = f"多い症状: {top_symptoms_str}"
        if area_label:
            updates["target_job_lifestyle"] = f"主要エリア: {area_label}"
        if updates:
            db.save_ads_account(clinic_id, {**acc, **updates})


# ============================================================
# API ハンドラ（main.pyから呼び出す）
# ============================================================
async def handle_patient_sync(req: LogictionSyncReq, request: Request, db, integration_bridge):
    """LOGICTION → AdMu 患者データ同期"""
    # 認証: DB優先（clinic_idごとのキー）→ 環境変数フォールバック
    provided_key = req.secret_key or request.headers.get("X-Integration-Secret", "")
    acc_for_auth = db.get_ads_account(req.clinic_id)
    db_key = (acc_for_auth or {}).get("logiction_integration_key") or ""
    env_key = os.environ.get("INTEGRATION_SECRET_KEY", "")
    expected_key = db_key or env_key  # DB設定を優先
    if expected_key and provided_key != expected_key:
        raise HTTPException(403, "Invalid integration secret key")

    synced = 0
    updated = 0

    with db.get_conn() as conn:
        for p in req.patients:
            age_group = _age_to_group(p.age)
            ltv = p.ltv_yen
            if ltv is None:
                ltv_info = integration_bridge.calculate_patient_ltv(
                    visit_count=p.visit_count,
                    total_revenue=float(p.total_revenue),
                    is_churned=False,
                    is_course_member=False,
                )
                ltv = int(ltv_info["ltv_value"])

            symptoms_json = json.dumps(p.symptoms or [], ensure_ascii=False)

            try:
                # PostgreSQL / SQLite 両対応の UPSERT
                if db.USE_PG:
                    conn.execute("""
                        INSERT INTO logiction_patients
                          (clinic_id, patient_id, gender, age, age_group,
                           address_pref, address_city, symptoms,
                           visit_count, total_revenue, ltv_yen,
                           acquisition_channel, gclid, first_visit_date)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT(clinic_id, patient_id) DO UPDATE SET
                          gender=EXCLUDED.gender, age=EXCLUDED.age,
                          age_group=EXCLUDED.age_group,
                          address_pref=EXCLUDED.address_pref,
                          address_city=EXCLUDED.address_city,
                          symptoms=EXCLUDED.symptoms,
                          visit_count=EXCLUDED.visit_count,
                          total_revenue=EXCLUDED.total_revenue,
                          ltv_yen=EXCLUDED.ltv_yen,
                          acquisition_channel=EXCLUDED.acquisition_channel,
                          gclid=EXCLUDED.gclid,
                          synced_at=NOW()
                    """, (
                        req.clinic_id, p.patient_id,
                        p.gender, p.age, age_group,
                        p.address_pref, p.address_city, symptoms_json,
                        p.visit_count, p.total_revenue, ltv,
                        p.acquisition_channel, p.gclid, p.first_visit_date
                    ))
                else:
                    conn.execute("""
                        INSERT INTO logiction_patients
                          (clinic_id, patient_id, gender, age, age_group,
                           address_pref, address_city, symptoms,
                           visit_count, total_revenue, ltv_yen,
                           acquisition_channel, gclid, first_visit_date)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(clinic_id, patient_id) DO UPDATE SET
                          gender=excluded.gender, age=excluded.age,
                          age_group=excluded.age_group,
                          address_pref=excluded.address_pref,
                          address_city=excluded.address_city,
                          symptoms=excluded.symptoms,
                          visit_count=excluded.visit_count,
                          total_revenue=excluded.total_revenue,
                          ltv_yen=excluded.ltv_yen,
                          acquisition_channel=excluded.acquisition_channel,
                          gclid=excluded.gclid,
                          synced_at=datetime('now','localtime')
                    """, (
                        req.clinic_id, p.patient_id,
                        p.gender, p.age, age_group,
                        p.address_pref, p.address_city, symptoms_json,
                        p.visit_count, p.total_revenue, ltv,
                        p.acquisition_channel, p.gclid, p.first_visit_date
                    ))
                synced += 1
            except Exception as e:
                if "UNIQUE constraint" in str(e) or "unique violation" in str(e):
                    updated += 1
                else:
                    print(f"[LOGICTION Sync] error patient_id={p.patient_id}: {e}")

        conn.execute("""
            INSERT INTO logiction_sync_log (clinic_id, synced_count, updated_count, sync_source)
            VALUES (?,?,?,'api')
        """ if not db.USE_PG else """
            INSERT INTO logiction_sync_log (clinic_id, synced_count, updated_count, sync_source)
            VALUES (%s,%s,%s,'api')
        """, (req.clinic_id, synced, updated))

    try:
        _auto_update_persona_from_patients(req.clinic_id, db)
    except Exception as e:
        print(f"[LOGICTION] ペルソナ自動更新エラー: {e}")

    return {
        "success": True,
        "synced": synced,
        "updated": updated,
        "total": len(req.patients),
        "message": f"{len(req.patients)}件処理（新規{synced}件・更新{updated}件）"
    }


def handle_persona_analysis(clinic_id: int, db):
    """蓄積患者データを多角的に分析してペルソナインサイトを返す"""
    with db.get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) as c FROM logiction_patients WHERE clinic_id=?",
            (clinic_id,)
        ).fetchone()["c"]

        if total == 0:
            return {
                "success": True,
                "total_patients": 0,
                "message": "LOGICTIONからの患者データがまだありません。",
                "insights": {}
            }

        ph = "%s" if db.USE_PG else "?"

        gender_rows = conn.execute(f"""
            SELECT gender, COUNT(*) as cnt,
                   AVG(ltv_yen) as avg_ltv,
                   AVG(visit_count) as avg_visits
            FROM logiction_patients
            WHERE clinic_id={ph} AND gender IS NOT NULL
            GROUP BY gender ORDER BY avg_ltv DESC
        """, (clinic_id,)).fetchall()

        age_rows = conn.execute(f"""
            SELECT age_group, COUNT(*) as cnt,
                   AVG(ltv_yen) as avg_ltv,
                   AVG(visit_count) as avg_visits
            FROM logiction_patients
            WHERE clinic_id={ph} AND age_group != '不明'
            GROUP BY age_group ORDER BY avg_ltv DESC
        """, (clinic_id,)).fetchall()

        channel_rows = conn.execute(f"""
            SELECT acquisition_channel, COUNT(*) as cnt,
                   AVG(ltv_yen) as avg_ltv,
                   SUM(total_revenue) as total_rev
            FROM logiction_patients
            WHERE clinic_id={ph} AND acquisition_channel IS NOT NULL
            GROUP BY acquisition_channel ORDER BY avg_ltv DESC
        """, (clinic_id,)).fetchall()

        area_rows = conn.execute(f"""
            SELECT address_pref, COUNT(*) as cnt, AVG(ltv_yen) as avg_ltv
            FROM logiction_patients
            WHERE clinic_id={ph} AND address_pref IS NOT NULL
            GROUP BY address_pref ORDER BY cnt DESC LIMIT 10
        """, (clinic_id,)).fetchall()

        all_symptoms_rows = conn.execute(f"""
            SELECT symptoms, ltv_yen FROM logiction_patients WHERE clinic_id={ph}
        """, (clinic_id,)).fetchall()
        symptom_stats = {}
        for row in all_symptoms_rows:
            try:
                for s in json.loads(row["symptoms"] or "[]"):
                    if s not in symptom_stats:
                        symptom_stats[s] = {"cnt": 0, "ltv_sum": 0}
                    symptom_stats[s]["cnt"] += 1
                    symptom_stats[s]["ltv_sum"] += (row["ltv_yen"] or 0)
            except Exception:
                pass
        symptom_analysis = sorted([
            {"symptom": k, "cnt": v["cnt"], "avg_ltv": int(v["ltv_sum"] / v["cnt"])}
            for k, v in symptom_stats.items() if v["cnt"] > 0
        ], key=lambda x: x["avg_ltv"], reverse=True)[:10]

        last_sync = conn.execute(f"""
            SELECT synced_at, synced_count, updated_count
            FROM logiction_sync_log WHERE clinic_id={ph}
            ORDER BY synced_at DESC LIMIT 1
        """, (clinic_id,)).fetchone()

    return {
        "success": True,
        "total_patients": total,
        "last_sync": dict(last_sync) if last_sync else None,
        "insights": {
            "by_gender": [dict(r) for r in gender_rows],
            "by_age_group": [dict(r) for r in age_rows],
            "by_channel": [dict(r) for r in channel_rows],
            "by_area": [dict(r) for r in area_rows],
            "by_symptom": symptom_analysis,
        }
    }


async def handle_apply_to_ads(clinic_id: int, platform: str, db, _require_account, _get_ads_client):
    """患者データ分析をGoogle Ads入札調整に反映"""
    with db.get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) as c FROM logiction_patients WHERE clinic_id=?",
            (clinic_id,)
        ).fetchone()["c"]

    if total == 0:
        raise HTTPException(400, "患者データがありません。まずLOGICTIONからデータを同期してください。")

    analysis = handle_persona_analysis(clinic_id, db)
    insights = analysis.get("insights", {})
    adjustments_applied = []
    warnings = []

    try:
        acc = _require_account(clinic_id)
        client = _get_ads_client(acc, platform)
        campaigns = db.list_campaigns(clinic_id)

        for camp in campaigns:
            g_id = camp.get("google_campaign_id", "")
            if not g_id:
                continue

            # 性別別入札調整
            gender_data = insights.get("by_gender", [])
            if len(gender_data) > 1:
                avg_all = sum(r["avg_ltv"] for r in gender_data) / len(gender_data)
                for g_data in gender_data:
                    ratio = g_data["avg_ltv"] / max(avg_all, 1)
                    adj_pct = max(-20, min(20, int((ratio - 1) * 100)))
                    gender_map = {"female": "FEMALE", "male": "MALE"}
                    g_type = gender_map.get(g_data.get("gender", ""), "")
                    if g_type and adj_pct != 0:
                        try:
                            client.set_demographic_bid_adjustment(g_id, "gender", g_type, adj_pct)
                            adjustments_applied.append({
                                "type": "gender", "value": g_data["gender"],
                                "adjustment_pct": adj_pct,
                                "avg_ltv": int(g_data["avg_ltv"]),
                                "campaign": camp.get("name")
                            })
                        except Exception as e:
                            warnings.append(f"gender bid adjustment failed: {e}")

            # 年齢別入札調整
            age_data = insights.get("by_age_group", [])
            if len(age_data) > 1:
                avg_all = sum(r["avg_ltv"] for r in age_data) / len(age_data)
                age_map = {
                    "18-24歳": "AGE_RANGE_18_24", "25-34歳": "AGE_RANGE_25_34",
                    "35-44歳": "AGE_RANGE_35_44", "45-54歳": "AGE_RANGE_45_54",
                    "55-64歳": "AGE_RANGE_55_64", "65歳以上": "AGE_RANGE_65_UP",
                }
                for a_data in age_data:
                    ratio = a_data["avg_ltv"] / max(avg_all, 1)
                    adj_pct = max(-20, min(20, int((ratio - 1) * 100)))
                    age_type = age_map.get(a_data.get("age_group", ""), "")
                    if age_type and adj_pct != 0:
                        try:
                            client.set_demographic_bid_adjustment(g_id, "age", age_type, adj_pct)
                            adjustments_applied.append({
                                "type": "age", "value": a_data["age_group"],
                                "adjustment_pct": adj_pct,
                                "avg_ltv": int(a_data["avg_ltv"]),
                                "campaign": camp.get("name")
                            })
                        except Exception as e:
                            warnings.append(f"age bid adjustment failed: {e}")

    except Exception as e:
        warnings.append(f"Google Ads API接続エラー: {str(e)}")

    db.add_audit_log(
        clinic_id, "system",
        f"[LOGICTION→Ads] 入札調整{len(adjustments_applied)}件適用",
        entity="persona_bid_apply"
    )

    return {
        "success": True,
        "adjustments_applied": adjustments_applied,
        "adjustments_count": len(adjustments_applied),
        "warnings": warnings,
        "persona_updated": True,
        "message": f"{len(adjustments_applied)}件の入札調整をGoogle Adsに反映しました"
    }
