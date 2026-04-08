"""
yahoo_ads_client.py - Yahoo! Ads API クライアント（モックモード対応）
MOCK_MODE=True のときはダミーデータを返す。
"""
import random
from datetime import datetime, timedelta
from typing import Optional

# 整体院らしいキャンペーン構成 (Yahoo版)
_CAMPAIGN_TEMPLATES = [
    {"name": "指名検索キャンペーン (Yahoo)",     "budget": 5_000_000_000,  "ctr_base": 5.8, "cvr_base": 7.5,  "status": "ENABLED"},
    {"name": "地域一般 | 整体 (Yahoo)",         "budget": 8_000_000_000,  "ctr_base": 2.5, "cvr_base": 3.5,  "status": "ENABLED"},
    {"name": "症状別 | 腰痛・肩こり (Yahoo)",    "budget": 6_000_000_000,  "ctr_base": 3.8, "cvr_base": 4.8,  "status": "ENABLED"},
    {"name": "リターゲティング (YDN)",          "budget": 4_000_000_000,  "ctr_base": 0.9, "cvr_base": 6.2,  "status": "ENABLED"},
]

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
    """最小化されたYahoo Ads APIラッパー。mock_mode=True なら全てダミーデータを返す。"""

    def __init__(self, account_config: dict):
        raw = account_config.get("yahoo_mock_mode", 1)
        self.mock_mode = (str(raw) != "0") if raw is not None else True
        self.account_id = account_config.get("yahoo_account_id") or "Y-DEMO"
        self._client: Optional[object] = None

        if not self.mock_mode:
            try:
                cfg = {
                    "client_id":       account_config.get("yahoo_client_id") or "",
                    "client_secret":   account_config.get("yahoo_client_secret") or "",
                    "refresh_token":   account_config.get("yahoo_refresh_token") or "",
                }
                if not all([cfg["client_id"], cfg["client_secret"], cfg["refresh_token"]]):
                    print("[YahooAdsClient] Yahoo Ads認資情報が不完全のためモックモードで動作します")
                    self.mock_mode = True
                else:
                    print("[YahooAdsClient] Yahoo Ads 実API初期化 (Not fully implemented, falling back to mock)")
                    self.mock_mode = True
            except Exception as e:
                print(f"[YahooAdsClient] API初期化失敗、モックモードに切替: {e}")
                self.mock_mode = True

    def list_campaigns(self):
        if self.mock_mode:
            return [_mock_campaign(i, customer_id=self.account_id) for i in range(4)]
        return []

    def create_campaign(self, name: str, budget_micros: int, target_region: str = "", campaign_type: str = "SEARCH") -> str:
        if self.mock_mode:
            mock_id = f"Y-MOCK-{self.account_id}-{random.randint(2000,9999)}"
            print(f"[Y-MOCK] キャンペーン作成: {name} id={mock_id}")
            return mock_id
        return ""

    def update_campaign_status(self, campaign_id: str, status: str):
        if self.mock_mode:
            print(f"[Y-MOCK] ステータス更新: {campaign_id} -> {status}")
            return

    def get_performance_series(self, days: str = "7", start_date: str = None, end_date: str = None):
        if self.mock_mode:
            return _mock_performance_series(days, start_date, end_date)
        return []

    def adjust_keyword_bid(self, ad_group_id: str, keyword_id: str, new_cpc_micros: int):
        if self.mock_mode:
            print(f"[Y-MOCK] 入札調整: keyword={keyword_id} new_cpc={new_cpc_micros}")
            return {"adjusted": True, "new_cpc_micros": new_cpc_micros}
        return {"adjusted": True, "new_cpc_micros": new_cpc_micros}
