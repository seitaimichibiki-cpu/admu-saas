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
            SELECT address_pref, address_city,
                   TRIM(COALESCE(address_pref,'') || ' ' || COALESCE(address_city,'')) as area_label,
                   COUNT(*) as c FROM logiction_patients
            WHERE clinic_id=?
              AND TRIM(COALESCE(address_pref,'') || COALESCE(address_city,'')) != ''
            GROUP BY address_pref, address_city ORDER BY c DESC LIMIT 1
        """, (clinic_id,)).fetchone()

    gender_label = {"male": "男性", "female": "女性"}.get(
        (top_gender["gender"] if top_gender else ""), ""
    )
    age_label = top_age["age_group"] if top_age else ""
    area_label = top_area["area_label"].strip() if top_area else ""

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
            SELECT
                address_pref,
                address_city,
                TRIM(COALESCE(address_pref,'') || ' ' || COALESCE(address_city,'')) as area_label,
                COUNT(*) as cnt,
                AVG(ltv_yen) as avg_ltv
            FROM logiction_patients
            WHERE clinic_id={ph}
              AND TRIM(COALESCE(address_pref,'') || COALESCE(address_city,'')) != ''
            GROUP BY address_pref, address_city
            ORDER BY cnt DESC
            LIMIT 15
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
    """
    LOGICTIONの患者データを多角的に分析し、Google Ads入札を自動最適化する。

    最適化の軸：
        1. 性別別LTV → 性別入札調整
        2. 年齢層別LTV → 年齢入札調整
        3. 来院チャネル別分析 → 推奨予算配分（ログのみ、API制限なし）
        4. 症状別LTV分析 → キーワード推奨（ログのみ）
        5. 地域別来院数分析 → エリアターゲット推奨（ログのみ）
    """
    ph = "%s" if db.USE_PG else "?"

    with db.get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) as c FROM logiction_patients WHERE clinic_id={ph}",
            (clinic_id,)
        ).fetchone()["c"]

    if total == 0:
        raise HTTPException(400, "患者データがありません。まずLOGICTIONからデータを同期してください。")

    analysis = handle_persona_analysis(clinic_id, db)
    insights = analysis.get("insights", {})
    adjustments_applied = []
    recommendations = []   # APIでは変更せず推奨情報として返す
    warnings = []

    # ========================================================
    # 1 & 2: Google Ads API経由の入札調整（性別・年齢）
    # ========================================================
    try:
        acc = _require_account(clinic_id)
        client = _get_ads_client(acc, platform)
        campaigns = db.list_campaigns(clinic_id)
        active_campaigns = [c for c in campaigns if c.get("status") in ("ENABLED", "PAUSED") and c.get("google_campaign_id")]

        for camp in active_campaigns:
            g_id = camp.get("google_campaign_id", "")
            if not g_id:
                continue

            # 性別別入札調整
            gender_data = insights.get("by_gender", [])
            if len(gender_data) > 1:
                avg_all = sum(r["avg_ltv"] for r in gender_data) / len(gender_data)
                for g_data in gender_data:
                    ratio = g_data["avg_ltv"] / max(avg_all, 1)
                    # LTV差が5%以上の場合のみ調整（ノイズ除去）
                    if abs(ratio - 1) < 0.05:
                        continue
                    adj_pct = max(-30, min(30, int((ratio - 1) * 100)))
                    gender_map = {"female": "FEMALE", "male": "MALE"}
                    g_type = gender_map.get(g_data.get("gender", ""), "")
                    if g_type and adj_pct != 0:
                        try:
                            result = client.set_demographic_bid_adjustment(g_id, "gender", g_type, adj_pct)
                            label = {"female": "女性", "male": "男性"}.get(g_data.get("gender", ""), g_data.get("gender", ""))
                            adjustments_applied.append({
                                "type": "gender",
                                "label": label,
                                "value": g_data["gender"],
                                "adjustment_pct": adj_pct,
                                "avg_ltv": int(g_data["avg_ltv"]),
                                "patient_count": g_data.get("cnt", 0),
                                "campaign": camp.get("name"),
                                "campaign_id": g_id,
                                "applied_to_api": result.get("success", False),
                            })
                        except Exception as e:
                            warnings.append(f"性別入札調整エラー ({g_data.get('gender')}): {e}")

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
                    if abs(ratio - 1) < 0.05:
                        continue
                    adj_pct = max(-30, min(30, int((ratio - 1) * 100)))
                    age_type = age_map.get(a_data.get("age_group", ""), "")
                    if age_type and adj_pct != 0:
                        try:
                            result = client.set_demographic_bid_adjustment(g_id, "age", age_type, adj_pct)
                            adjustments_applied.append({
                                "type": "age",
                                "label": a_data.get("age_group", ""),
                                "value": a_data.get("age_group", ""),
                                "adjustment_pct": adj_pct,
                                "avg_ltv": int(a_data["avg_ltv"]),
                                "patient_count": a_data.get("cnt", 0),
                                "campaign": camp.get("name"),
                                "campaign_id": g_id,
                                "applied_to_api": result.get("success", False),
                            })
                        except Exception as e:
                            warnings.append(f"年齢入札調整エラー ({a_data.get('age_group')}): {e}")

    except Exception as e:
        warnings.append(f"Google Ads API接続エラー: {str(e)}")

    # ========================================================
    # 3: 来院チャネル別分析 → 推奨（API変更なし）
    # ========================================================
    channel_data = insights.get("by_channel", [])
    if channel_data:
        best_channel = channel_data[0]  # avg_ltv降順でソート済み
        if best_channel.get("avg_ltv", 0) > 0:
            recommendations.append({
                "type": "channel",
                "title": f"最高LTVチャネル: {best_channel.get('acquisition_channel', '不明')}",
                "detail": f"平均LTV ¥{int(best_channel.get('avg_ltv', 0)):,}（{best_channel.get('cnt', 0)}名）",
                "action": "このチャネルへの予算配分を増やすことを推奨します",
                "avg_ltv": int(best_channel.get("avg_ltv", 0)),
            })
        # Google広告経由の患者が特定できている場合は特記
        google_ch = next((c for c in channel_data if "google" in (c.get("acquisition_channel") or "").lower()), None)
        if google_ch:
            recommendations.append({
                "type": "channel_google",
                "title": f"Google広告経由患者のLTV",
                "detail": f"¥{int(google_ch.get('avg_ltv', 0)):,}（{google_ch.get('cnt', 0)}名）",
                "action": "Google Ads経由の患者が特定できています。OCTでの価値ベース入札への切替を推奨します",
                "avg_ltv": int(google_ch.get("avg_ltv", 0)),
            })

    # ========================================================
    # 4: 症状別LTV分析 → キーワード推奨
    # ========================================================
    symptom_data = insights.get("by_symptom", [])
    if symptom_data:
        top_symptom = symptom_data[0]
        recommendations.append({
            "type": "symptom",
            "title": f"高LTV症状キーワード: 「{top_symptom.get('symptom', '')}」",
            "detail": f"平均LTV ¥{int(top_symptom.get('avg_ltv', 0)):,}（{top_symptom.get('cnt', 0)}名）",
            "action": f"「{top_symptom.get('symptom', '')}」系のキーワードに入札単価を上げることを推奨します",
            "avg_ltv": int(top_symptom.get("avg_ltv", 0)),
        })
        # 上位3症状をキーワード候補としてリスト
        kw_suggestions = [s.get("symptom", "") for s in symptom_data[:3]]
        if kw_suggestions:
            recommendations.append({
                "type": "keyword_suggestion",
                "title": "推奨キーワード（高LTV症状）",
                "detail": "・".join(kw_suggestions),
                "action": "これらの症状ワードを含むキーワードを広告グループに追加することを推奨します",
                "keywords": kw_suggestions,
            })

    # ========================================================
    # 5: 地域別来院分析 → エリアターゲット推奨
    # ========================================================
    area_data = insights.get("by_area", [])
    if area_data and len(area_data) > 0:
        # 上位エリア（市区町村単位）をリストアップ
        top_areas = area_data[:5]
        top_area = top_areas[0]
        area_name = top_area.get("area_label") or top_area.get("address_city") or top_area.get("address_pref") or "不明"
        area_list = [
            (a.get("area_label") or a.get("address_city") or a.get("address_pref") or "不明", a.get("cnt", 0), int(a.get("avg_ltv", 0)))
            for a in top_areas
        ]
        recommendations.append({
            "type": "area",
            "title": f"最多来院エリア（市区町村別）: {area_name}",
            "detail": f"{top_area.get('cnt', 0)}名来院（平均LTV ¥{int(top_area.get('avg_ltv', 0)):,}）",
            "action": f"{area_name}への地域ターゲットを強化することを推奨します",
            "avg_ltv": int(top_area.get("avg_ltv", 0)),
            "area_breakdown": [
                {"name": name, "cnt": cnt, "avg_ltv": ltv}
                for name, cnt, ltv in area_list
            ],
        })

    # ========================================================
    # 監査ログ & ペルソナ自動更新
    # ========================================================
    try:
        _auto_update_persona_from_patients(clinic_id, db)
    except Exception as e:
        warnings.append(f"ペルソナ自動更新エラー: {e}")

    db.add_audit_log(
        clinic_id, "system",
        f"[LOGICTION自動入札最適化] 入札調整{len(adjustments_applied)}件適用、推奨{len(recommendations)}件生成",
        entity="persona_bid_apply"
    )

    return {
        "success": True,
        "total_patients_analyzed": total,
        "adjustments_applied": adjustments_applied,
        "adjustments_count": len(adjustments_applied),
        "recommendations": recommendations,
        "recommendations_count": len(recommendations),
        "warnings": warnings,
        "persona_updated": True,
        "insights_summary": {
            "top_gender": (insights.get("by_gender") or [{}])[0],
            "top_age": (insights.get("by_age_group") or [{}])[0],
            "top_channel": (insights.get("by_channel") or [{}])[0],
            "top_symptom": (insights.get("by_symptom") or [{}])[0],
            "top_area": (insights.get("by_area") or [{}])[0],
        },
        "message": f"✅ {len(adjustments_applied)}件の入札調整を適用、{len(recommendations)}件の改善提案を生成しました（分析患者数: {total}名）"
    }

