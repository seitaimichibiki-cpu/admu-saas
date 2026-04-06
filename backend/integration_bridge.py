"""
integration_bridge.py
======================
LOGICTIONから広告運用システムへオフラインCVデータを送信するスタブモジュール。
サーバー未起動時も例外を吸収して静かにスキップします。

使い方（LOGICTIONの来院記録処理内から呼ぶ）:
    import asyncio
    from integration_bridge import send_offline_conversion
    asyncio.run(send_offline_conversion(
        gclid       = session.get("gclid", ""),  # URLパラメータで受け取ったもの
        conversion_name  = "来院",
        conversion_value = 0,
        clinic_id        = 1,
        patient_id       = str(patient.id),
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


def _sign(payload_str: str) -> str:
    return hmac.new(INTEGRATION_SECRET.encode(), payload_str.encode(), hashlib.sha256).hexdigest()


def make_headers(payload_str: str) -> dict:
    return {
        "Content-Type": "application/json",
        "X-Integration-Signature": _sign(payload_str),
        "X-Integration-Source": "logiction",
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
        conversion_value: 売上金額（例: 30000）
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
