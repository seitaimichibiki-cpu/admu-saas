"""
line_notifier.py - LINE Messaging API 通知モジュール
"""
import json
import urllib.request
import urllib.error
from datetime import datetime


def _micros_to_yen(micros: int) -> str:
    return f"¥{int(micros / 1_000_000):,}"


def send_text(channel_token: str, user_id: str, message: str) -> bool:
    """テキストメッセージを送信"""
    if not channel_token or not user_id:
        print("[LINE] token/user_id が未設定のためスキップ")
        return False
    payload = json.dumps({
        "to": user_id,
        "messages": [{"type": "text", "text": message}]
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {channel_token}",
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"[LINE] 送信成功 status={resp.status} to={user_id}")
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"[LINE] 送信失敗 {e.code}: {body}")
        return False
    except Exception as e:
        print(f"[LINE] 送信エラー: {e}")
        return False


def send_daily_report(channel_token: str, user_id: str, summary: dict) -> bool:
    """日次レポートをFlex Message風テキストで送信"""
    today = datetime.now().strftime("%Y年%m月%d日")
    perf = summary.get("performance", {})
    alerts = summary.get("alerts", [])

    impressions = perf.get("impressions", 0)
    clicks = perf.get("clicks", 0)
    ctr = perf.get("ctr", 0)
    cost = perf.get("cost_micros", 0)
    conversions = perf.get("conversions", 0)
    cvr = perf.get("cvr", 0)
    avg_cpc = perf.get("avg_cpc_micros", 0)

    alert_text = ""
    if alerts:
        alert_items = "\n".join([f"  ⚠️ {a['message']}" for a in alerts[:3]])
        alert_text = f"\n\n📢 アラート ({len(alerts)}件)\n{alert_items}"

    msg = f"""📊 Google広告 日次レポート
{today}
━━━━━━━━━━━━━━
👁 表示回数: {impressions:,}
🖱 クリック数: {clicks:,}
📈 CTR: {ctr:.2f}%
💰 費用: {_micros_to_yen(cost)}
🎯 CV数: {conversions:.1f}
✅ CVR: {cvr:.2f}%
🔑 平均CPC: {_micros_to_yen(avg_cpc)}{alert_text}
━━━━━━━━━━━━━━
by 広告運用システム"""
    return send_text(channel_token, user_id, msg)


def send_alert(channel_token: str, user_id: str, level: str, message: str) -> bool:
    """アラート通知を送信"""
    icons = {"ERROR": "🚨", "WARNING": "⚠️", "INFO": "ℹ️"}
    icon = icons.get(level, "📌")
    text = f"{icon} 広告アラート [{level}]\n{message}\n\n{datetime.now().strftime('%H:%M')}"
    return send_text(channel_token, user_id, text)


def send_bid_adjustment_report(channel_token: str, user_id: str, logs: list) -> bool:
    """入札調整結果を通知"""
    if not logs:
        return True
    items = "\n".join([f"  • {l['rule_name']}: {l['result']}" for l in logs[:5]])
    msg = f"""🤖 入札調整を実行しました
{datetime.now().strftime('%H:%M')}
━━━━━━━━━━━━━━
{items}
━━━━━━━━━━━━━━
by AdMu 広告AI"""
    return send_text(channel_token, user_id, msg)


def send_weekly_report(channel_token: str, user_id: str, clinic_name: str, summary: dict) -> bool:
    """週次レポートをLINEに送信"""
    week = datetime.now().strftime("%Y年%m月%d日（月）週")
    total_cost = summary.get("total_cost_yen", 0)
    total_clicks = summary.get("total_clicks", 0)
    avg_ctr = summary.get("avg_ctr", 0)
    total_cv = summary.get("total_conversions", 0)
    cpa = summary.get("cpa", 0)

    # 簡易アドバイス
    if avg_ctr >= 5.0:
        advice = "✅ CTR好調！予算増加を検討ください"
    elif avg_ctr < 2.0:
        advice = "📌 CTR低め。広告文の見直しを推奨"
    elif cpa > 5000:
        advice = "⚠️ CPAが高め。LPの改善を検討ください"
    else:
        advice = "📊 安定的に配信中です"

    msg = f"""📈 週次レポート
{week}
{clinic_name}
━━━━━━━━━━━━━━
💰 総費用: ¥{total_cost:,}
🖱 クリック: {total_clicks:,}
📈 CTR: {avg_ctr:.2f}%
🎯 CV数: {total_cv:.1f}件
💡 CPA: {'¥' + f'{cpa:,}' if cpa > 0 else '—'}
━━━━━━━━━━━━━━
⚡ AIアドバイス:
{advice}
━━━━━━━━━━━━━━
by AdMu 広告AI"""
    return send_text(channel_token, user_id, msg)

