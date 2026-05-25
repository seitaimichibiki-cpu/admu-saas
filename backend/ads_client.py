"""
ads_client.py - Google Ads API クライアント（モックモード対応）
MOCK_MODE=True のときはダミーデータを返す。
"""
import os
import random
from datetime import datetime, timedelta
from typing import Optional

try:
    from google.ads.googleads.client import GoogleAdsClient
    GOOGLE_ADS_AVAILABLE = True
except ImportError:
    GOOGLE_ADS_AVAILABLE = False


# 整体院らしいキャンペーン構成
_CAMPAIGN_TEMPLATES = [
    {"name": "指名検索キャンペーン",     "budget": 5_000_000_000,  "ctr_base": 6.8, "cvr_base": 8.5,  "status": "ENABLED"},
    {"name": "地域一般 | 整体・腰痛",    "budget": 10_000_000_000, "ctr_base": 3.2, "cvr_base": 4.1,  "status": "ENABLED"},
    {"name": "症状別 | 肩こり・頭痛",   "budget": 8_000_000_000,  "ctr_base": 4.5, "cvr_base": 5.8,  "status": "ENABLED"},
    {"name": "競合比較 | 代理店不要",    "budget": 3_000_000_000,  "ctr_base": 2.1, "cvr_base": 3.0,  "status": "PAUSED"},
    {"name": "リターゲティング | 再来院","budget": 4_000_000_000,  "ctr_base": 5.9, "cvr_base": 7.2,  "status": "ENABLED"},
]

def _mock_campaign(i: int, name: str = None, customer_id: str = "DEMO"):
    tpl = _CAMPAIGN_TEMPLATES[i % len(_CAMPAIGN_TEMPLATES)]
    random.seed(i * 7 + 13)  # 固定シード（毎回同じ値）
    ctr  = round(tpl["ctr_base"]  + random.uniform(-0.5, 0.5), 2)
    cvr  = round(tpl["cvr_base"]  + random.uniform(-0.8, 0.8), 2)
    imp  = random.randint(800, 4000)
    clk  = int(imp * ctr / 100)
    cost = clk * random.randint(150_000, 380_000)  # CPC ¥150〜¥380
    conv = round(clk * cvr / 100, 1)
    return {
        "id": f"MOCK-{customer_id}-{1000+i}",
        "name": name or tpl["name"],
        "status": tpl["status"],
        "budget_micros": tpl["budget"],
        "impressions": imp,
        "clicks": clk,
        "ctr": ctr,
        "avg_cpc_micros": random.randint(150_000, 380_000),
        "cost_micros": cost,
        "conversions": conv,
        "cvr": cvr,
    }


def _mock_performance_series(days: str = "7", start_date: str = None, end_date: str = None):
    series = []
    base = datetime.now()
    
    # 期間の計算
    target_days = 7
    if start_date and end_date:
        sd = datetime.strptime(start_date, "%Y-%m-%d")
        ed = datetime.strptime(end_date, "%Y-%m-%d")
        target_days = max(1, (ed - sd).days + 1)
        base = ed + timedelta(days=1)
    elif str(days) == "this_year":
        sd = datetime(base.year, 1, 1)
        ed = datetime(base.year, 12, 31)
        target_days = (base - sd).days + 1
        base = base
    elif str(days) == "last_year":
        sd = datetime(base.year - 1, 1, 1)
        ed = datetime(base.year - 1, 12, 31)
        target_days = 365
        base = ed + timedelta(days=1)
    else:
        target_days = int(days)

    if target_days > 365:
        target_days = 365 # メモリ過多を防ぐためモック上限
        
    for d in range(target_days - 1, -1, -1):
        dt = base - timedelta(days=d+1)
        random.seed(str(dt.date()) + "mock")
        # 整体院らしい曜日パターン
        # 月・火：集患意欲高（週明け痛み増）、水・木：中程度、金：やや高、土日：低下
        dow = dt.weekday()  # 0=月, 6=日
        if dow == 0:   day_mult = 1.30  # 月: 高い
        elif dow == 1: day_mult = 1.20  # 火
        elif dow == 2: day_mult = 1.05  # 水
        elif dow == 3: day_mult = 1.00  # 木
        elif dow == 4: day_mult = 1.10  # 金
        elif dow == 5: day_mult = 0.75  # 土: 低下
        else:          day_mult = 0.60  # 日: 最低
        imp_base = int(random.randint(400, 1600) * day_mult)
        ctr      = round(random.uniform(2.8, 6.5) * (1.1 if dow < 2 else 0.95 if dow >= 5 else 1.0), 2)
        clk      = max(1, int(imp_base * ctr / 100))
        cpc      = random.randint(150_000, 350_000)  # ¥150〜¥350
        cost     = clk * cpc
        cvr      = round(random.uniform(2.5, 9.0) * day_mult, 2)
        conv     = round(clk * cvr / 100, 1)
        series.append({
            "date": dt.strftime("%Y-%m-%d"),
            "impressions": imp_base,
            "clicks": clk,
            "ctr": ctr,
            "avg_cpc_micros": cpc,
            "cost_micros": cost,
            "conversions": conv,
            "cvr": cvr,
        })
    return series


class AdsClient:
    """最小化されたGoogle Ads APIラッパー。mock_mode=True なら全てダミーデータを返す。"""

    def __init__(self, account_config: dict):
        # mock_modeは 1 / "1" / True なども全てモックとして扱う
        raw = account_config.get("mock_mode", 1)
        self.mock_mode = (str(raw) != "0") if raw is not None else True
        raw_customer_id = account_config.get("customer_id") or "DEMO"
        self.customer_id = str(raw_customer_id).replace("-", "")
        self._client: Optional[object] = None

        if not self.mock_mode and GOOGLE_ADS_AVAILABLE:
            try:
                cfg = {
                    "developer_token": account_config.get("developer_token") or os.environ.get("MASTER_ADS_DEVELOPER_TOKEN", "") or os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", ""),
                    "client_id":       account_config.get("client_id") or os.environ.get("MASTER_ADS_CLIENT_ID", "") or os.environ.get("GOOGLE_ADS_CLIENT_ID", ""),
                    "client_secret":   account_config.get("client_secret") or os.environ.get("MASTER_ADS_CLIENT_SECRET", "") or os.environ.get("GOOGLE_ADS_CLIENT_SECRET", ""),
                    "refresh_token":   account_config.get("refresh_token") or os.environ.get("MASTER_ADS_REFRESH_TOKEN", "") or os.environ.get("GOOGLE_ADS_REFRESH_TOKEN", ""),
                    "use_proto_plus": True,
                }
                
                # login_customer_id（親MCCのID）の解決：顧客個別の設定があれば優先、なければマスターのMCC ID
                login_id = account_config.get("login_customer_id") or os.environ.get("MASTER_ADS_LOGIN_CUSTOMER_ID", "")
                if login_id:
                    cfg["login_customer_id"] = str(login_id).replace("-", "")

                # 認証情報が一つでも欠ければモックにフォールバック（どのキーが欠けているか詳細ログ）
                missing_keys = [k for k in ["developer_token", "client_id", "client_secret", "refresh_token"] if not cfg[k]]
                if missing_keys:
                    print(f"[AdsClient] 本番モードに切替できません。以下の認証情報が未設定です: {', '.join(missing_keys)} (Customer: {self.customer_id})")
                    print(f"[AdsClient] → 設定画面から各認証情報を入力して保存してください。モックモードで動作継続します。")
                    self.mock_mode = True
                else:
                    self._client = GoogleAdsClient.load_from_dict(cfg)
                    print(f"[AdsClient] ✅ 本番APIモードで初期化成功 (Customer: {self.customer_id})")
            except Exception as e:
                print(f"[AdsClient] API初期化失敗、モックモードに切替: {e}")
                self.mock_mode = True
        elif not self.mock_mode and not GOOGLE_ADS_AVAILABLE:
            print(f"[AdsClient] google-ads ライブラリが未インストールのためモックモードで動作します。`pip install google-ads` を実行してください。")
            self.mock_mode = True

    # ---- キャンペーン ----
    def list_campaigns(self):
        if self.mock_mode:
            return [_mock_campaign(i, customer_id=self.customer_id) for i in range(4)]
        # 実API実装
        ga_service = self._client.get_service("GoogleAdsService")
        query = """
            SELECT campaign.id, campaign.name, campaign.status,
                   campaign_budget.amount_micros,
                   metrics.impressions, metrics.clicks, metrics.ctr,
                   metrics.average_cpc, metrics.cost_micros,
                   metrics.conversions, metrics.conversions_from_interactions_rate
            FROM campaign
            WHERE segments.date DURING LAST_7_DAYS
        """
        resp = ga_service.search(customer_id=self.customer_id, query=query)
        results = []
        for row in resp:
            c = row.campaign
            m = row.metrics
            results.append({
                "id": str(c.id),
                "name": c.name,
                "status": c.status.name,
                "budget_micros": row.campaign_budget.amount_micros,
                "impressions": m.impressions,
                "clicks": m.clicks,
                "ctr": round(m.ctr * 100, 2),
                "avg_cpc_micros": int(m.average_cpc),
                "cost_micros": int(m.cost_micros),
                "conversions": m.conversions,
                "cvr": round(m.conversions_from_interactions_rate * 100, 2),
            })
        return results

    def create_campaign(self, name: str, budget_micros: int, target_region: str = "",
                        campaign_type: str = "SEARCH") -> str:
        if self.mock_mode:
            mock_id = f"MOCK-{self.customer_id}-{random.randint(2000,9999)}"
            print(f"[MOCK] キャンペーン作成: {name} id={mock_id}")
            return mock_id
        # 実API実装（簡略版）
        campaign_service = self._client.get_service("CampaignService")
        campaign_op = self._client.get_type("CampaignOperation")
        campaign = campaign_op.create
        campaign.name = name
        campaign.status = self._client.enums.CampaignStatusEnum.ENABLED
        campaign.advertising_channel_type = self._client.enums.AdvertisingChannelTypeEnum.SEARCH
        campaign.manual_cpc.enhanced_cpc_enabled = True
        campaign.campaign_budget = f"customers/{self.customer_id}/campaignBudgets/{budget_micros}"
        resp = campaign_service.mutate_campaigns(
            customer_id=self.customer_id, operations=[campaign_op])
        return resp.results[0].resource_name.split("/")[-1]

    def update_campaign_status(self, google_campaign_id: str, status: str):
        if self.mock_mode:
            print(f"[MOCK] ステータス更新: {google_campaign_id} -> {status}")
            return
        campaign_service = self._client.get_service("CampaignService")
        campaign_op = self._client.get_type("CampaignOperation")
        campaign = campaign_op.update
        campaign.resource_name = f"customers/{self.customer_id}/campaigns/{google_campaign_id}"
        status_enum = self._client.enums.CampaignStatusEnum[status]
        campaign.status = status_enum
        campaign_op.update_mask.paths.append("status")
        campaign_service.mutate_campaigns(customer_id=self.customer_id, operations=[campaign_op])

    # ---- パフォーマンス ----
    def get_performance_series(self, days: str = "7", start_date: str = None, end_date: str = None):
        if self.mock_mode:
            return _mock_performance_series(days, start_date, end_date)
        ga_service = self._client.get_service("GoogleAdsService")
        
        where_clause = ""
        if start_date and end_date:
            where_clause = f"segments.date BETWEEN '{start_date}' AND '{end_date}'"
        elif str(days) == "this_year":
            where_clause = "segments.date DURING THIS_YEAR"
        elif str(days) == "last_year":
            where_clause = "segments.date DURING LAST_YEAR"
        else:
            where_clause = f"segments.date DURING LAST_{days}_DAYS"

        query = f"""
            SELECT segments.date, metrics.impressions, metrics.clicks, metrics.ctr,
                   metrics.average_cpc, metrics.cost_micros, metrics.conversions,
                   metrics.conversions_from_interactions_rate
            FROM campaign WHERE {where_clause}
            ORDER BY segments.date
        """
        resp = ga_service.search(customer_id=self.customer_id, query=query)
        by_date = {}
        for row in resp:
            d = row.segments.date
            m = row.metrics
            if d not in by_date:
                by_date[d] = {"date": d, "impressions": 0, "clicks": 0,
                              "ctr": [], "avg_cpc_micros": [], "cost_micros": 0,
                              "conversions": 0, "cvr": []}
            by_date[d]["impressions"] += m.impressions
            by_date[d]["clicks"] += m.clicks
            by_date[d]["ctr"].append(m.ctr * 100)
            by_date[d]["avg_cpc_micros"].append(int(m.average_cpc))
            by_date[d]["cost_micros"] += int(m.cost_micros)
            by_date[d]["conversions"] += m.conversions
            by_date[d]["cvr"].append(m.conversions_from_interactions_rate * 100)
        result = []
        for d, v in sorted(by_date.items()):
            result.append({
                "date": d,
                "impressions": v["impressions"], "clicks": v["clicks"],
                "ctr": round(sum(v["ctr"])/len(v["ctr"]) if v["ctr"] else 0, 2),
                "avg_cpc_micros": int(sum(v["avg_cpc_micros"])/len(v["avg_cpc_micros"]) if v["avg_cpc_micros"] else 0),
                "cost_micros": v["cost_micros"], "conversions": v["conversions"],
                "cvr": round(sum(v["cvr"])/len(v["cvr"]) if v["cvr"] else 0, 2),
            })
        return result

    # ---- 検索語句レポート ② ----
    def get_search_term_report(self, days: int = 30, min_cost_yen: int = 500, max_conversions: float = 0) -> list:
        """
        検索語句レポートを取得。
        コスト高・コンバージョンゼロの「ムダ遣い語句」を自動特定する。

        Args:
            days:             集計期間（日数）
            min_cost_yen:     この金額以上使っている語句のみ対象
            max_conversions:  この数以下のコンバージョン数の語句を「ムダ」と判定

        Returns:
            list of { search_term, clicks, cost_yen, conversions, campaign_id, campaign_name, is_wasted }
        """
        if self.mock_mode:
            import random
            mock_terms = [
                ("整体 安い", 45, 8100, 0.0),
                ("腰痛 セルフケア", 38, 6800, 0.0),
                ("整体師 求人", 22, 4400, 0.0),
                ("肩こり 解消 自分で", 19, 3420, 0.0),
                ("整体 無料", 31, 5580, 0.0),
                ("腰痛 整体 渋谷", 55, 9900, 3.2),
                ("産後 骨盤矯正", 48, 8640, 4.1),
                ("整体 おすすめ", 62, 11160, 2.8),
                ("整体院 予約", 71, 12780, 5.5),
            ]
            results = []
            for term, clicks, cost_yen, conv in mock_terms:
                is_wasted = (cost_yen >= min_cost_yen and conv <= max_conversions)
                results.append({
                    "search_term": term,
                    "clicks": clicks,
                    "cost_yen": cost_yen,
                    "conversions": conv,
                    "campaign_id": f"MOCK-{self.customer_id}-1001",
                    "campaign_name": "地域一般 | 整体・腰痛",
                    "is_wasted": is_wasted,
                })
            return sorted(results, key=lambda x: (-int(x["is_wasted"]), -x["cost_yen"]))

        # 実API実装
        ga_service = self._client.get_service("GoogleAdsService")
        query = f"""
            SELECT
                search_term_view.search_term,
                campaign.id, campaign.name,
                metrics.clicks, metrics.cost_micros, metrics.conversions
            FROM search_term_view
            WHERE segments.date DURING LAST_{days}_DAYS
              AND metrics.impressions > 0
            ORDER BY metrics.cost_micros DESC
            LIMIT 500
        """
        resp = ga_service.search(customer_id=self.customer_id, query=query)
        results = []
        for row in resp:
            cost_yen = int(row.metrics.cost_micros) / 1_000_000
            conv     = row.metrics.conversions
            is_wasted = (cost_yen >= min_cost_yen and conv <= max_conversions)
            results.append({
                "search_term":   row.search_term_view.search_term,
                "clicks":        row.metrics.clicks,
                "cost_yen":      round(cost_yen),
                "conversions":   conv,
                "campaign_id":   str(row.campaign.id),
                "campaign_name": row.campaign.name,
                "is_wasted":     is_wasted,
            })
        return sorted(results, key=lambda x: (-int(x["is_wasted"]), -x["cost_yen"]))

    # ---- 入札 ----
    def adjust_keyword_bid(self, ad_group_id: str, keyword_id: str, new_cpc_micros: int):
        if self.mock_mode:
            print(f"[MOCK] 入札調整: keyword={keyword_id} new_cpc={new_cpc_micros}")
            return {"adjusted": True, "new_cpc_micros": new_cpc_micros}

        # --- 実API実装 ---
        # new_cpc_microsが100未満（極端に低い）の場合は安全装置として拒否
        if new_cpc_micros < 100:
            print(f"[AdsClient] 入札単価が低すぎるため適用をスキップ: {new_cpc_micros} micros")
            return {"adjusted": False, "reason": "bid_too_low"}

        try:
            ad_group_criterion_service = self._client.get_service("AdGroupCriterionService")
            ga_service = self._client.get_service("GoogleAdsService")

            # ad_group_id="*" はアカウント全キーワードを対象にする
            if ad_group_id == "*":
                query = f"""
                    SELECT ad_group_criterion.resource_name,
                           ad_group_criterion.cpc_bid_micros,
                           ad_group.id
                    FROM ad_group_criterion
                    WHERE ad_group_criterion.type = 'KEYWORD'
                      AND ad_group_criterion.status = 'ENABLED'
                      AND ad_group.status = 'ENABLED'
                    LIMIT 50
                """
                resp = ga_service.search(customer_id=self.customer_id, query=query)
                operations = []
                for row in resp:
                    op = self._client.get_type("AdGroupCriterionOperation")
                    criterion = op.update
                    criterion.resource_name = row.ad_group_criterion.resource_name
                    criterion.cpc_bid_micros = new_cpc_micros
                    op.update_mask.paths.append("cpc_bid_micros")
                    operations.append(op)

                if operations:
                    ad_group_criterion_service.mutate_ad_group_criteria(
                        customer_id=self.customer_id, operations=operations
                    )
                    print(f"[AdsClient] 入札調整完了: {len(operations)}件のキーワードを更新")
                    return {"adjusted": True, "new_cpc_micros": new_cpc_micros, "count": len(operations)}
                return {"adjusted": False, "reason": "no_keywords_found"}
            else:
                # 特定キーワードIDを指定
                resource_name = f"customers/{self.customer_id}/adGroupCriteria/{ad_group_id}~{keyword_id}"
                op = self._client.get_type("AdGroupCriterionOperation")
                criterion = op.update
                criterion.resource_name = resource_name
                criterion.cpc_bid_micros = new_cpc_micros
                op.update_mask.paths.append("cpc_bid_micros")
                ad_group_criterion_service.mutate_ad_group_criteria(
                    customer_id=self.customer_id, operations=[op]
                )
                return {"adjusted": True, "new_cpc_micros": new_cpc_micros}
        except Exception as e:
            print(f"[AdsClient] 入札調整エラー: {e}")
            return {"adjusted": False, "error": str(e)}

    # ---- 時間帯別入札スケジュール適用 ⑤ ----
    def apply_ad_schedule_bid_modifiers(self, campaign_id: str, schedule_modifiers: list) -> dict:
        """
        時間帯ヒートマップの分析結果をGoogle Adsのキャンペーンに実際に適用する。

        Args:
            campaign_id:        Google Adsキャンペーンリソース名またはID
            schedule_modifiers: [
                { "day_of_week": "MONDAY", "start_hour": 9, "end_hour": 10, "bid_modifier": 1.3 },
                ...
            ]
        Returns:
            dict: { "success": bool, "applied_count": int }
        """
        if self.mock_mode:
            print(f"[MOCK] 入札スケジュール適用: campaign={campaign_id} 件数={len(schedule_modifiers)}")
            return {"success": True, "applied_count": len(schedule_modifiers), "mock": True}

        try:
            campaign_criterion_service = self._client.get_service("CampaignCriterionService")
            operations = []
            for slot in schedule_modifiers:
                op = self._client.get_type("CampaignCriterionOperation")
                criterion = op.create
                criterion.campaign = f"customers/{self.customer_id}/campaigns/{campaign_id}"
                criterion.bid_modifier = float(slot.get("bid_modifier", 1.0))

                # 曜日マッピング
                day_enum = self._client.enums.DayOfWeekEnum[slot["day_of_week"]]
                criterion.ad_schedule.day_of_week = day_enum
                criterion.ad_schedule.start_hour  = int(slot["start_hour"])
                criterion.ad_schedule.end_hour    = int(slot["end_hour"])
                criterion.ad_schedule.start_minute = self._client.enums.MinuteOfHourEnum.ZERO
                criterion.ad_schedule.end_minute   = self._client.enums.MinuteOfHourEnum.ZERO
                operations.append(op)

            resp = campaign_criterion_service.mutate_campaign_criteria(
                customer_id=self.customer_id, operations=operations
            )
            return {"success": True, "applied_count": len(resp.results), "mock": False}
        except Exception as e:
            print(f"[AdsClient] 入札スケジュール適用エラー: {e}")
            return {"success": False, "error": str(e)}

    # ---- コンバージョン送信 (OCT) ----
    def upload_offline_conversion(self, gclid: str, conversion_name: str, conversion_value: float, conversion_time: str):
        if self.mock_mode:
            print(f"[MOCK] OCT送信: GCLID={gclid}, Name={conversion_name}, Value={conversion_value}")
            return {"success": True, "mock": True}
        
        try:
            # 1. コンバージョンアクションを取得
            ga_service = self._client.get_service("GoogleAdsService")
            query = f"SELECT conversion_action.resource_name FROM conversion_action WHERE conversion_action.name = '{conversion_name}' AND conversion_action.status = 'ENABLED' LIMIT 1"
            resp = ga_service.search(customer_id=self.customer_id, query=query)
            action_resource = None
            for row in resp:
                action_resource = row.conversion_action.resource_name
                break
                
            if not action_resource:
                return {"success": False, "error": f"Conversion action '{conversion_name}' not found."}
                
            # 2. コンバージョン送信
            conversion_upload_service = self._client.get_service("ConversionUploadService")
            click_conversion = self._client.get_type("ClickConversion")
            click_conversion.conversion_action = action_resource
            click_conversion.gclid = gclid
            if conversion_value:
                click_conversion.conversion_value = float(conversion_value)
                click_conversion.currency_code = "JPY"
            if conversion_time:
                click_conversion.conversion_date_time = conversion_time
                
            request = self._client.get_type("UploadClickConversionsRequest")
            request.customer_id = self.customer_id
            request.conversions.append(click_conversion)
            request.partial_failure = True
            
            upload_response = conversion_upload_service.upload_click_conversions(request=request)
            
            if hasattr(upload_response, "partial_failure_error") and upload_response.partial_failure_error.message:
                return {"success": False, "error": upload_response.partial_failure_error.message}
            return {"success": True, "mock": False, "resource": action_resource}
        except Exception as e:
            return {"success": False, "error": str(e)}
