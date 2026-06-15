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

        # --- アセット（ビジネスの名前・ロゴ）不承認チェック ---
        try:
            disapproved_assets = client.check_asset_approval_statuses()
            if disapproved_assets:
                # すでに最近同じメッセージのアラートがあるか確認（過去30日以内に同一の本人確認警告があればスキップ）
                existing_alerts = db.list_alerts(clinic_id, limit=30)
                has_alert = any("本人確認" in a.get("message", "") for a in existing_alerts)
                if not has_alert:
                    asset_names = ", ".join([
                        f"「{a['business_name']}」" if a.get("business_name") else "ビジネスの名前/ロゴ"
                        for a in disapproved_assets
                    ])
                    msg = (
                        f"🚨 {asset_names}アセットがGoogle広告側で不承認になっています。"
                        "広告主の本人確認（適格性確認）が未完了である可能性があります。"
                        "Google広告の管理画面から本人確認手続きを完了させてください。"
                    )
                    db.create_alert(clinic_id, msg, level="WARNING")
                    
                    # LINE通知
                    token = acc.get("line_channel_token", "")
                    uid = acc.get("line_user_id", "")
                    if token and uid:
                        try:
                            import line_notifier
                            line_notifier.send_text(
                                token, uid, 
                                f"【AdMu警告】\n{msg}\n\n※公的書類（登記簿謄本や運転免許証など）のアップロードが必要なため、所有者様ご本人による手続きが必要です。"
                            )
                        except Exception as ne:
                            print(f"[Monitor] 本人確認LINE通知失敗: {ne}")
        except Exception as asset_err:
            print(f"[Monitor] アセット審査状況チェック中にエラー clinic_id={clinic_id}: {asset_err}")

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

    # ─── 🔒 月間予算自動セーフティブレーキ ───
    safety_enabled = acc.get("budget_safety_brake_enabled", 1) == 1
    monthly_budget = acc.get("monthly_budget_yen", 0)

    if safety_enabled and monthly_budget > 0:
        try:
            total_month_cost_micros = client.get_this_month_cost()
            total_month_cost_yen = total_month_cost_micros / 1_000_000
            
            if total_month_cost_yen >= monthly_budget:
                paused_campaigns = []
                for c in campaigns:
                    g_id = c.get("id")  # google_campaign_id
                    c_name = c.get("name")
                    status = c.get("status")
                    if status == "ENABLED" and g_id:
                        client.update_campaign_status(g_id, "PAUSED")
                        with db.get_conn() as conn:
                            conn.execute(
                                "UPDATE campaigns SET status='PAUSED', updated_at=? WHERE google_campaign_id=? AND clinic_id=?",
                                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), str(g_id), clinic_id)
                            )
                        paused_campaigns.append(c_name)
                
                if paused_campaigns:
                    # 過去24時間以内に既にブレーキアラートを送信済みかチェック（二重送信防止）
                    has_recent_brake_alert = False
                    try:
                        recent_alerts = db.list_alerts(clinic_id, limit=20)
                        for alert in recent_alerts:
                            msg = alert.get("message", "")
                            if "予算自動ブレーキ作動" in msg:
                                created_at_str = alert.get("created_at")
                                if created_at_str:
                                    try:
                                        if isinstance(created_at_str, str):
                                            # SQLite: YYYY-MM-DD HH:MM:SS
                                            clean_str = created_at_str.replace(" ", "T").split(".")[0]
                                            created_at = datetime.fromisoformat(clean_str)
                                        else:
                                            created_at = created_at_str
                                        
                                        if (datetime.now() - created_at).total_seconds() < 24 * 3600:
                                            has_recent_brake_alert = True
                                            break
                                    except Exception as parse_err:
                                        # パースに失敗した場合は、今日の日付文字列が含まれるかだけでフォールバック判定
                                        today_str = datetime.now().strftime("%Y-%m-%d")
                                        if today_str in str(created_at_str):
                                            has_recent_brake_alert = True
                                            break
                    except Exception as ae:
                        print(f"[Monitor] 二重送信チェックエラー (clinic_id={clinic_id}): {ae}")

                    if not has_recent_brake_alert:
                        db.create_alert(
                            clinic_id, 
                            f"予算自動ブレーキ作動: 月間予算上限(¥{monthly_budget:,})を超過したため配信を自動停止しました。", 
                            level="ERROR"
                        )
                        
                        stop_msg = (
                            f"🚨【予算自動ブレーキ作動】\n"
                            f"当月の総消化コスト（¥{int(total_month_cost_yen):,}）が設定月間予算（¥{monthly_budget:,}）の上限に達したため、以下のキャンペーンの配信を自動停止（PAUSE）しました。\n"
                            f"・" + "\n・".join(paused_campaigns) + "\n\n"
                            f"配信を再開するにはダッシュボードから月間予算を増やすか、手動でキャンペーンを再起動（ENABLED）してください。"
                        )
                        
                        is_mock_or_demo = acc.get("mock_mode", 1) == 1 or acc.get("is_demo") == 1
                        
                        line_token = acc.get("line_channel_token", "")
                        line_uid = acc.get("line_user_id", "")
                        if line_token and line_uid and not is_mock_or_demo:
                            try:
                                import line_notifier
                                line_notifier.send_text(line_token, line_uid, stop_msg)
                            except Exception as ne:
                                print(f"[Monitor] ブレーキLINE通知失敗: {ne}")
                                
                        notify_email = acc.get("notification_email", "")
                        if notify_email and not is_mock_or_demo:
                            try:
                                import email_notifier
                                email_notifier.send_alert_email(
                                    notify_email,
                                    "【重要】予算自動ブレーキ作動による配信停止 - AdMu 広告AI",
                                    stop_msg
                                )
                            except Exception as ee:
                                print(f"[Monitor] ブレーキメール通知失敗: {ee}")
                    else:
                        print(f"[Monitor] 過去24時間以内にブレーキアラート送信済のため、メール/LINE/アラート作成をスキップします (clinic_id={clinic_id})")
        except Exception as brake_err:
            print(f"[Monitor] 月間予算セーフティブレーキエラー clinic_id={clinic_id}: {brake_err}")

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
            # Google広告 API に直接 Push する（自動適用）
            push_kws = [{"keyword": t["search_term"], "match_type": "BROAD"} for t in newly_added]
            try:
                push_res = client.push_negative_keywords(push_kws)
                print(f"[Monitor] Google広告への自動除外KW適用結果: {push_res}")
                
                # DB側も適用済み (applied = 1) に更新する
                with db.get_conn() as conn:
                    for t in newly_added:
                        conn.execute(
                            "UPDATE negative_keywords SET applied=1 WHERE clinic_id=? AND keyword=?",
                            (clinic_id, t["search_term"])
                        )
                    conn.commit()
            except Exception as e_push:
                print(f"[Monitor] Google広告への自動除外KW適用エラー（DBには記録済み）: {e_push}")

            # LINE通知
            token = acc.get("line_channel_token", "")
            uid   = acc.get("line_user_id", "")
            if token and uid:
                summary = "\n".join(
                    [f"・「{t['search_term']}」(費用¥{t['cost_yen']:,}、CV{t['conversions']})"]
                    for t in newly_added[:5]  # 最大5件表示
                )
                msg = (
                    f"🔍 除外KW自動スキャン・適用完了\n"
                    f"{len(newly_added)}件のムダ語句を除外リストに追加し、Google広告キャンペーンへ自動反映しました。\n\n"
                    f"{summary}\n\n"
                    f"AdMuダッシュボードで詳細を確認してください。"
                )
                line_notifier.send_text(token, uid, msg)

        print(f"[Monitor] 検索語句自動スキャン完了 clinic_id={clinic_id} 新規除外={len(newly_added)}件")

    except Exception as e:
        print(f"[Monitor] 検索語句自動スキャンエラー clinic_id={clinic_id}: {e}")


def _run_auto_exclusion_area_scan(clinic_id: int):
    """
    毎週水曜4時: コンバージョン見込みのない無駄地域（患者が殆ど来ない遠方地域）を自動検出・除外ターゲット登録 ⑤
    """
    import math
    import requests as rq

    acc = _get_account_and_notify_config(clinic_id)
    if not acc:
        return

    # デフォルトの院の座標: 藤枝市田沼1-19-7
    CLINIC_LAT = 34.868
    CLINIC_LON = 138.257

    def haversine_km(lat1, lon1, lat2, lon2):
        R = 6371
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
        return R * 2 * math.asin(math.sqrt(a))

    def geocode_address(address: str) -> tuple:
        try:
            url = f"https://msearch.gsi.go.jp/address-search/AddressSearch?q={rq.utils.quote(address)}"
            resp = rq.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if data and len(data) > 0:
                    coords = data[0].get('geometry', {}).get('coordinates', [])
                    if len(coords) >= 2:
                        return float(coords[1]), float(coords[0])  # lat, lon
        except Exception:
            pass
        return None, None

    try:
        # 患者データを取得
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT address_pref, address_city FROM logiction_patients "
                "WHERE clinic_id=? AND address_city IS NOT NULL AND address_city != ''",
                (clinic_id,)
            ).fetchall()

        if not rows:
            print(f"[Monitor] 患者データがないため無駄地域スキャンをスキップします。clinic_id={clinic_id}")
            return

        # 市区町村ごとの患者数を集計
        city_counts = {}
        for pref, city in rows:
            city_name = city.strip()
            full_addr = f"{pref or '静岡県'}{city_name}"
            city_counts[full_addr] = city_counts.get(full_addr, 0) + 1

        total_patients = len(rows)
        wasted_areas = []

        # 院からの距離が20km以上かつ患者割合が1.5%未満を無駄地域とする
        for addr, count in city_counts.items():
            lat, lon = geocode_address(addr)
            if lat and lon:
                dist = haversine_km(CLINIC_LAT, CLINIC_LON, lat, lon)
                pct = (count / total_patients) * 100
                if dist >= 20.0 and pct < 1.5:
                    wasted_areas.append({
                        "name": addr,
                        "distance_km": round(dist, 1),
                        "patient_count": count,
                        "percentage": round(pct, 2)
                    })

        if not wasted_areas:
            print(f"[Monitor] 無駄地域は検出されませんでした clinic_id={clinic_id}")
            return

        client = AdsClient(acc)
        campaigns = client.list_campaigns()
        active_campaigns = [c for c in campaigns if c.get("status") in ("ENABLED", "PAUSED")]

        if not active_campaigns:
            print(f"[Monitor] 設定対象のキャンペーンが見つかりません clinic_id={clinic_id}")
            return

        applied_count = 0
        excluded_details = []

        for area in wasted_areas:
            # 地名からGeo Target IDを解決
            res_names = client.suggest_geo_target_constants([area["name"]])
            if not res_names:
                print(f"[Monitor] 地域IDが解決できませんでした: {area['name']}")
                continue

            geo_res_name = res_names[0]
            geo_id = geo_res_name.split("/")[-1]

            for camp in active_campaigns:
                camp_id = camp.get("id")
                if not camp_id:
                    continue

                res = client.exclude_campaign_location(camp_id, geo_id)
                if res.get("success"):
                    applied_count += 1
                    excluded_details.append(f"・{camp.get('name')}: {area['name']} (距離 {area['distance_km']}km, 患者数 {area['patient_count']}名, 割合 {area['percentage']}%)")
                    db.create_alert(
                        clinic_id,
                        f"無駄地域自動除外: キャンペーン「{camp.get('name')}」から「{area['name']}」（距離{area['distance_km']}km、CV率{area['percentage']}%）を除外設定しました。",
                        level="INFO"
                    )

        if applied_count > 0:
            # LINE通知
            token = acc.get("line_channel_token", "")
            uid   = acc.get("line_user_id", "")
            if token and uid:
                summary = "\n".join(excluded_details[:5]) # 最大5件表示
                msg = (
                    f"🛡️ AI Feed Guard: 無駄エリア自動除外完了\n"
                    f"患者データ分析により、来院見込みの低い遠方地域({len(wasted_areas)}箇所)を広告配信対象から自動的に除外設定しました。\n\n"
                    f"【除外設定詳細】\n"
                    f"{summary}\n\n"
                    f"CPA改善と無駄クリック費用の削減に貢献します。詳細はAdMuダッシュボードを確認してください。"
                )
                line_notifier.send_text(token, uid, msg)

        print(f"[Monitor] 無駄地域自動スキャン完了 clinic_id={clinic_id} 除外適用数={applied_count}件")

    except Exception as e:
        print(f"[Monitor] 無駄地域自動スキャンエラー clinic_id={clinic_id}: {e}")


def _run_auto_ltv_upload_scan(clinic_id: int):
    """LTVデータのオフラインコンバージョン値自動Push同期"""
    acc = _get_account_and_notify_config(clinic_id)
    if not acc:
        return
    
    action_id = acc.get("ltv_conversion_action_id")
    if not action_id:
        print(f"[LTV Sync] clinic_id={clinic_id}: ltv_conversion_action_id が設定されていないため自動同期をスキップします。")
        return

    try:
        client = AdsClient(acc)
    except Exception as e:
        print(f"[LTV Sync] AdsClientの初期化に失敗しました: {e}")
        return

    # Logiction患者データのうち、GCLIDが存在し、かつLTV(ltv_yen)が0より大きい患者を取得
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT gclid, ltv_yen, first_visit_date FROM logiction_patients "
            "WHERE clinic_id=? AND gclid IS NOT NULL AND gclid != '' AND ltv_yen > 0",
            (clinic_id,)
        ).fetchall()

    if not rows:
        print(f"[LTV Sync] clinic_id={clinic_id}: 同期対象のLTVデータがありません。")
        return

    success_count = 0
    failed_count = 0

    print(f"[LTV Sync] clinic_id={clinic_id}: {len(rows)}件のLTVデータを同期中...")

    for gclid, ltv_yen, first_visit in rows:
        if not first_visit:
            first_visit = datetime.now().strftime("%Y-%m-%d")
        
        cv_time = f"{first_visit} 12:00:00+09:00"

        try:
            res = client.upload_offline_conversion_value(
                gclid=gclid,
                conversion_action_id=action_id,
                conversion_time_str=cv_time,
                value=float(ltv_yen)
            )
            if res.get("success"):
                success_count += 1
            else:
                failed_count += 1
        except Exception as upload_err:
            print(f"[LTV Sync] CVアップロードエラー (GCLID: {gclid}): {upload_err}")
            failed_count += 1

    # 同期結果をアラートログに記録
    if success_count > 0:
        db.create_alert(
            clinic_id, 
            f"LTVコンバージョン自動同期完了: {success_count}件のLTVデータをGoogle広告に反映しました。(失敗: {failed_count}件)", 
            level="INFO"
        )
    print(f"[LTV Sync] 同期完了 clinic_id={clinic_id} 成功={success_count} 失敗={failed_count}")


def _run_cleanup():
    """週1回曜深夜: 古いログやアラートを削除"""
    try:
        res = db.cleanup_old_logs(365)
        print(f"[Monitor] 自動クリーンアップ完了: {res}")
    except Exception as e:
        print(f"[Monitor] 自動クリーンアップ失敗: {e}")


def _collect_performance_data(clinic_id: int):
    """
    毎日深夜2:00: 契約クリニックの広告パフォーマンスデータを収集してDBに蓄積。

    蓄積データ活用例:
    - 管理者(石川さん)がクリニック横断でCTR/CVR/コスト推移を分析可能
    - 整体院業界のベンチマーク算出（平均CTR・CPA等）
    - 高パフォーマンスクリニックのベストプラクティス抽出
    - 低パフォーマンスクリニックへの早期アラート強化
    """
    acc = _get_account_and_notify_config(clinic_id)
    if not acc or acc.get("mock_mode", 1):
        # モックモード（未設定）のクリニックはスキップ
        return

    try:
        client = AdsClient(acc)
        # 前日分（1日）のパフォーマンスデータを取得
        perf_list = client.get_performance_series(days=1)
        if not perf_list:
            return

        today = datetime.now().strftime("%Y-%m-%d")
        campaigns = db.list_campaigns(clinic_id)
        camp_map = {c.get("google_campaign_id"): c["id"] for c in campaigns if c.get("google_campaign_id")}

        saved = 0
        for perf in perf_list:
            g_camp_id = str(perf.get("campaign_id", ""))
            local_camp_id = camp_map.get(g_camp_id)
            db.insert_performance(clinic_id, {
                "campaign_id": local_camp_id,
                "date": perf.get("date", today),
                "impressions": int(perf.get("impressions", 0)),
                "clicks": int(perf.get("clicks", 0)),
                "ctr": float(perf.get("ctr", 0)),
                "avg_cpc_micros": int(perf.get("avg_cpc_micros", 0)),
                "cost_micros": int(perf.get("cost_micros", 0)),
                "conversions": float(perf.get("conversions", 0)),
                "cvr": float(perf.get("cvr", 0)),
            })
            saved += 1

        print(f"[Monitor] パフォーマンス自動収集完了 clinic_id={clinic_id} {saved}件保存")

    except Exception as e:
        print(f"[Monitor] パフォーマンス自動収集エラー clinic_id={clinic_id}: {e}")


def _run_onboarding_followup_check():
    """毎朝10:00: 登録から3日・7日後も未完了のクリニックに自動フォローアップメールを送信"""
    try:
        from datetime import timedelta
        now = datetime.now()
        today = now.date()

        with db.get_conn() as conn:
            rows = conn.execute("""
                SELECT o.clinic_id, o.step_reached, o.completed,
                       o.gemini_set, o.google_ads_set, o.persona_set,
                       o.started_at, o.completed_at,
                       c.name as clinic_name, u.email
                FROM onboarding_progress o
                JOIN clinics c ON o.clinic_id = c.id
                LEFT JOIN users u ON u.clinic_id = c.id AND u.role = 'admin'
                WHERE (o.completed = 0 OR o.gemini_set = 0)
                  AND u.email IS NOT NULL
            """).fetchall()

        sent_count = 0
        for r in rows:
            email = r["email"]
            if not email:
                continue

            # started_at から経過日数を計算
            started_raw = r["started_at"]
            if not started_raw:
                continue
            try:
                # PostgreSQLはdatetime型、SQLiteは文字列
                if isinstance(started_raw, str):
                    from datetime import datetime as dt
                    started = dt.fromisoformat(started_raw.replace("Z", "+00:00")).date()
                else:
                    started = started_raw.date() if hasattr(started_raw, 'date') else today
            except Exception:
                continue

            elapsed_days = (today - started).days

            # 送信条件: 3日後または7日後
            if elapsed_days not in (3, 7):
                continue

            # 7日後は gemini未設定の院のみに絞る（Gemini設定済み院は除外）
            if elapsed_days == 7 and r["gemini_set"]:
                continue

            missing = []
            if not r["gemini_set"]:     missing.append("gemini")
            if not r["google_ads_set"]: missing.append("google_ads")
            if not r["persona_set"]:    missing.append("persona")

            ok = email_notifier.send_onboarding_followup_email(
                to=email,
                clinic_name=r["clinic_name"],
                step_reached=r["step_reached"] or 1,
                missing=missing
            )
            if ok:
                sent_count += 1
                print(f"[Monitor] フォローアップメール送信: {r['clinic_name']} ({email}) 登録{elapsed_days}日後")

        print(f"[Monitor] オンボーディング自動フォローアップ完了: {sent_count}件送信")
    except Exception as e:
        print(f"[Monitor] オンボーディング自動フォローアップエラー: {e}")

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
        # ⑤ 無駄地域自動除外スキャン（毎週水曜 4:00）
        _scheduler.add_job(
            _run_auto_exclusion_area_scan, CronTrigger(day_of_week='wed', hour=4, minute=0),
            id=f"area_exclude_scan_{cid}", args=[cid], replace_existing=True
        )
        # ③ 広告パフォーマンス自動収集（毎日深夜2:00）
        _scheduler.add_job(
            _collect_performance_data, CronTrigger(hour=2, minute=0),
            id=f"perf_collect_{cid}", args=[cid], replace_existing=True
        )
        # ④ LTVオフラインコンバージョン自動同期（毎日深夜3:00）
        _scheduler.add_job(
            _run_auto_ltv_upload_scan, CronTrigger(hour=3, minute=0),
            id=f"ltv_sync_{cid}", args=[cid], replace_existing=True
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

    # オンボーディング自動フォローアップ（毎朝10:00）
    # 登録から3日後・7日後に未完了クリニックへ自動送信
    _scheduler.add_job(
        _run_onboarding_followup_check, CronTrigger(hour=10, minute=0),
        id="onboarding_followup", replace_existing=True
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
    # ⑤ 無駄地域自動除外スキャン（毎週水曜 4:00）
    _scheduler.add_job(
        _run_auto_exclusion_area_scan, CronTrigger(day_of_week='wed', hour=4, minute=0),
        id=f"area_exclude_scan_{clinic_id}", args=[clinic_id], replace_existing=True
    )
    # ③ 広告パフォーマンス自動収集（毎日深夜2:00）
    _scheduler.add_job(
        _collect_performance_data, CronTrigger(hour=2, minute=0),
        id=f"perf_collect_{clinic_id}", args=[clinic_id], replace_existing=True
    )
    # ④ LTVオフラインコンバージョン自動同期（毎日深夜3:00）
    _scheduler.add_job(
        _run_auto_ltv_upload_scan, CronTrigger(hour=3, minute=0),
        id=f"ltv_sync_{clinic_id}", args=[clinic_id], replace_existing=True
    )
    print(f"[Monitor] 新規クリニックのジョブを動的登録完了 (clinic_id={clinic_id})")


def unregister_clinic_jobs(clinic_id: int):
    """クリニック解約/停止時にスケジューラからジョブを動的削除する"""
    global _scheduler
    if not _scheduler or not _scheduler.running:
        return
    for job_prefix in ["check_", "bid_", "daily_", "weekly_", "nkw_scan_", "perf_collect_", "area_exclude_scan_", "ltv_sync_"]:
        job_id = f"{job_prefix}{clinic_id}"
        try:
            _scheduler.remove_job(job_id)
        except Exception:
            pass  # ジョブが存在しない場合は無視
    print(f"[Monitor] クリニックのジョブを削除完了 (clinic_id={clinic_id})")
