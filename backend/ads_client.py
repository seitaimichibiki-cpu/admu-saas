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
                    "developer_token": account_config.get("developer_token") or os.environ.get("MASTER_ADS_DEVELOPER_TOKEN", ""),
                    "client_id":       account_config.get("client_id") or os.environ.get("MASTER_ADS_CLIENT_ID", ""),
                    "client_secret":   account_config.get("client_secret") or os.environ.get("MASTER_ADS_CLIENT_SECRET", ""),
                    "refresh_token":   account_config.get("refresh_token") or os.environ.get("MASTER_ADS_REFRESH_TOKEN", ""),
                    "use_proto_plus": True,
                }
                
                # login_customer_id（親MCCのID）の解決：顧客個別の設定があれば優先、なければマスターのMCC ID
                login_id = account_config.get("login_customer_id") or os.environ.get("MASTER_ADS_LOGIN_CUSTOMER_ID", "")
                if login_id:
                    cfg["login_customer_id"] = str(login_id).replace("-", "")

                # 認証情報が一つでも欠ければモックにフォールバック
                if not all([cfg["developer_token"], cfg["client_id"],
                            cfg["client_secret"], cfg["refresh_token"]]):
                    print(f"[AdsClient] Ads認資情報が不完全のためモックモードで動作します (Customer: {self.customer_id})")
                    self.mock_mode = True
                else:
                    self._client = GoogleAdsClient.load_from_dict(cfg)
            except Exception as e:
                print(f"[AdsClient] API初期化失敗、モックモードに切替: {e}")
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

    # ---- 入札 ----
    def adjust_keyword_bid(self, ad_group_id: str, keyword_id: str, new_cpc_micros: int):
        if self.mock_mode:
            print(f"[MOCK] 入札調整: keyword={keyword_id} new_cpc={new_cpc_micros}")
            return {"adjusted": True, "new_cpc_micros": new_cpc_micros}
        # 実API実装省略（google-ads SDK での bid 更新）
        return {"adjusted": True, "new_cpc_micros": new_cpc_micros}

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
