"""
yahoo_ads_client.py - Yahoo! Ads API クライアント（モックモード対応）
MOCK_MODE=True のときはダミーデータを返す。
本番モードではYahoo! Ads Display API v10を使用。
"""
import random
import os
import requests
from datetime import datetime, timedelta
from typing import Optional

# 整体院らしいキャンペーン構成 (Yahoo版)
_CAMPAIGN_TEMPLATES = [
    {"name": "指名検索キャンペーン (Yahoo)",     "budget": 5_000_000_000,  "ctr_base": 5.8, "cvr_base": 7.5,  "status": "ENABLED"},
    {"name": "地域一般 | 整体 (Yahoo)",         "budget": 8_000_000_000,  "ctr_base": 2.5, "cvr_base": 3.5,  "status": "ENABLED"},
    {"name": "症状別 | 腰痛・肩こり (Yahoo)",    "budget": 6_000_000_000,  "ctr_base": 3.8, "cvr_base": 4.8,  "status": "ENABLED"},
    {"name": "リターゲティング (YDN)",          "budget": 4_000_000_000,  "ctr_base": 0.9, "cvr_base": 6.2,  "status": "ENABLED"},
]

# Yahoo! Ads API エンドポイント
YAHOO_ADS_API_BASE  = "https://ads-search.yahooapis.jp/api/v10"
YAHOO_TOKEN_URL     = "https://biz-oauth.yahoo.co.jp/oauth/v1/token"

def _mock_campaign(i: int, name: str = None, customer_id: str = "DEMO"):
    tpl = _CAMPAIGN_TEMPLATES[i % len(_CAMPAIGN_TEMPLATES)]
    random.seed(i * 11 + 17)  
    ctr  = round(tpl["ctr_base"]  + random.uniform(-0.5, 0.5), 2)
    cvr  = round(tpl["cvr_base"]  + random.uniform(-0.8, 0.8), 2)
    imp  = random.randint(1200, 5000)
    clk  = int(imp * ctr / 100)
    cost = clk * random.randint(120_000, 320_000)  # CPC ¥120〜¥320
    conv = round(clk * cvr / 100, 1)
    return {
        "id": f"Y-MOCK-{customer_id}-{1000+i}",
        "name": name or tpl["name"],
        "status": tpl["status"],
        "budget_micros": tpl["budget"],
        "impressions": imp,
        "clicks": clk,
        "ctr": ctr,
        "avg_cpc_micros": random.randint(120_000, 320_000),
        "cost_micros": cost,
        "conversions": conv,
        "cvr": cvr,
    }


def _mock_performance_series(days: str = "7", start_date: str = None, end_date: str = None):
    series = []
    base = datetime.now()
    
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
    elif str(days) == "last_year":
        sd = datetime(base.year - 1, 1, 1)
        ed = datetime(base.year - 1, 12, 31)
        target_days = 365
        base = ed + timedelta(days=1)
    else:
        target_days = int(days)

    if target_days > 365:
        target_days = 365
        
    for d in range(target_days - 1, -1, -1):
        dt = base - timedelta(days=d+1)
        random.seed(str(dt.date()) + "yahoo_mock")
        dow = dt.weekday()
        if dow == 0:   day_mult = 1.20
        elif dow == 1: day_mult = 1.15
        elif dow == 2: day_mult = 1.05
        elif dow == 3: day_mult = 1.00
        elif dow == 4: day_mult = 1.05
        elif dow == 5: day_mult = 0.85
        else:          day_mult = 0.70
        
        imp_base = int(random.randint(500, 2000) * day_mult)
        ctr      = round(random.uniform(2.0, 5.5) * (1.1 if dow < 2 else 0.95 if dow >= 5 else 1.0), 2)
        clk      = max(1, int(imp_base * ctr / 100))
        cpc      = random.randint(120_000, 320_000)
        cost     = clk * cpc
        cvr      = round(random.uniform(2.0, 8.0) * day_mult, 2)
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

class YahooAdsClient:
    """Yahoo! Ads APIラッパー。mock_mode=True なら全てダミーデータを返す。"""

    def __init__(self, account_config: dict):
        raw = account_config.get("yahoo_mock_mode", 1)
        self.mock_mode = (str(raw) != "0") if raw is not None else True
        self.account_id = account_config.get("yahoo_account_id") or "Y-DEMO"
        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None

        self._client_id     = account_config.get("yahoo_client_id") or ""
        self._client_secret = account_config.get("yahoo_client_secret") or ""
        self._refresh_token = account_config.get("yahoo_refresh_token") or ""

        if not self.mock_mode:
            if not all([self._client_id, self._client_secret, self._refresh_token]):
                print("[YahooAdsClient] Yahoo Ads認証情報が不完全のためモックモードで動作します")
                self.mock_mode = True
            else:
                # 初期アクセストークン取得
                try:
                    self._refresh_access_token()
                    print(f"[YahooAdsClient] Yahoo Ads 実API接続成功 (account_id={self.account_id})")
                except Exception as e:
                    print(f"[YahooAdsClient] アクセストークン取得失敗、モックモードに切替: {e}")
                    self.mock_mode = True

    def _refresh_access_token(self):
        """リフレッシュトークンからアクセストークンを再取得する"""
        resp = requests.post(YAHOO_TOKEN_URL, data={
            "grant_type":    "refresh_token",
            "client_id":     self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": self._refresh_token,
        }, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        self._access_token       = data["access_token"]
        expires_in               = data.get("expires_in", 3600)
        self._token_expires_at   = datetime.now() + timedelta(seconds=expires_in - 60)

    def _get_headers(self) -> dict:
        """有効なアクセストークンを付与したヘッダーを返す"""
        if not self._access_token or datetime.now() >= (self._token_expires_at or datetime.min):
            self._refresh_access_token()
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type":  "application/json",
        }

    def _post(self, endpoint: str, payload: dict) -> dict:
        """Yahoo Ads API へのPOSTリクエスト共通処理"""
        url = f"{YAHOO_ADS_API_BASE}/{endpoint}"
        resp = requests.post(url, json=payload, headers=self._get_headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def list_campaigns(self):
        if self.mock_mode:
            return [_mock_campaign(i, customer_id=self.account_id) for i in range(4)]

        try:
            data = self._post("CampaignService/get", {
                "accountId": self.account_id,
                "selector": {
                    "fields": ["CAMPAIGN_ID", "CAMPAIGN_NAME", "CAMPAIGN_DAILY_BUDGET",
                               "CAMPAIGN_STATUS", "IMPRESSIONS", "CLICKS", "CTR",
                               "AVERAGE_CPC", "COST", "CONVERSIONS", "CONV_RATE"],
                    "dateRange": {"endDate": datetime.now().strftime("%Y%m%d"),
                                  "startDate": (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")},
                }
            })
            results = []
            for item in data.get("rval", {}).get("values", []):
                c = item.get("campaign", {})
                m = item.get("campaignStats", {})
                results.append({
                    "id":             str(c.get("campaignId", "")),
                    "name":           c.get("campaignName", ""),
                    "status":         c.get("userStatus", "ENABLED"),
                    "budget_micros":  int(float(c.get("campaignDailyBudget", {}).get("budget", 0)) * 1_000_000),
                    "impressions":    int(m.get("impressions", 0)),
                    "clicks":         int(m.get("clicks", 0)),
                    "ctr":            round(float(m.get("ctr", 0)) * 100, 2),
                    "avg_cpc_micros": int(float(m.get("averageCpc", 0)) * 1_000_000),
                    "cost_micros":    int(float(m.get("cost", 0)) * 1_000_000),
                    "conversions":    float(m.get("conversions", 0)),
                    "cvr":            round(float(m.get("convRate", 0)) * 100, 2),
                })
            return results
        except Exception as e:
            print(f"[YahooAdsClient] list_campaigns エラー: {e}")
            return []

    def create_campaign(self, name: str, budget_micros: int, target_region: str = "", campaign_type: str = "SEARCH") -> str:
        if self.mock_mode:
            mock_id = f"Y-MOCK-{self.account_id}-{random.randint(2000,9999)}"
            print(f"[Y-MOCK] キャンペーン作成: {name} id={mock_id}")
            return mock_id

        try:
            data = self._post("CampaignService/mutate", {
                "accountId": self.account_id,
                "operand": [{
                    "operator": "ADD",
                    "campaign": {
                        "campaignName": name,
                        "campaignType": campaign_type,
                        "userStatus": "ENABLED",
                        "campaignDailyBudget": {"budget": budget_micros / 1_000_000},
                        "biddingStrategyType": "MANUAL_CPC",
                    }
                }]
            })
            return str(data.get("rval", {}).get("values", [{}])[0].get("campaign", {}).get("campaignId", ""))
        except Exception as e:
            print(f"[YahooAdsClient] create_campaign エラー: {e}")
            return ""

    def update_campaign_status(self, campaign_id: str, status: str):
        if self.mock_mode:
            print(f"[Y-MOCK] ステータス更新: {campaign_id} -> {status}")
            return

        try:
            self._post("CampaignService/mutate", {
                "accountId": self.account_id,
                "operand": [{
                    "operator": "SET",
                    "campaign": {"campaignId": campaign_id, "userStatus": status}
                }]
            })
        except Exception as e:
            print(f"[YahooAdsClient] update_campaign_status エラー: {e}")

    def get_performance_series(self, days: str = "7", start_date: str = None, end_date: str = None):
        if self.mock_mode:
            return _mock_performance_series(days, start_date, end_date)

        try:
            if start_date and end_date:
                sd = start_date.replace("-", "")
                ed = end_date.replace("-", "")
            else:
                d_int = int(days) if str(days).isdigit() else 7
                ed = datetime.now().strftime("%Y%m%d")
                sd = (datetime.now() - timedelta(days=d_int)).strftime("%Y%m%d")

            data = self._post("ReportDefinitionService/mutate", {
                "accountId": self.account_id,
                "operand": [{
                    "operator": "ADD",
                    "reportDefinition": {
                        "reportName": f"daily_report_{sd}_{ed}",
                        "reportType": "CAMPAIGN",
                        "dateRangeType": "CUSTOM_DATE",
                        "dateRange": {"startDate": sd, "endDate": ed},
                        "fields": ["DAY", "IMPRESSIONS", "CLICKS", "CTR",
                                   "AVERAGE_CPC", "COST", "CONVERSIONS", "CONV_RATE"],
                        "format": "JSON",
                    }
                }]
            })
            # レポートのダウンロードURLを取得して解析
            report_id = data.get("rval", {}).get("values", [{}])[0].get("reportDefinition", {}).get("reportDefinitionId")
            if not report_id:
                return _mock_performance_series(days, start_date, end_date)

            # レポートダウンロード（ポーリングを省略し、利用可能なら即取得）
            report_resp = requests.get(
                f"{YAHOO_ADS_API_BASE}/ReportService/download?accountId={self.account_id}&reportDefinitionId={report_id}",
                headers=self._get_headers(), timeout=30
            )
            report_resp.raise_for_status()
            rows = report_resp.json().get("report", {}).get("rows", [])
            result = []
            for row in rows:
                result.append({
                    "date":           row.get("day", ""),
                    "impressions":    int(row.get("impressions", 0)),
                    "clicks":         int(row.get("clicks", 0)),
                    "ctr":            round(float(row.get("ctr", 0)) * 100, 2),
                    "avg_cpc_micros": int(float(row.get("averageCpc", 0)) * 1_000_000),
                    "cost_micros":    int(float(row.get("cost", 0)) * 1_000_000),
                    "conversions":    float(row.get("conversions", 0)),
                    "cvr":            round(float(row.get("convRate", 0)) * 100, 2),
                })
            return result
        except Exception as e:
            print(f"[YahooAdsClient] get_performance_series エラー: {e}。モックデータで代替。")
            return _mock_performance_series(days, start_date, end_date)

    def adjust_keyword_bid(self, ad_group_id: str, keyword_id: str, new_cpc_micros: int):
        if self.mock_mode:
            print(f"[Y-MOCK] 入札調整: keyword={keyword_id} new_cpc={new_cpc_micros}")
            return {"adjusted": True, "new_cpc_micros": new_cpc_micros}

        try:
            self._post("BidLandscapeService/mutate", {
                "accountId": self.account_id,
                "operand": [{
                    "operator": "SET",
                    "adGroupCriterion": {
                        "adGroupId": ad_group_id,
                        "criterionId": keyword_id,
                        "bid": {"maxCpc": new_cpc_micros / 1_000_000},
                    }
                }]
            })
            return {"adjusted": True, "new_cpc_micros": new_cpc_micros}
        except Exception as e:
            print(f"[YahooAdsClient] adjust_keyword_bid エラー: {e}")
            return {"adjusted": False, "error": str(e)}


