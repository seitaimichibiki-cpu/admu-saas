"""
email_notifier.py - メール送信モジュール
優先順位: Resend API > Gmail SMTP > コンソール出力（開発用）
"""
import os
from datetime import datetime

# ---- 設定 ----
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
SMTP_SERVER    = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT      = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER      = os.environ.get("SMTP_USER", "")
SMTP_PASS      = os.environ.get("SMTP_PASS", "")
FROM_EMAIL     = os.environ.get("FROM_EMAIL", SMTP_USER or "noreply@admu.jp")
APP_BASE_URL   = os.environ.get("APP_BASE_URL", "http://localhost:8001")


def _send(to: str, subject: str, html: str) -> bool:
    """Resend > SMTP > コンソールの順で送信を試みる共通関数"""

    # ── Resend API ──────────────────────────────────────────
    if RESEND_API_KEY:
        try:
            import resend
            resend.api_key = RESEND_API_KEY
            resend.Emails.send({
                "from": f"AdMu <{FROM_EMAIL}>",
                "to": [to],
                "subject": subject,
                "html": html,
            })
            print(f"[Email/Resend] 送信完了: {to} / {subject}")
            return True
        except Exception as e:
            print(f"[Email/Resend] 送信失敗: {e}")
            return False

    # ── Gmail SMTP ──────────────────────────────────────────
    if SMTP_USER and SMTP_PASS:
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = SMTP_USER
            msg["To"]      = to
            msg.attach(MIMEText(html, "html", "utf-8"))
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as s:
                s.starttls()
                s.login(SMTP_USER, SMTP_PASS)
                s.sendmail(SMTP_USER, to, msg.as_string())
            print(f"[Email/SMTP] 送信完了: {to} / {subject}")
            return True
        except Exception as e:
            print(f"[Email/SMTP] 送信失敗: {e}")
            return False

    # ── 未設定（コンソール出力） ─────────────────────────────
    print(f"[Email] 未設定のためスキップ: {to} / {subject}")
    return False


# ============================================================
# パスワードリセットメール
# ============================================================
def send_password_reset_email(to: str, token: str) -> bool:
    reset_url = f"{APP_BASE_URL}/?reset_token={token}"
    subject   = "【AdMu】パスワードリセットのご案内"
    html = f"""
<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#0b0f1a;font-family:'Helvetica Neue',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0b0f1a;padding:32px 0;">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;">
        <tr>
          <td style="background:#1e293b;border-radius:16px;padding:40px 40px 32px;border:1px solid rgba(255,255,255,0.08);">
            <div style="text-align:center;margin-bottom:28px;">
              <span style="font-size:22px;font-weight:900;color:#3b82f6;letter-spacing:-0.5px;">AdMu</span>
              <p style="color:#475569;font-size:11px;letter-spacing:2px;margin:4px 0 0;">無を、極める。</p>
            </div>
            <h2 style="color:#f1f5f9;font-size:20px;font-weight:700;margin:0 0 16px;text-align:center;">🔐 パスワードリセット</h2>
            <p style="color:#94a3b8;font-size:14px;line-height:1.8;margin:0 0 24px;">
              パスワードリセットのリクエストを受け付けました。<br>
              以下のボタンをクリックして、新しいパスワードを設定してください。<br>
              このリンクは <strong style="color:#f1f5f9;">1時間</strong> で有効期限が切れます。
            </p>
            <div style="text-align:center;margin:28px 0;">
              <a href="{reset_url}"
                 style="background:linear-gradient(135deg,#3b82f6,#6366f1);color:#fff;padding:14px 36px;border-radius:99px;text-decoration:none;font-weight:700;font-size:15px;display:inline-block;">
                パスワードをリセットする →
              </a>
            </div>
            <div style="background:#0f172a;border-radius:8px;padding:12px 16px;margin-top:8px;">
              <p style="color:#475569;font-size:12px;margin:0;word-break:break-all;">
                ボタンが機能しない場合は以下のURLをブラウザに貼り付けてください：<br>
                <a href="{reset_url}" style="color:#3b82f6;">{reset_url}</a>
              </p>
            </div>
            <p style="color:#475569;font-size:12px;margin:20px 0 0;text-align:center;">
              このメールに心当たりがない場合は無視してください。パスワードは変更されません。
            </p>
          </td>
        </tr>
        <tr><td style="padding:16px;text-align:center;">
          <p style="color:#334155;font-size:11px;margin:0;">© AdMu | {datetime.now().year}</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""
    return _send(to, subject, html)


# ============================================================
# ウェルカムメール
# ============================================================
def send_welcome_email(to: str, clinic_name: str) -> bool:
    subject  = f"【AdMu】{clinic_name} 様、ご利用開始ありがとうございます"
    dash_url = APP_BASE_URL
    html = f"""
<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#0b0f1a;font-family:'Helvetica Neue',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0b0f1a;padding:32px 0;">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;">
        <tr>
          <td style="background:#1e293b;border-radius:16px;padding:40px;border:1px solid rgba(255,255,255,0.08);">
            <div style="text-align:center;margin-bottom:28px;">
              <span style="font-size:22px;font-weight:900;color:#3b82f6;">AdMu</span>
              <p style="color:#475569;font-size:11px;letter-spacing:2px;margin:4px 0 0;">無を、極める。</p>
            </div>
            <h2 style="color:#f1f5f9;font-size:22px;text-align:center;margin:0 0 20px;">ご利用開始ありがとうございます 🎉</h2>
            <p style="color:#94a3b8;font-size:14px;line-height:1.8;margin:0 0 24px;">
              {clinic_name} 様のAdMuアカウントが作成されました。<br>
              以下のボタンからダッシュボードにアクセスしてください。
            </p>
            <div style="text-align:center;margin:28px 0;">
              <a href="{dash_url}"
                 style="background:linear-gradient(135deg,#3b82f6,#6366f1);color:#fff;padding:14px 36px;border-radius:99px;text-decoration:none;font-weight:700;font-size:15px;display:inline-block;">
                ダッシュボードを開く →
              </a>
            </div>
            <p style="color:#475569;font-size:12px;text-align:center;margin:16px 0 0;">
              ご不明な点は support@admu.jp までお気軽にご連絡ください。
            </p>
          </td>
        </tr>
        <tr><td style="padding:16px;text-align:center;">
          <p style="color:#334155;font-size:11px;margin:0;">© AdMu | {datetime.now().year}</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""
    return _send(to, subject, html)


# ============================================================
# アラートメール
# ============================================================
def send_alert_email(to: str, subject: str, body: str) -> bool:
    html = f"""
<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#0b0f1a;font-family:'Helvetica Neue',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0b0f1a;padding:32px 0;">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;">
        <tr>
          <td style="background:#1e293b;border-radius:16px;padding:32px;border:1px solid rgba(255,255,255,0.08);">
            <h2 style="color:#f59e0b;font-size:18px;margin:0 0 16px;">⚡ 広告運用アラート</h2>
            <div style="color:#94a3b8;font-size:14px;line-height:1.8;white-space:pre-wrap;">{body}</div>
            <p style="color:#475569;font-size:12px;margin:20px 0 0;">{datetime.now().strftime('%Y-%m-%d %H:%M')} | AdMu 自動通知</p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""
    return _send(to, subject, html)


# ============================================================
# 週次レポートメール（互換性のために残す）
# ============================================================
def send_report_email(to: str, clinic_name: str, summary: dict) -> bool:
    total_cost        = summary.get('total_cost_yen', 0)
    total_clicks      = summary.get('total_clicks', 0)
    total_impressions = summary.get('total_impressions', 0)
    total_conversions = summary.get('total_conversions', 0)
    avg_ctr           = summary.get('avg_ctr', 0)
    prev_cost         = summary.get('prev_cost_yen', 0)
    prev_conversions  = summary.get('prev_conversions', 0)
    prev_ctr          = summary.get('prev_avg_ctr', 0)
    cpa      = round(total_cost / total_conversions) if total_conversions > 0 else 0
    prev_cpa = round(prev_cost  / prev_conversions)  if prev_conversions  > 0 else 0

    def pct_diff(curr, prev):
        if not prev: return None
        return round((curr - prev) / prev * 100, 1)
    def sign(v): return f"+{v}" if v and v > 0 else str(v) if v else "±0"

    ctr_diff = pct_diff(avg_ctr, prev_ctr)
    cpa_diff = pct_diff(cpa, prev_cpa)
    cv_diff  = pct_diff(total_conversions, prev_conversions)

    advice = []
    # --- CTR診断 ---
    if avg_ctr < 2.0:
        advice.append("📌 CTRが低め（整体院目標4%以上）です。広告文のキャッチコピーや表示URLを見直し、「初回無料」「当日予約OK」などのアクションワードを追加してください。")
    elif 2.0 <= avg_ctr < 4.0:
        advice.append("✅ CTRは標準範囲内です。見出しに地域名や症状名を直接入れるテストで更なる改善が狙えます。")
    elif avg_ctr >= 4.0:
        advice.append("🚀 CTRが高水準です！予算を増やすことでさらに患者を獲得できます。日予算上限の引き上げを検討してください。")
    # --- CPA診断 ---
    if cpa > 8000:
        advice.append(f"⚠️ CPA¥{cpa:,}は高めのアラートです。除外KWの追加・LPのファーストビュー・予約ボタンの視認性をご確認ください。")
    elif cpa > 5000:
        advice.append(f"📌 CPA¥{cpa:,}はやや高め。除外KW追加とLPのCTA文言修正で次週にCPA削減を目指しましょう。")
    elif 0 < cpa <= 3000:
        advice.append(f"✅ CPA¥{cpa:,}は優秀な水準です。予算を増やす絶好のタイミングです。")
    # --- CV数診断 ---
    if total_conversions == 0:
        advice.append("🚨 今週はCV（問い合わせ）がゼロです。クリックは発生しているためLP側のフォーム設置・電話番号表示に問題がないかご確認ください。")
    elif total_conversions < 3:
        advice.append(f"↥ CV数{total_conversions:.1f}件と少なめです。LPの「今すぐ予約」前のモーダルやチャットの導入を検討してみてください。")
    elif total_conversions >= 10:
        advice.append(f"🎉 CV数{total_conversions:.0f}件！好調です。リターゲティングキャンペーンへの予算追加で再来院率も向上させましょう。")
    # --- 前週比 ---
    if cv_diff is not None and abs(cv_diff) >= 5:
        emoji = "📈" if cv_diff > 0 else "📉"
        advice.append(f"{emoji} 前週比CV数が{sign(cv_diff)}%{'増加' if cv_diff > 0 else '減少'}しました。{'この好調なトレンドを維持するため、上位キャンペーンの予算を増やしましょう。' if cv_diff > 0 else '先週と比べて患者からの反応が薄いです。広告文またはLPの見出しを見直しましょう。'}")
    if cpa_diff is not None and abs(cpa_diff) >= 10:
        emoji = "⚠️" if cpa_diff > 0 else "✅"
        advice.append(f"{emoji} CPAが前週比{sign(cpa_diff)}%({'上昇' if cpa_diff > 0 else '改善'})です。{'除外KW追加・入札調整で全速改善を目指します。' if cpa_diff > 0 else 'AI自動最適化が機能しています。この勢いを維持しましょう。'}")
    # --- インプレッション ---
    if total_impressions > 0 and total_clicks / total_impressions < 0.015:
        advice.append("💫 広告表示はされているのにクリック率が非常に低いです。拡張テキスト（電話番号・定休日・アクセス方法）の追加をお勧めします。")
    if not advice:
        advice.append("📊 安定的に広告が配信されています。AI自動最適化が進行中です。次週も引き続き状況をモニタリングします。")

    # --- 前週比バッジ ---
    prev_badges = ""
    if prev_cost or prev_conversions:
        cv_badge  = f'<span style="font-size:12px;color:{"#10b981" if (cv_diff or 0)>=0 else "#ef4444"}">🎯 CV {sign(cv_diff)}%</span>'  if cv_diff  is not None else ""
        cpa_badge = f'<span style="font-size:12px;color:{"#10b981" if (cpa_diff or 0)<=0 else "#ef4444"}">💰 CPA {sign(cpa_diff)}%</span>' if cpa_diff is not None else ""
        ctr_badge = f'<span style="font-size:12px;color:{"#10b981" if (ctr_diff or 0)>=0 else "#ef4444"}">📈 CTR {sign(ctr_diff)}%</span>' if ctr_diff is not None else ""
        prev_badges = f"""
        <div style="background:#0f172a;border-radius:8px;padding:10px 14px;margin-top:12px;border:1px solid rgba(255,255,255,0.05);">
          <div style="color:#475569;font-size:10px;letter-spacing:1px;margin-bottom:6px;">↻ 前週比</div>
          <div style="display:flex;gap:16px;flex-wrap:wrap">{cv_badge}{cpa_badge}{ctr_badge}</div>
        </div>"""

    advice_html = "".join([f'<li style="margin-bottom:8px;line-height:1.6">{a}</li>' for a in advice])
    subject = f"【週次レポート】{clinic_name} の広告成績 — {datetime.now().strftime('%Y年%m月%d日')}時点"
    html = f"""
<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#0b0f1a;font-family:'Helvetica Neue',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0b0f1a;padding:32px 0;">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;">
        <tr>
          <td style="background:#1e293b;border-radius:16px 16px 0 0;padding:32px 32px 24px;border:1px solid rgba(255,255,255,0.08);border-bottom:none;">
            <div style="display:flex;justify-content:space-between;align-items:center;">
              <div>
                <div style="font-size:20px;font-weight:800;color:#3b82f6;">AdMu</div>
                <div style="font-size:11px;color:#475569;letter-spacing:2px;">無を、極める。</div>
              </div>
              <span style="background:rgba(16,185,129,0.15);color:#10b981;border:1px solid rgba(16,185,129,0.3);padding:4px 12px;border-radius:99px;font-size:11px;font-weight:700;">週次レポート</span>
            </div>
            <h1 style="color:#f1f5f9;font-size:20px;margin:20px 0 4px;">{clinic_name}</h1>
            <p style="color:#475569;font-size:12px;margin:0;">{datetime.now().strftime('%Y年%m月%d日')} 時点</p>
          </td>
        </tr>
        <tr>
          <td style="background:#111827;padding:24px 32px;border:1px solid rgba(255,255,255,0.08);border-top:none;border-bottom:none;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td width="49%" style="background:#1e293b;border-radius:10px;padding:16px;border:1px solid rgba(255,255,255,0.07);">
                  <div style="color:#475569;font-size:10px;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">💰 総費用</div>
                  <div style="color:#f1f5f9;font-size:22px;font-weight:800;">¥{total_cost:,}</div>
                </td>
                <td width="2%"></td>
                <td width="49%" style="background:#1e293b;border-radius:10px;padding:16px;border:1px solid rgba(255,255,255,0.07);">
                  <div style="color:#475569;font-size:10px;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">🖱 クリック数</div>
                  <div style="color:#10b981;font-size:22px;font-weight:800;">{total_clicks:,}</div>
                </td>
              </tr>
              <tr style="margin-top:12px;"><td colspan="3" style="height:12px;"></td></tr>
              <tr>
                <td width="32%" style="background:#1e293b;border-radius:10px;padding:14px;border:1px solid rgba(255,255,255,0.07);">
                  <div style="color:#475569;font-size:10px;margin-bottom:4px;">📈 CTR</div>
                  <div style="color:#f59e0b;font-size:18px;font-weight:800;">{avg_ctr:.2f}%</div>
                </td>
                <td width="2%"></td>
                <td width="32%" style="background:#1e293b;border-radius:10px;padding:14px;border:1px solid rgba(255,255,255,0.07);">
                  <div style="color:#475569;font-size:10px;margin-bottom:4px;">🎯 CV数</div>
                  <div style="color:#06b6d4;font-size:18px;font-weight:800;">{total_conversions:.1f}件</div>
                </td>
                <td width="2%"></td>
                <td width="32%" style="background:#1e293b;border-radius:10px;padding:14px;border:1px solid rgba(255,255,255,0.07);">
                  <div style="color:#475569;font-size:10px;margin-bottom:4px;">💡 CPA</div>
                  <div style="color:#a78bfa;font-size:18px;font-weight:800;">{'¥' + f'{cpa:,}' if cpa > 0 else '—'}</div>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td style="background:#111827;padding:0 32px 24px;border:1px solid rgba(255,255,255,0.08);border-top:none;border-bottom:none;">
            <div style="background:rgba(59,130,246,0.08);border:1px solid rgba(59,130,246,0.25);border-radius:10px;padding:20px;">
              <div style="color:#3b82f6;font-weight:700;font-size:14px;margin-bottom:12px;">⚡ AIからのアドバイス</div>
              <ul style="margin:0;padding-left:16px;color:#94a3b8;font-size:13px;line-height:1.7;">{advice_html}</ul>
            </div>
          </td>
        </tr>
        <tr>
          <td style="background:#0d1321;border-radius:0 0 16px 16px;padding:20px 32px;border:1px solid rgba(255,255,255,0.08);border-top:1px solid rgba(255,255,255,0.05);">
            <p style="color:#475569;font-size:12px;margin:0;text-align:center;">
              このメールはAdMuから自動送信されています。{datetime.now().strftime('%Y-%m-%d %H:%M')}
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""
    return _send(to, subject, html)
