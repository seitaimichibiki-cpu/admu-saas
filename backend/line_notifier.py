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
    """週次レポートをLINE Flex Messageで送信（リッチカード形式）"""
    week = datetime.now().strftime("%Y年%m月%d日（月）週")
    total_cost = summary.get("total_cost_yen", 0)
    total_clicks = summary.get("total_clicks", 0)
    avg_ctr = summary.get("avg_ctr", 0)
    total_cv = summary.get("total_conversions", 0)
    cpa = summary.get("cpa", 0)
    total_impressions = summary.get("total_impressions", 0)

    # AIアドバイスと色
    if avg_ctr >= 5.0:
        advice = "CTRが好調です！予算を増加してリーチを拡大しましょう 🚀"
        advice_color = "#10b981"
    elif avg_ctr < 2.0:
        advice = "CTRが低めです。広告文の見直しをAIに依頼してみてください 📝"
        advice_color = "#ef4444"
    elif cpa > 5000:
        advice = "CPAがやや高めです。LPのCTA配置を改善すると効果的です 🔧"
        advice_color = "#f59e0b"
    else:
        advice = "安定した配信中です。このまま継続してください 📊"
        advice_color = "#3b82f6"

    # Flex Message カード構造
    flex_message = {
        "type": "flex",
        "altText": f"📈 週次レポート | {clinic_name} | 費用¥{total_cost:,}",
        "contents": {
            "type": "bubble",
            "size": "giga",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#0f172a",
                "paddingAll": "20px",
                "contents": [
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "contents": [
                            {
                                "type": "text",
                                "text": "AdMu",
                                "weight": "bold",
                                "size": "xl",
                                "color": "#3b82f6",
                            },
                            {
                                "type": "text",
                                "text": "WEEKLY REPORT",
                                "size": "xs",
                                "color": "#64748b",
                                "align": "end",
                                "gravity": "center",
                            }
                        ]
                    },
                    {"type": "text", "text": clinic_name, "weight": "bold", "size": "lg", "color": "#f1f5f9", "margin": "sm"},
                    {"type": "text", "text": week, "size": "xs", "color": "#64748b", "margin": "xs"},
                ]
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#1e293b",
                "paddingAll": "16px",
                "spacing": "md",
                "contents": [
                    # KPIグリッド（2列）
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "spacing": "sm",
                        "contents": [
                            _flex_kpi_box("💰 総費用", f"¥{total_cost:,}", "#f59e0b"),
                            _flex_kpi_box("🖱 クリック", f"{total_clicks:,}回", "#3b82f6"),
                        ]
                    },
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "spacing": "sm",
                        "contents": [
                            _flex_kpi_box("📈 CTR", f"{avg_ctr:.2f}%", "#10b981" if avg_ctr >= 3 else "#ef4444"),
                            _flex_kpi_box("🎯 CV数", f"{total_cv:.1f}件", "#6366f1"),
                        ]
                    },
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "spacing": "sm",
                        "contents": [
                            _flex_kpi_box("💡 CPA", f"¥{cpa:,}" if cpa > 0 else "─", "#94a3b8"),
                            _flex_kpi_box("👁 表示回数", f"{total_impressions:,}", "#94a3b8"),
                        ]
                    },
                    # 区切り線
                    {"type": "separator", "margin": "md", "color": "#334155"},
                    # AIアドバイス
                    {
                        "type": "box",
                        "layout": "vertical",
                        "backgroundColor": "#0f172a",
                        "cornerRadius": "8px",
                        "paddingAll": "12px",
                        "contents": [
                            {
                                "type": "text",
                                "text": "⚡ AIアドバイス",
                                "size": "xs",
                                "color": "#64748b",
                                "weight": "bold",
                            },
                            {
                                "type": "text",
                                "text": advice,
                                "size": "sm",
                                "color": advice_color,
                                "wrap": True,
                                "margin": "xs",
                                "weight": "bold",
                            }
                        ]
                    },
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#0f172a",
                "paddingAll": "12px",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#3b82f6",
                        "action": {
                            "type": "uri",
                            "label": "📊 ダッシュボードで詳細を確認",
                            "uri": "https://admu-backend-jxi0.onrender.com"
                        },
                        "height": "sm",
                    }
                ]
            }
        }
    }

    return _send_flex(channel_token, user_id, flex_message)


def _flex_kpi_box(label: str, value: str, color: str) -> dict:
    """KPI表示用のFlexボックスコンポーネント"""
    return {
        "type": "box",
        "layout": "vertical",
        "flex": 1,
        "backgroundColor": "#0f172a",
        "cornerRadius": "8px",
        "paddingAll": "10px",
        "contents": [
            {"type": "text", "text": label, "size": "xxs", "color": "#64748b"},
            {"type": "text", "text": value, "size": "lg", "weight": "bold", "color": color, "margin": "xs"},
        ]
    }


def _send_flex(channel_token: str, user_id: str, flex_content: dict) -> bool:
    """Flex Messageを送信"""
    if not channel_token or not user_id:
        print("[LINE] token/user_id が未設定のためスキップ")
        return False
    payload = json.dumps({
        "to": user_id,
        "messages": [flex_content]
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
            print(f"[LINE] Flex Message送信成功 status={resp.status} to={user_id}")
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"[LINE] Flex Message送信失敗 {e.code}: {body}")
        # Flex Message非対応の場合はテキストにフォールバック
        return False
    except Exception as e:
        print(f"[LINE] Flex Message送信エラー: {e}")
        return False

