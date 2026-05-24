"""
integration_bridge.py
======================
LOGICTIONから広告運用システムへオフラインCVデータを送信するモジュール。
LTVベースのコンバージョン値計算により、Google AdsのtROAS入札精度を最大化します。
サーバー未起動時も例外を吸収して静かにスキップします。

使い方（LOGICTIONの来院記録処理内から呼ぶ）:
    import asyncio
    from integration_bridge import send_ltv_conversion
    asyncio.run(send_ltv_conversion(
        gclid           = session.get("gclid", ""),
        clinic_id       = 1,
        patient_id      = str(patient.id),
        visit_count     = patient.visit_count,
        total_revenue   = patient.total_revenue,
        is_churned      = patient.is_churned,
        last_menu       = "腰痛コース",
    ))
"""

import os
import hashlib
import hmac
import logging
import json
from datetime import datetime
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

ADS_SYSTEM_BASE_URL = os.getenv("ADS_SYSTEM_BASE_URL", "http://localhost:8001")
INTEGRATION_SECRET  = os.getenv("INTEGRATION_SECRET_KEY", "dev-secret-key")
REQUEST_TIMEOUT     = 10

# ============================================================
# LTV計算定数（整体院業界標準値）
# ============================================================
# 初回来院の期待LTV（コース未契約）
LTV_FIRST_VISIT_BASE       = 15_000   # ¥15,000
# 継続来院1回あたりの追加LTV貢献
LTV_PER_REPEAT_VISIT       = 8_000    # ¥8,000/回
# コース・回数券購入時の乗数
LTV_COURSE_MULTIPLIER      = 2.5
# 休眠患者（再来院）の再活性化価値
LTV_REACTIVATION_BONUS     = 20_000   # ¥20,000
# LTV上限（外れ値防止）
LTV_CAP                    = 300_000  # ¥300,000


def calculate_patient_ltv(
    visit_count: int = 1,
    total_revenue: float = 0,
    is_churned: bool = False,
    last_menu: Optional[str] = None,
    is_course_member: bool = False,
) -> dict:
    """
    患者データからLTV（生涯価値）を計算する。

    Args:
        visit_count:       累計来院回数
        total_revenue:     累計売上金額
        is_churned:        休眠患者フラグ（60日以上来院なし）
        last_menu:         直近施術メニュー名
        is_course_member:  コース・回数券加入フラグ

    Returns:
        dict: {
            "ltv_value": 計算済みLTV（Google Adsへ送る値）,
            "ltv_grade": S/A/B/C のグレード,
            "reason": 計算根拠の説明,
        }
    """
    # ① 実績売上がある場合はそれを基準に、なければ推定計算
    if total_revenue and total_revenue > 0:
        base_ltv = float(total_revenue)
    else:
        # 来院回数から推定
        if visit_count <= 1:
            base_ltv = LTV_FIRST_VISIT_BASE
        else:
            base_ltv = LTV_FIRST_VISIT_BASE + (visit_count - 1) * LTV_PER_REPEAT_VISIT

    # ② コース・回数券加入者は将来収益が高い
    if is_course_member:
        base_ltv *= LTV_COURSE_MULTIPLIER
        reason = f"コース加入・来院{visit_count}回 → 将来LTV¥{int(base_ltv):,}"
    elif is_churned:
        # 休眠からの再来院は再活性化ボーナス
        base_ltv += LTV_REACTIVATION_BONUS
        reason = f"休眠患者の再来院 → 再活性化ボーナス込みLTV¥{int(base_ltv):,}"
    elif visit_count >= 5:
        reason = f"リピーター（来院{visit_count}回） → LTV¥{int(base_ltv):,}"
    else:
        reason = f"新規・来院{visit_count}回 → 基本LTV¥{int(base_ltv):,}"

    # ③ メニューによる補正
    if last_menu:
        if any(kw in last_menu for kw in ["コース", "月額", "定額", "プラン"]):
            base_ltv *= 1.3
        elif any(kw in last_menu for kw in ["回数券", "パック", "セット"]):
            base_ltv *= 1.15

    # ④ LTV上限適用
    ltv_value = min(base_ltv, LTV_CAP)

    # ⑤ グレード判定
    if ltv_value >= 100_000:
        grade = "S"
    elif ltv_value >= 50_000:
        grade = "A"
    elif ltv_value >= 20_000:
        grade = "B"
    else:
        grade = "C"

    return {
        "ltv_value": round(ltv_value),
        "ltv_grade": grade,
        "reason": reason,
        "visit_count": visit_count,
        "is_course_member": is_course_member,
        "is_churned": is_churned,
    }


def _sign(payload_str: str) -> str:
    return hmac.new(INTEGRATION_SECRET.encode(), payload_str.encode(), hashlib.sha256).hexdigest()


def make_headers(payload_str: str) -> dict:
    return {
        "Content-Type": "application/json",
        "X-Integration-Signature": _sign(payload_str),
        "X-Integration-Source": "logiction",
    }


async def send_ltv_conversion(
    gclid: str,
    clinic_id: int = 1,
    patient_id: Optional[str] = None,
    visit_count: int = 1,
    total_revenue: float = 0,
    is_churned: bool = False,
    last_menu: Optional[str] = None,
    is_course_member: bool = False,
    conversion_name: str = "来院",
) -> dict:
    """
    LOGICTIONの患者データからLTVを計算し、
    Google Ads Offline Conversion APIへ送信する（メイン関数）。

    これにより Google Ads の tROAS 自動入札が「初回来院金額」ではなく
    「患者の生涯価値」をコンバージョン値として学習し、
    優良患者を獲得できる広告配信を自動最適化します。

    Returns:
        dict: { "success": bool, "ltv": dict, "sent_value": int }
    """
    # LTV計算
    ltv = calculate_patient_ltv(
        visit_count=visit_count,
        total_revenue=total_revenue,
        is_churned=is_churned,
        last_menu=last_menu,
        is_course_member=is_course_member,
    )

    logger.info(
        f"[LTV-OCT] clinic={clinic_id} patient={patient_id} "
        f"grade={ltv['ltv_grade']} value=¥{ltv['ltv_value']:,} "
        f"reason={ltv['reason']}"
    )

    # Google Ads OCT へ送信
    result = await send_offline_conversion(
        gclid=gclid,
        conversion_name=conversion_name,
        conversion_value=ltv["ltv_value"],
        clinic_id=clinic_id,
        patient_id=patient_id,
    )

    return {
        "success": result,
        "ltv": ltv,
        "sent_value": ltv["ltv_value"],
    }


async def send_offline_conversion(
    gclid: str,
    conversion_name: str,
    conversion_value: float,
    clinic_id: int = 1,
    patient_id: Optional[str] = None,
) -> bool:
    """
    LOGICTIONから広告運用システムへオフラインCVデータを送信する。
    サーバー未接続時はログのみ記録してFalseを返す（スタブ動作）。

    Args:
        gclid:            Google Click ID（予約フォームのURLパラメータから取得）
        conversion_name:  CV名（例: "来院", "回数券購入", "コース契約"）
        conversion_value: 売上金額またはLTV値
        clinic_id:        クリニックID
        patient_id:       患者ID（ハッシュ化して送信）
    """
    payload = {
        "gclid": gclid,
        "conversion_name": conversion_name,
        "conversion_value": conversion_value,
        "conversion_time": datetime.now().isoformat(),
        "clinic_id": clinic_id,
        "patient_id": hashlib.sha256((patient_id or "").encode()).hexdigest() if patient_id else None,
    }
    payload_str = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    url = f"{ADS_SYSTEM_BASE_URL}/api/integration/offline-conversion"

    logger.info(f"[OCT送信] {conversion_name} ¥{conversion_value:,.0f} → {url}")

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.post(url, content=payload_str, headers=make_headers(payload_str))
            resp.raise_for_status()
            logger.info(f"[OCT送信] 成功: {resp.status_code}")
            return True
    except httpx.ConnectError:
        logger.warning("[OCT送信] 広告運用システムに接続できません（サーバー未起動）。スタブとしてスキップ。")
        return False
    except Exception as e:
        logger.error(f"[OCT送信] エラー: {e}")
        return False

