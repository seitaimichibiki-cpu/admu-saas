"""
monitor.py - リアルタイム監視スケジューラ（APScheduler）
"""
import os
import db
import email_notifier
import line_notifier
from bid_adjuster import run_bid_adjustment
from ads_client import AdsClient
from datetime import datetime

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    SCHEDULER_AVAILABLE = True
except ImportError:
    SCHEDULER_AVAILABLE = False
    print("[Monitor] APScheduler未インストール。スケジューラ機能無効。")

_scheduler = None
_monitor_status = {
    "running": False,
    "last_check": None,
    "last_bid_run": None,
    "last_daily_report": None,
    "checks_today": 0,
    "budget_alerts_today": 0,
}


def _get_account_and_notify_config(clinic_id: int):
    clinic = db.get_clinic(clinic_id)
    if clinic and clinic.get("plan_status") == "suspended":
        print(f"[Monitor] clinic_id={clinic_id} は利用停止(suspended)のため処理をスキップします。")
        return {}
    acc = db.get_ads_account(clinic_id)
    return acc or {}


def _check_campaigns(clinic_id: int):
    """5分毎: 配信状態チェック・異常検知 + 🔒 予算消化率安全装置"""
    global _monitor_status
    acc = _get_account_and_notify_config(clinic_id)
    if not acc:
        return
    try:
        client = AdsClient(acc)
        campaigns = client.list_campaigns()
    except Exception as e:
        error_msg = str(e)
        print(f"[Monitor] API接続エラー clinic_id={clinic_id}: {error_msg}")
        # API認証・トークンエラーの検知
        if any(err in error_msg for err in ["invalid_grant", "UNAUTHENTICATED", "DEVELOPER_TOKEN", "PERMISSION_DENIED"]):
            msg = f"🚨 API認証・トークンエラー発生 (clinic_id={clinic_id})\n{error_msg[:100]}...\n至急、トークンの有効期限やアカウントの停止状態を確認してください。"
            db.create_alert(clinic_id, "API認証エラー: Google Adsへの接続に失敗しました", level="ERROR")
            
            # 管理者へ緊急LINE通知
            admin_line = os.environ.get("LINE_DEFAULT_USER_ID", "")
            channel_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
            if admin_line and channel_token:
                try:
                    import line_notifier
                    line_notifier.send_text(channel_token, admin_line, msg)
                except Exception as ne:
                    print(f"[Monitor] 管理者へのLINE通知に失敗: {ne}")
        return

    abnormal = []
    budget_warnings = []

    for c in campaigns:
        ctr = c.get("ctr", 0)
        cvr = c.get("cvr", 0)
        status = c.get("status", "")

        if status == "PAUSED":
            continue

        # --- 異常検知 ---
        if ctr < 0.5:
            abnormal.append(f"{c['name']}: CTR低下 ({ctr:.2f}%)")
            db.create_alert(clinic_id, f"CTR急落: {c['name']} CTR={ctr:.2f}%", level="WARNING")
        if cvr < 0.3:
            abnormal.append(f"{c['name']}: CVR低下 ({cvr:.2f}%)")
            db.create_alert(clinic_id, f"CVR急落: {c['name']} CVR={cvr:.2f}%", level="WARNING")

        # --- 🔒 安全装置: 予算消化率チェック ---
        budget_micros = c.get("budget_micros", 0)
        cost_micros = c.get("cost_micros", 0)
        if budget_micros > 0:
            usage_pct = (cost_micros / budget_micros) * 100
            if usage_pct >= 95:
                msg = (
                    f"🚨 予算枯渇警告: {c['name']}\n"
                    f"消化率 {usage_pct:.1f}%（本日予算¥{budget_micros//1_000_000:,} のほぼ全額消化）\n"
                    f"配信継続のため予算追加を推奨します。"
                )
                budget_warnings.append(msg)
                db.create_alert(clinic_id, f"予算枯渇: {c['name']} 消化率{usage_pct:.1f}%", level="ERROR")
            elif usage_pct >= 85:
                msg = (
                    f"⚠️ 予算警告: {c['name']}\n"
                    f"消化率 {usage_pct:.1f}% — まもなく予算上限に達します"
                )
                budget_warnings.append(msg)
                db.create_alert(clinic_id, f"予算警告: {c['name']} 消化率{usage_pct:.1f}%", level="WARNING")

    token = acc.get("line_channel_token", "")
    uid = acc.get("line_user_id", "")

    # LINE通知（異常検知時）
    if abnormal and token and uid:
        for msg in abnormal:
            line_notifier.send_alert(token, uid, "WARNING", msg)

    # LINE通知（予算警告）
    if budget_warnings and token and uid:
        for msg in budget_warnings:
            level = "ERROR" if "枯渇" in msg else "WARNING"
            line_notifier.send_alert(token, uid, level, msg)
        _monitor_status["budget_alerts_today"] = _monitor_status.get("budget_alerts_today", 0) + len(budget_warnings)

    # メール通知（95%超の緊急アラートのみ）
    critical = [w for w in budget_warnings if "枯渇" in w]
    if critical:
        notify_email = acc.get("notification_email", "")
        if notify_email:
            body = "\n\n".join(critical) + "\n\n今すぐダッシュボードを確認し、予算追加または不要なキャンペーンの一時停止を検討してください。"
            email_notifier.send_alert_email(
                notify_email,
                "【緊急】予算枯渇アラート - AdMu 広告AI",
                body
            )

    _monitor_status["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _monitor_status["checks_today"] = _monitor_status.get("checks_today", 0) + 1
    total_w = len(abnormal) + len(budget_warnings)
    print(f"[Monitor] キャンペーンチェック完了 clinic_id={clinic_id} 異常={total_w}件")


def _run_bid_adjustment(clinic_id: int):
    """1時間毎: 入札調整"""
    global _monitor_status
    acc = _get_account_and_notify_config(clinic_id)
    if not acc:
        return
    logs = run_bid_adjustment(clinic_id, acc)
    if logs:
        token = acc.get("line_channel_token", "")
        uid = acc.get("line_user_id", "")
        if token and uid:
            line_notifier.send_bid_adjustment_report(token, uid, logs)
    _monitor_status["last_bid_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[Monitor] 入札調整完了 clinic_id={clinic_id} 実行ルール={len(logs)}件")


def _send_daily_report(clinic_id: int):
    """毎朝9時: 日次レポートLINE送信"""
    global _monitor_status
    acc = _get_account_and_notify_config(clinic_id)
    if not acc:
        return
    token = acc.get("line_channel_token", "")
    uid = acc.get("line_user_id", "")
    if not token or not uid:
        return

    client = AdsClient(acc)
    perf_list = client.get_performance_series(days=1)
    latest = perf_list[-1] if perf_list else {}
    alerts = db.list_alerts(clinic_id, limit=10)
    new_alerts = [a for a in alerts if not a.get("notified")]

    line_notifier.send_daily_report(token, uid, {
        "performance": latest,
        "alerts": new_alerts,
    })
    _monitor_status["last_daily_report"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[Monitor] 日次レポート送信完了 clinic_id={clinic_id}")


def _send_weekly_report(clinic_id: int):
    """毎週月曜8時: 週次レポート（メール + LINE 同時送信）"""
    acc = _get_account_and_notify_config(clinic_id)
    if not acc:
        return

    clinic = db.get_clinic(clinic_id) or {}
    clinic_name = clinic.get("name", f"Clinic#{clinic_id}")

    try:
        client = AdsClient(acc)
        perf_list = client.get_performance_series(days=7)
        total_cost = sum(p.get("cost_micros", 0) for p in perf_list) / 1_000_000
        total_clicks = sum(p.get("clicks", 0) for p in perf_list)
        total_impressions = sum(p.get("impressions", 0) for p in perf_list)
        total_conversions = sum(p.get("conversions", 0) for p in perf_list)
        avg_ctr = (total_clicks / total_impressions * 100) if total_impressions else 0
        cpa = round(total_cost / total_conversions) if total_conversions > 0 else 0
        summary = {
            "total_cost_yen": int(total_cost),
            "total_clicks": total_clicks,
            "total_impressions": total_impressions,
            "total_conversions": total_conversions,
            "avg_ctr": round(avg_ctr, 2),
            "cpa": cpa,
        }
    except Exception as e:
        summary = {"error": str(e)}

    # --- メール送信 ---
    notify_email = acc.get("notification_email", "")
    if notify_email:
        email_notifier.send_report_email(notify_email, clinic_name, summary)
        print(f"[Monitor] 週次メール送信完了 clinic_id={clinic_id} to={notify_email}")

    # --- LINE送信（同時送信）---
    token = acc.get("line_channel_token", "")
    uid = acc.get("line_user_id", "")
    if token and uid and "error" not in summary:
        line_notifier.send_weekly_report(token, uid, clinic_name, summary)
        print(f"[Monitor] 週次LINE送信完了 clinic_id={clinic_id}")


def _send_system_daily_report():
    """毎朝9時: システム全体の稼働サマリーを管理者LINEへ送信"""
    admin_line = os.environ.get("LINE_DEFAULT_USER_ID", "")
    channel_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not admin_line or not channel_token:
        return

    clinics = db.list_clinics()
    active_count = sum(1 for c in clinics if c.get("plan_status") == "active")
    pending_count = sum(1 for c in clinics if c.get("plan_status") == "pending")

    msg = f"""⚙️ AdMu システム稼働レポート
{datetime.now().strftime("%Y年%m月%d日")}
━━━━━━━━━━━━━━
✅ アクティブ院数: {active_count}
⏳ 承認待ち(Pending): {pending_count}
🏥 総登録院数: {len(clinics)}
━━━━━━━━━━━━━━
by AdMu System Monitor"""

    line_notifier.send_text(channel_token, admin_line, msg)
    print("[Monitor] システム日次レポート送信完了")


def _run_auto_negative_keyword_scan(clinic_id: int):
    """毎週水曜3時: 検索語句を自動スキャンし除外KW候補をDBに追加 ②"""
    acc = _get_account_and_notify_config(clinic_id)
    if not acc:
        return

    try:
        client = AdsClient(acc)
        # コスト¥500以上使ってコンバージョン0の語句をムダと判定
        wasted_terms = [
            t for t in client.get_search_term_report(
                days=30, min_cost_yen=500, max_conversions=0
            ) if t["is_wasted"]
        ]

        newly_added = []
        for term in wasted_terms:
            kw = term["search_term"]
            # すでに除外リストにあればスキップ
            existing = db.list_negative_keywords(clinic_id)
            if any(nk["keyword"] == kw for nk in existing):
                continue
            db.add_negative_keyword(
                clinic_id, kw, "BROAD",
                campaign_id=None, source="auto_scan"
            )
            db.create_alert(
                clinic_id,
                f"除外KW自動追加: 「{kw}」 費用¥{term['cost_yen']:,} CV={term['conversions']}",
                level="INFO"
            )
            newly_added.append(term)

        if newly_added:
            # LINE通知
            token = acc.get("line_channel_token", "")
            uid   = acc.get("line_user_id", "")
            if token and uid:
                summary = "\n".join(
                    [f"・「{t['search_term']}」(費用¥{t['cost_yen']:,}、CV{t['conversions']})"]
                    for t in newly_added[:5]  # 最大5件表示
                )
                msg = (
                    f"🔍 除外KW自動スキャン完了\n"
                    f"{len(newly_added)}件のムダ語句を除外リストに追加しました。\n\n"
                    f"{summary}\n\n"
                    f"AdMuダッシュボードで詳細を確認してください。"
                )
                line_notifier.send_text(token, uid, msg)

        print(f"[Monitor] 検索語句自動スキャン完了 clinic_id={clinic_id} 新規除外={len(newly_added)}件")

    except Exception as e:
        print(f"[Monitor] 検索語句自動スキャンエラー clinic_id={clinic_id}: {e}")


def _run_cleanup():
    """週1回曜深夜: 古いログやアラートを削除"""
    try:
        res = db.cleanup_old_logs(365)
        print(f"[Monitor] 自動クリーンアップ完了: {res}")
    except Exception as e:
        print(f"[Monitor] 自動クリーンアップ失敗: {e}")

def start_scheduler():
    """スケジューラを起動"""
    global _scheduler, _monitor_status
    if not SCHEDULER_AVAILABLE:
        print("[Monitor] APScheduler利用不可: スケジューラ起動スキップ")
        return
    if _scheduler and _scheduler.running:
        print("[Monitor] スケジューラ起動済み")
        return

    clinics = db.list_clinics()
    _scheduler = BackgroundScheduler(timezone="Asia/Tokyo")

    for clinic in clinics:
        cid = clinic["id"]
        _scheduler.add_job(
            _check_campaigns, IntervalTrigger(minutes=5),
            id=f"check_{cid}", args=[cid], replace_existing=True
        )
        _scheduler.add_job(
            _run_bid_adjustment, IntervalTrigger(hours=1),
            id=f"bid_{cid}", args=[cid], replace_existing=True
        )
        _scheduler.add_job(
            _send_daily_report, CronTrigger(hour=9, minute=0),
            id=f"daily_{cid}", args=[cid], replace_existing=True
        )
        _scheduler.add_job(
            _send_weekly_report, CronTrigger(day_of_week='mon', hour=8, minute=0),
            id=f"weekly_{cid}", args=[cid], replace_existing=True
        )
        # ② 検索語句自動スキャン（毎週水曜 3:00）
        _scheduler.add_job(
            _run_auto_negative_keyword_scan, CronTrigger(day_of_week='wed', hour=3, minute=0),
            id=f"nkw_scan_{cid}", args=[cid], replace_existing=True
        )

    # システム全体の日次稼働レポート（毎日 9:00 管理者宛）
    _scheduler.add_job(
        _send_system_daily_report, CronTrigger(hour=9, minute=0),
        id="system_daily_report", replace_existing=True
    )

    # システム全体のクリーンアップジョブ（日曜日 3:00）
    _scheduler.add_job(
        _run_cleanup, CronTrigger(day_of_week='sun', hour=3, minute=0),
        id="system_cleanup", replace_existing=True
    )

    _scheduler.start()
    _monitor_status["running"] = True
    print(f"[Monitor] スケジューラ起動完了 (クリニック数={len(clinics)})")


def stop_scheduler():
    global _scheduler, _monitor_status
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _monitor_status["running"] = False
        print("[Monitor] スケジューラ停止")


def get_status() -> dict:
    return {**_monitor_status}


def trigger_check_now(clinic_id: int):
    _check_campaigns(clinic_id)


def trigger_bid_now(clinic_id: int):
    _run_bid_adjustment(clinic_id)


def register_clinic_jobs(clinic_id: int):
    """新規クリニック登録時にサーバー再起動なしでスケジューラにジョブを動的追加する"""
    global _scheduler
    if not _scheduler or not _scheduler.running:
        print(f"[Monitor] スケジューラ未起動のためジョブ登録をスキップ (clinic_id={clinic_id})")
        return
    _scheduler.add_job(
        _check_campaigns, IntervalTrigger(minutes=5),
        id=f"check_{clinic_id}", args=[clinic_id], replace_existing=True
    )
    _scheduler.add_job(
        _run_bid_adjustment, IntervalTrigger(hours=1),
        id=f"bid_{clinic_id}", args=[clinic_id], replace_existing=True
    )
    _scheduler.add_job(
        _send_daily_report, CronTrigger(hour=9, minute=0),
        id=f"daily_{clinic_id}", args=[clinic_id], replace_existing=True
    )
    _scheduler.add_job(
        _send_weekly_report, CronTrigger(day_of_week='mon', hour=8, minute=0),
        id=f"weekly_{clinic_id}", args=[clinic_id], replace_existing=True
    )
    _scheduler.add_job(
        _run_auto_negative_keyword_scan, CronTrigger(day_of_week='wed', hour=3, minute=0),
        id=f"nkw_scan_{clinic_id}", args=[clinic_id], replace_existing=True
    )
    print(f"[Monitor] 新規クリニックのジョブを動的登録完了 (clinic_id={clinic_id})")


def unregister_clinic_jobs(clinic_id: int):
    """クリニック解約/停止時にスケジューラからジョブを動的削除する"""
    global _scheduler
    if not _scheduler or not _scheduler.running:
        return
    for job_prefix in ["check_", "bid_", "daily_", "weekly_", "nkw_scan_"]:
        job_id = f"{job_prefix}{clinic_id}"
        try:
            _scheduler.remove_job(job_id)
        except Exception:
            pass  # ジョブが存在しない場合は無視
    print(f"[Monitor] クリニックのジョブを削除完了 (clinic_id={clinic_id})")
