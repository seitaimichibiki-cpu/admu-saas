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
def clean_keyword_text(text: str) -> str:
    """Google広告で使用できない無効な記号を除去・クリーニングする"""
    if not text:
        return ""
    import re
    # 無効な記号（! @ # $ % ^ & * ( ) = { } [ ] | \ : ; " ' < > , ? / ~ と全角チルダ・波ダッシュなど）を除去
    invalid_chars = r'[!@#\$%\^&\*\(\)=\{\}\[\]|\\:;"\'<>\,\?\/\~〜~]'
    cleaned = re.sub(invalid_chars, '', text)
    # 連続するスペースを1つの半角スペースにまとめる
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned.strip()


class AdsClient:
    """最小化されたGoogle Ads APIラッパー。mock_mode=True なら全てダミーデータを返す。"""

    def __init__(self, account_config: dict):
        # mock_modeは 1 / "1" / True なども全てモックとして扱う
        raw = account_config.get("mock_mode", 1)
        self.mock_mode = (str(raw) != "0") if raw is not None else True
        raw_customer_id = account_config.get("customer_id") or "DEMO"
        self.customer_id = str(raw_customer_id).replace("-", "")
        self._client: Optional[object] = None

        # REST API用の認証情報を常に初期化（どのパスでも属性が存在するように）
        self._developer_token   = account_config.get("developer_token") or os.environ.get("MASTER_ADS_DEVELOPER_TOKEN", "") or os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", "")
        self._client_id         = account_config.get("client_id") or os.environ.get("MASTER_ADS_CLIENT_ID", "") or os.environ.get("GOOGLE_ADS_CLIENT_ID", "")
        self._client_secret     = account_config.get("client_secret") or os.environ.get("MASTER_ADS_CLIENT_SECRET", "") or os.environ.get("GOOGLE_ADS_CLIENT_SECRET", "")
        self._refresh_token     = account_config.get("refresh_token") or os.environ.get("MASTER_ADS_REFRESH_TOKEN", "") or os.environ.get("GOOGLE_ADS_REFRESH_TOKEN", "")
        login_id_raw            = account_config.get("login_customer_id") or os.environ.get("MASTER_ADS_LOGIN_CUSTOMER_ID", "")
        self._login_customer_id = str(login_id_raw).replace("-", "") if login_id_raw else ""

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

                # REST API用に認証情報をインスタンスに保存
                self._developer_token  = cfg["developer_token"]
                self._client_id        = cfg["client_id"]
                self._client_secret    = cfg["client_secret"]
                self._refresh_token    = cfg["refresh_token"]
                self._login_customer_id = cfg.get("login_customer_id", "")

                # 認証情報が一つでも欠ければモックにフォールバック（どのキーが欠けているか詳細ログ）
                missing_keys = [k for k in ["developer_token", "client_id", "client_secret", "refresh_token"] if not cfg[k]]
                if missing_keys:
                    print(f"[AdsClient] 本番モードに切替できません。以下の認証情報が未設定です: {', '.join(missing_keys)} (Customer: {self.customer_id})")
                    print(f"[AdsClient] → 設定画面から各認証情報を入力して保存してください。モックモードで動作継続します。")
                    self.mock_mode = True
                else:
                    # RESTトランスポートを使用: gRPCのbool省略問題を回避しJSON通信にする
                    try:
                        self._client = GoogleAdsClient.load_from_dict(cfg, transport="rest")
                        print(f"[AdsClient] ✅ 本番APIモード(REST)で初期化成功 (Customer: {self.customer_id})")
                    except TypeError:
                        # 古いライブラリはtransport引数未対応 → gRPCフォールバック
                        self._client = GoogleAdsClient.load_from_dict(cfg)
                        print(f"[AdsClient] ✅ 本番APIモード(gRPC)で初期化成功 (Customer: {self.customer_id})")
            except Exception as e:
                print(f"[AdsClient] API初期化失敗、モックモードに切替: {e}")
                self._init_error = str(e)
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
                "budget_micros": row.campaign_budget.amount_micros if row.campaign_budget.amount_micros is not None else 0,
                "impressions": m.impressions if m.impressions is not None else 0,
                "clicks": m.clicks if m.clicks is not None else 0,
                "ctr": round(m.ctr * 100, 2) if m.ctr is not None else 0.0,
                "avg_cpc_micros": int(m.average_cpc) if m.average_cpc is not None else 0,
                "cost_micros": int(m.cost_micros) if m.cost_micros is not None else 0,
                "conversions": m.conversions if m.conversions is not None else 0.0,
                "cvr": round(m.conversions_from_interactions_rate * 100, 2) if m.conversions_from_interactions_rate is not None else 0.0,
            })
        return results

    def create_campaign(self, name: str, budget_micros: int, target_region: str = "",
                        campaign_type: str = "SEARCH") -> str:
        """後方互換用。新規はcreate_full_campaign_setupを使うこと。"""
        if self.mock_mode:
            mock_id = f"MOCK-{self.customer_id}-{random.randint(2000,9999)}"
            print(f"[MOCK] キャンペーン作成: {name} id={mock_id}")
            return mock_id
        # バジェット作成
        budget_service = self._client.get_service("CampaignBudgetService")
        b_op = self._client.get_type("CampaignBudgetOperation")
        b = b_op.create
        b.name = f"{name}_budget_{random.randint(1000,9999)}"
        b.amount_micros = budget_micros
        b.delivery_method = self._client.enums.BudgetDeliveryMethodEnum.STANDARD
        b_resp = budget_service.mutate_campaign_budgets(customer_id=self.customer_id, operations=[b_op])
        budget_rn = b_resp.results[0].resource_name
        # キャンペーン作成
        campaign_service = self._client.get_service("CampaignService")
        campaign_op = self._client.get_type("CampaignOperation")
        campaign = campaign_op.create
        campaign.name = name
        campaign.status = self._client.enums.CampaignStatusEnum.PAUSED
        campaign.advertising_channel_type = self._client.enums.AdvertisingChannelTypeEnum.SEARCH
        campaign.manual_cpc.enhanced_cpc_enabled = True
        campaign.campaign_budget = budget_rn
        campaign.network_settings.target_google_search = True
        campaign.network_settings.target_search_network = True
        campaign.network_settings.target_content_network = False
        resp = campaign_service.mutate_campaigns(
            customer_id=self.customer_id, operations=[campaign_op])
        return resp.results[0].resource_name.split("/")[-1]

    def _get_rest_access_token(self) -> str:
        """REST API用のアクセストークンを取得する"""
        import google.oauth2.credentials
        import google.auth.transport.requests as ga_req
        creds = google.oauth2.credentials.Credentials(
            token=None,
            refresh_token=self._refresh_token,
            client_id=self._client_id,
            client_secret=self._client_secret,
            token_uri="https://oauth2.googleapis.com/token",
        )
        creds.refresh(ga_req.Request())
        return creds.token

    def _rest_post(self, endpoint: str, ops: list, access_token: str) -> dict:
        """Google Ads REST API v23 へのPOSTリクエスト"""
        import requests as rq
        url = f"https://googleads.googleapis.com/v23/customers/{self.customer_id}/{endpoint}:mutate"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "developer-token": self._developer_token,
            "login-customer-id": self._login_customer_id,
            "Content-Type": "application/json",
        }
        resp = rq.post(url, headers=headers, json={"operations": ops})
        if resp.status_code != 200:
            raise Exception(f"REST APIエラー [{endpoint}]: {resp.text[:500]}")
        return resp.json()

    def create_full_campaign_setup(self, config: dict) -> dict:
        """
        キャンペーン・広告グループ・キーワード・RSA広告文を一括作成。
        config = {
            "campaign_name": str,
            "daily_budget_yen": int,
            "final_url": str,
            "status": "PAUSED" | "ENABLED",
            "lat": float, "lon": float, "radius_km": int,  # 位置ターゲティング
            "ad_groups": [
                {
                    "name": str,
                    "keywords": [{"text": str, "match_type": "PHRASE"|"EXACT"|"BROAD"}],
                    "headlines": [str],   # max 15本・各30文字以内
                    "descriptions": [str] # max 4本・各90文字以内
                }
            ]
        }
        """
        if self.mock_mode:
            cid = f"MOCK-{self.customer_id}-{random.randint(3000,9999)}"
            result = {
                "campaign_id": cid,
                "campaign_name": config["campaign_name"],
                "status": "PAUSED",
                "mock": True,
                "ad_groups": []
            }
            for ag in config.get("ad_groups", []):
                ag_id = f"MOCK-AG-{random.randint(1000,9999)}"
                result["ad_groups"].append({
                    "name": ag["name"],
                    "id": ag_id,
                    "keywords_added": len(ag.get("keywords", [])),
                    "ad_created": True,
                })
            print(f"[MOCK] create_full_campaign_setup: {result}")
            return result

        cid = self.customer_id

        # REST API v23 を使用（gRPCのbool省略問題を回避）
        # containsEuPoliticalAdvertising: gRPCではFalse=0がデフォルト値として省略されREQUIREDエラーになる
        token = self._get_rest_access_token()
        import requests as _rq
        _rest_headers = {
            "Authorization": f"Bearer {token}",
            "developer-token": self._developer_token,
            "login-customer-id": self._login_customer_id,
            "Content-Type": "application/json",
        }

        # ① バジェット作成
        daily_micros = config["daily_budget_yen"] * 1_000_000
        r = self._rest_post("campaignBudgets", [{"create": {
            "name": f"{config['campaign_name']}_budget_{random.randint(1000,9999)}",
            "amountMicros": str(daily_micros),
            "deliveryMethod": "STANDARD",
        }}], token)
        budget_rn = r["results"][0]["resourceName"]
        print(f"[AdsClient] バジェット作成: {budget_rn}")

        # ② キャンペーン作成（REST経由でEnumを明示的に指定）
        # containsEuPoliticalAdvertising: 3 = DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
        r2 = self._rest_post("campaigns", [{"create": {
            "name": config["campaign_name"],
            "status": config.get("status", "PAUSED"),
            "advertisingChannelType": "SEARCH",
            "campaignBudget": budget_rn,
            "manualCpc": {"enhancedCpcEnabled": False},
            "containsEuPoliticalAdvertising": 3,
            "networkSettings": {
                "targetGoogleSearch": True,
                "targetSearchNetwork": True,
                "targetContentNetwork": False,
                "targetPartnerSearchNetwork": False,
            },
        }}], token)
        campaign_rn = r2["results"][0]["resourceName"]
        campaign_id = campaign_rn.split("/")[-1]
        print(f"[AdsClient] キャンペーン作成: {campaign_rn} (id={campaign_id})")

        # ③ 位置ターゲティング（半径指定）
        if config.get("lat") and config.get("lon"):
            try:
                self._rest_post("campaignCriteria", [{"create": {
                    "campaign": campaign_rn,
                    "proximity": {
                        "geoPoint": {
                            "longitudeInMicroDegrees": int(config["lon"] * 1_000_000),
                            "latitudeInMicroDegrees": int(config["lat"] * 1_000_000),
                        },
                        "radius": config.get("radius_km", 20),
                        "radiusUnits": "KILOMETERS",
                    }
                }}], token)
                print(f"[AdsClient] 位置ターゲティング設定完了")
            except Exception as e:
                print(f"[AdsClient] 位置ターゲティング設定エラー（続行）: {e}")

        # ④ 広告グループ・キーワード・RSA作成
        ad_groups_result = []

        for ag_config in config.get("ad_groups", []):
            # 広告グループ作成
            r4 = self._rest_post("adGroups", [{"create": {
                "name": ag_config["name"],
                "campaign": campaign_rn,
                "status": "ENABLED",
                "type": "SEARCH_STANDARD",
                "cpcBidMicros": "200000000",
            }}], token)
            ag_rn = r4["results"][0]["resourceName"]
            ag_id = ag_rn.split("/")[-1]
            print(f"[AdsClient] 広告グループ作成: {ag_config['name']} ({ag_id})")

            # キーワード追加
            kw_ops = [{"create": {
                "adGroup": ag_rn,
                "status": "ENABLED",
                "keyword": {
                    "text": kw["text"],
                    "matchType": kw.get("match_type", "PHRASE").upper(),
                },
            }} for kw in ag_config.get("keywords", [])]
            kw_added = 0
            if kw_ops:
                kw_url = f"https://googleads.googleapis.com/v23/customers/{cid}/adGroupCriteria:mutate"
                kw_resp = _rq.post(kw_url, headers=_rest_headers, json={"operations": kw_ops})
                if kw_resp.status_code == 200:
                    kw_added = len(kw_resp.json().get("results", []))
                    print(f"[AdsClient] キーワード追加: {kw_added}/{len(kw_ops)}件")
                else:
                    print(f"[AdsClient] キーワード追加エラー: {kw_resp.text[:200]}")

            # RSA広告文作成
            # Google Ads RSAの文字数制限: ヘッドライン30文字以内、説明文45文字以内（全角）
            headlines = [{"text": hl[:30]} for hl in ag_config.get("headlines", [])[:15]]
            descriptions = [{"text": d[:45]} for d in ag_config.get("descriptions", [])[:4]]

            ad_url = f"https://googleads.googleapis.com/v23/customers/{cid}/adGroupAds:mutate"
            ad_resp = _rq.post(ad_url, headers=_rest_headers, json={"operations": [{"create": {
                "adGroup": ag_rn,
                "status": "ENABLED",
                "ad": {
                    "finalUrls": [config["final_url"]],
                    "responsiveSearchAd": {
                        "headlines": headlines,
                        "descriptions": descriptions,
                    }
                }
            }}]})
            if ad_resp.status_code == 200:
                ad_created = True
                print(f"[AdsClient] RSA広告文作成完了: {ag_config['name']}")
            else:
                ad_created = False
                print(f"[AdsClient] RSA広告文作成エラー: {ad_resp.text[:300]}")

            ad_groups_result.append({
                "name": ag_config["name"],
                "id": ag_id,
                "keywords_added": kw_added,
                "ad_created": ad_created,
            })

        # ビジネス名・サイトリンクアセットの自動連携
        clinic_name = config.get("clinic_name")
        final_url = config.get("final_url")
        if campaign_id and clinic_name and final_url:
            self.link_business_name_and_sitelinks(campaign_id, clinic_name, final_url)

        return {
            "campaign_id": campaign_id,
            "campaign_name": config["campaign_name"],
            "status": config.get("status", "PAUSED"),
            "mock": False,
            "ad_groups": ad_groups_result,
        }

    def update_campaign_status(self, google_campaign_id: str, status: str):
        if self.mock_mode:
            print(f"[MOCK] ステータス更新: {google_campaign_id} -> {status}")
            return
        campaign_service = self._client.get_service("CampaignService")
        campaign_op = self._client.get_type("CampaignOperation")
        
        resource_name = f"customers/{self.customer_id}/campaigns/{google_campaign_id}"
        if status == "REMOVED":
            campaign_op.remove = resource_name
        else:
            campaign = campaign_op.update
            campaign.resource_name = resource_name
            status_enum = self._client.enums.CampaignStatusEnum[status]
            campaign.status = status_enum
            campaign_op.update_mask.paths.append("status")
            
        campaign_service.mutate_campaigns(customer_id=self.customer_id, operations=[campaign_op])

    def update_campaign_budget(self, google_campaign_id: str, budget_micros: int):
        """キャンペーンに紐づく予算（CampaignBudget）を変更する。"""
        if self.mock_mode:
            print(f"[MOCK] 予算更新: campaign_id={google_campaign_id} -> {budget_micros} micros")
            return
        
        # モックIDなど非数値のキャンペーンIDの場合は同期をスキップ
        if not google_campaign_id or not str(google_campaign_id).isdigit():
            print(f"[AdsClient] 数値以外のキャンペーンIDのため予算同期をスキップします: {google_campaign_id}")
            return
        
        # 1. キャンペーンに紐づく CampaignBudget の resource_name を取得
        ga_service = self._client.get_service("GoogleAdsService")
        query = f"""
            SELECT campaign.campaign_budget 
            FROM campaign 
            WHERE campaign.id = {google_campaign_id}
        """
        resp = ga_service.search(customer_id=self.customer_id, query=query)
        budget_rn = None
        for row in resp:
            budget_rn = row.campaign.campaign_budget
            break
            
        if not budget_rn:
            raise Exception(f"キャンペーン {google_campaign_id} に紐づく予算が見つかりません。")
            
        # 2. CampaignBudget の amount_micros を更新
        budget_service = self._client.get_service("CampaignBudgetService")
        b_op = self._client.get_type("CampaignBudgetOperation")
        
        b = b_op.update
        b.resource_name = budget_rn
        b.amount_micros = budget_micros
        b_op.update_mask.paths.append("amount_micros")
        
        budget_service.mutate_campaign_budgets(customer_id=self.customer_id, operations=[b_op])

    def get_this_month_cost(self) -> int:
        """今月の総消化コスト（micros）を取得する。"""
        if self.mock_mode:
            return 280_000 * 1_000_000  # モックデータ: 28万円分
        ga_service = self._client.get_service("GoogleAdsService")
        query = """
            SELECT metrics.cost_micros 
            FROM campaign 
            WHERE segments.date DURING THIS_MONTH
        """
        resp = ga_service.search(customer_id=self.customer_id, query=query)
        total_cost_micros = 0
        for row in resp:
            total_cost_micros += int(row.metrics.cost_micros)
        return total_cost_micros

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
            by_date[d]["impressions"] += m.impressions if m.impressions is not None else 0
            by_date[d]["clicks"] += m.clicks if m.clicks is not None else 0
            if m.ctr is not None:
                by_date[d]["ctr"].append(m.ctr * 100)
            if m.average_cpc is not None:
                by_date[d]["avg_cpc_micros"].append(int(m.average_cpc))
            by_date[d]["cost_micros"] += int(m.cost_micros) if m.cost_micros is not None else 0
            by_date[d]["conversions"] += m.conversions if m.conversions is not None else 0
            if m.conversions_from_interactions_rate is not None:
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
            ga_service = self._client.get_service("GoogleAdsService")
            campaign_criterion_service = self._client.get_service("CampaignCriterionService")

            # 競合重複を避けるため、既存のAD_SCHEDULEクライテリアを検索して一括削除
            try:
                query = f"""
                    SELECT campaign_criterion.resource_name
                    FROM campaign_criterion
                    WHERE campaign.id = '{campaign_id}'
                      AND campaign_criterion.type = 'AD_SCHEDULE'
                """
                search_response = ga_service.search(customer_id=self.customer_id, query=query)
                remove_operations = []
                for row in search_response:
                    op = self._client.get_type("CampaignCriterionOperation")
                    op.remove = row.campaign_criterion.resource_name
                    remove_operations.append(op)

                if remove_operations:
                    campaign_criterion_service.mutate_campaign_criteria(
                        customer_id=self.customer_id, operations=remove_operations
                    )
                    print(f"[AdsClient] 既存のスケジュールクライテリア {len(remove_operations)} 件を削除しました (Campaign: {campaign_id})")
            except Exception as ex_del:
                print(f"[AdsClient] 既存スケジュール削除中にエラーが発生しました（無視して続行）: {ex_del}")

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

    # ---- デモグラフィック入札調整（性別・年齢）---- ⑥
    def set_demographic_bid_adjustment(
        self,
        campaign_id: str,
        dimension: str,   # "gender" or "age"
        value: str,       # "MALE"/"FEMALE" or "AGE_RANGE_18_24" etc.
        adjustment_pct: int,  # -20 ~ +20 の整数（%）
    ) -> dict:
        """
        LOGICTIONのLTVデータに基づいてキャンペーンの性別・年齢別入札調整率を設定する。

        Args:
            campaign_id:     Google AdsキャンペーンID（数値文字列）
            dimension:       "gender" または "age"
            value:           性別: "MALE"/"FEMALE"  年齢: "AGE_RANGE_18_24" etc.
            adjustment_pct:  入札調整率（%）。-20〜+20。0は変更なし。
        Returns:
            dict: { "success": bool, "applied": bool, "adjustment_pct": int }
        """
        if self.mock_mode:
            print(f"[MOCK] デモグラフィック入札調整: campaign={campaign_id} {dimension}={value} adj={adjustment_pct:+}%")
            return {"success": True, "applied": True, "adjustment_pct": adjustment_pct, "mock": True}

        # 入力バリデーション
        adjustment_pct = max(-90, min(900, adjustment_pct))  # Google Ads APIの許容範囲
        bid_modifier = 1.0 + (adjustment_pct / 100.0)

        try:
            campaign_criterion_service = self._client.get_service("CampaignCriterionService")
            op = self._client.get_type("CampaignCriterionOperation")
            criterion = op.create
            criterion.campaign = f"customers/{self.customer_id}/campaigns/{campaign_id}"
            criterion.bid_modifier = bid_modifier

            if dimension == "gender":
                gender_map = {
                    "MALE":    self._client.enums.GenderTypeEnum.MALE,
                    "FEMALE":  self._client.enums.GenderTypeEnum.FEMALE,
                    "UNDETERMINED": self._client.enums.GenderTypeEnum.UNDETERMINED,
                }
                criterion.gender.type_ = gender_map.get(value, self._client.enums.GenderTypeEnum.UNDETERMINED)

            elif dimension == "age":
                age_map = {
                    "AGE_RANGE_18_24": self._client.enums.AgeRangeTypeEnum.AGE_RANGE_18_24,
                    "AGE_RANGE_25_34": self._client.enums.AgeRangeTypeEnum.AGE_RANGE_25_34,
                    "AGE_RANGE_35_44": self._client.enums.AgeRangeTypeEnum.AGE_RANGE_35_44,
                    "AGE_RANGE_45_54": self._client.enums.AgeRangeTypeEnum.AGE_RANGE_45_54,
                    "AGE_RANGE_55_64": self._client.enums.AgeRangeTypeEnum.AGE_RANGE_55_64,
                    "AGE_RANGE_65_UP": self._client.enums.AgeRangeTypeEnum.AGE_RANGE_65_UP,
                }
                if value not in age_map:
                    return {"success": False, "error": f"Unknown age range: {value}"}
                criterion.age_range.type_ = age_map[value]
            else:
                return {"success": False, "error": f"Unknown dimension: {dimension}"}

            campaign_criterion_service.mutate_campaign_criteria(
                customer_id=self.customer_id, operations=[op]
            )
            print(f"[AdsClient] デモグラフィック入札調整適用: campaign={campaign_id} {dimension}={value} modifier={bid_modifier:.2f}")
            return {"success": True, "applied": True, "adjustment_pct": adjustment_pct, "mock": False}

        except Exception as e:
            err = str(e)
            print(f"[AdsClient] デモグラフィック入札調整エラー: {err}")
            return {"success": False, "applied": False, "error": err}

    # ---- 除外キーワード 一括Push ----
    def push_negative_keywords(self, keywords: list) -> dict:
        """
        除外キーワードをGoogle Adsに一括登録する。
        各キャンペーンにnegative=TrueのCampaignCriterionとして直接追加する。

        Args:
            keywords: [{ "keyword": str, "match_type": str ("BROAD"/"PHRASE"/"EXACT") }, ...]

        Returns:
            dict: { "success": bool, "added": int, "skipped": int, "errors": list }
        """
        if self.mock_mode:
            print(f"[MOCK] 除外KW一括Push: {len(keywords)}件")
            return {
                "success": True,
                "added": len(keywords),
                "skipped": 0,
                "errors": [],
                "mock": True
            }

        added = 0
        skipped = 0
        errors = []

        try:
            ga_service = self._client.get_service("GoogleAdsService")
            campaign_criterion_service = self._client.get_service("CampaignCriterionService")

            # ① Searchキャンペーンのみ取得（VideoやDisplayは除外KW設定不可）
            query = """
                SELECT campaign.id, campaign.resource_name, campaign.status,
                       campaign.advertising_channel_type
                FROM campaign
                WHERE campaign.status != 'REMOVED'
                  AND campaign.advertising_channel_type = 'SEARCH'
            """
            resp = ga_service.search(customer_id=self.customer_id, query=query)
            campaigns = [(row.campaign.resource_name, str(row.campaign.id)) for row in resp]

            if not campaigns:
                return {
                    "success": True,
                    "added": 0,
                    "skipped": 0,
                    "errors": [],
                    "mock": False,
                    "message": "no_campaigns"
                }

            print(f"[AdsClient] 対象キャンペーン: {len(campaigns)}件")

            # ② マッチタイプマッピング
            match_type_map = {
                "BROAD":  self._client.enums.KeywordMatchTypeEnum.BROAD,
                "PHRASE": self._client.enums.KeywordMatchTypeEnum.PHRASE,
                "EXACT":  self._client.enums.KeywordMatchTypeEnum.EXACT,
            }

            # ③ 各キャンペーンの既存除外KWを取得してスキップ判定
            existing_per_camp = {}
            for camp_rn, camp_id in campaigns:
                try:
                    q = (
                        "SELECT campaign_criterion.keyword.text "
                        "FROM campaign_criterion "
                        f"WHERE campaign.resource_name = '{camp_rn}' "
                        "AND campaign_criterion.type = 'KEYWORD' "
                        "AND campaign_criterion.negative = TRUE"
                    )
                    cr = ga_service.search(customer_id=self.customer_id, query=q)
                    existing_per_camp[camp_rn] = set(
                        row.campaign_criterion.keyword.text.lower() for row in cr
                    )
                except Exception:
                    existing_per_camp[camp_rn] = set()

            # ④ 各キャンペーンに除外KWを追加
            BATCH = 2000
            for camp_rn, camp_id in campaigns:
                existing = existing_per_camp.get(camp_rn, set())
                operations = []
                for kw_data in keywords:
                    kw_text = clean_keyword_text(kw_data["keyword"])
                    if not kw_text:
                        skipped += 1
                        continue
                    if kw_text.lower() in existing:
                        skipped += 1
                        continue
                    match_type = match_type_map.get(
                        kw_data.get("match_type", "BROAD").upper(),
                        self._client.enums.KeywordMatchTypeEnum.BROAD
                    )
                    op = self._client.get_type("CampaignCriterionOperation")
                    criterion = op.create
                    criterion.campaign = camp_rn
                    criterion.negative = True
                    criterion.keyword.text = kw_text
                    criterion.keyword.match_type = match_type
                    operations.append(op)

                if not operations:
                    continue

                for i in range(0, len(operations), BATCH):
                    batch = operations[i:i + BATCH]
                    try:
                        result_resp = campaign_criterion_service.mutate_campaign_criteria(
                            customer_id=self.customer_id, operations=batch
                        )
                        added += len(result_resp.results)
                    except Exception as e:
                        full_err = str(e)
                        errors.append(f"Campaign {camp_id}: {full_err}")
                        print(f"[AdsClient] キャンペーン {camp_id} 除外KW追加エラー FULL: {full_err}")

            print(f"[AdsClient] 除外KW Push完了: 追加={added}件, スキップ={skipped}件, エラー={len(errors)}件")
            return {
                "success": added > 0 or (len(errors) == 0),
                "added": added,
                "skipped": skipped,
                "errors": errors[:5],
                "mock": False
            }

        except Exception as e:
            print(f"[AdsClient] 除外KW Push 致命的エラー: {e}")
            return {"success": False, "added": 0, "skipped": 0, "errors": [str(e)[:500]], "mock": False}

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

    def suggest_geo_target_constants(self, location_names: list[str]) -> list[str]:
        """地名(例: '藤枝市')からGeoTargetConstantリソース名を解決する"""
        if self.mock_mode:
            return [f"geoTargetConstants/{random.randint(100000, 999999)}" for _ in location_names]

        gtc_service = self._client.get_service("GeoTargetConstantService")
        request = self._client.get_type("SuggestGeoTargetConstantsRequest")
        request.locale = "ja"
        request.country_code = "JP"
        for name in location_names:
            request.location_names.names.append(name)

        try:
            response = gtc_service.suggest_geo_target_constants(request=request)
            resource_names = []
            for suggestion in response.geo_target_constant_suggestions:
                constant = suggestion.geo_target_constant
                print(f"[AdsClient] GeoTarget解決: {constant.canonical_name} ({constant.resource_name})")
                resource_names.append(constant.resource_name)
            return resource_names
        except Exception as e:
            print(f"[AdsClient] GeoTarget解決エラー: {e}")
            return []

    def update_campaign_location(self, campaign_id: str, loc_config: dict) -> dict:
        """
        キャンペーンの位置情報（配信半径、地域）を更新する。
        既存のLOCATION/PROXIMITYを削除し、新しいターゲットを追加する。
        """
        if self.mock_mode:
            print(f"[MOCK] 位置情報更新: campaign={campaign_id} config={loc_config}")
            return {"success": True, "mock": True}

        try:
            ga_service = self._client.get_service("GoogleAdsService")
            campaign_criterion_service = self._client.get_service("CampaignCriterionService")

            # 1. 既存の LOCATION または PROXIMITY を検索して削除
            remove_operations = []
            try:
                query = f"""
                    SELECT campaign_criterion.resource_name
                    FROM campaign_criterion
                    WHERE campaign.id = '{campaign_id}'
                      AND campaign_criterion.type IN ('LOCATION', 'PROXIMITY')
                      AND campaign_criterion.status != 'REMOVED'
                """
                search_response = ga_service.search(customer_id=self.customer_id, query=query)
                for row in search_response:
                    op = self._client.get_type("CampaignCriterionOperation")
                    op.remove = row.campaign_criterion.resource_name
                    remove_operations.append(op)

                if remove_operations:
                    campaign_criterion_service.mutate_campaign_criteria(
                        customer_id=self.customer_id, operations=remove_operations
                    )
                    print(f"[AdsClient] 既存の位置ターゲティング {len(remove_operations)} 件を削除しました (Campaign: {campaign_id})")
            except Exception as ex_del:
                print(f"[AdsClient] 既存位置ターゲット削除中にエラー（無視して続行）: {ex_del}")

            # 2. 新しい位置ターゲットを追加
            create_operations = []
            loc_type = loc_config.get("type", "proximity")

            if loc_type == "proximity":
                lat = loc_config.get("lat")
                lon = loc_config.get("lon")
                radius = loc_config.get("radius_km", 8)
                if lat is not None and lon is not None:
                    op = self._client.get_type("CampaignCriterionOperation")
                    criterion = op.create
                    criterion.campaign = f"customers/{self.customer_id}/campaigns/{campaign_id}"
                    criterion.proximity.geo_point.latitude_in_micro_degrees = int(lat * 1_000_000)
                    criterion.proximity.geo_point.longitude_in_micro_degrees = int(lon * 1_000_000)
                    criterion.proximity.radius = float(radius)
                    criterion.proximity.radius_units = self._client.enums.ProximityRadiusUnitsEnum.KILOMETERS
                    create_operations.append(op)
            elif loc_type == "geo_target":
                geo_targets = loc_config.get("geo_targets", [])
                if geo_targets:
                    resource_names = self.suggest_geo_target_constants(geo_targets)
                    for r_name in resource_names:
                        op = self._client.get_type("CampaignCriterionOperation")
                        criterion = op.create
                        criterion.campaign = f"customers/{self.customer_id}/campaigns/{campaign_id}"
                        criterion.location.geo_target_constant = r_name
                        create_operations.append(op)

            if create_operations:
                resp = campaign_criterion_service.mutate_campaign_criteria(
                    customer_id=self.customer_id, operations=create_operations
                )
                print(f"[AdsClient] 新しい位置ターゲティング {len(create_operations)} 件を追加しました")
                return {"success": True, "added_count": len(resp.results), "mock": False}
            else:
                return {"success": False, "error": "追加する位置情報が指定されていません", "mock": False}

        except Exception as e:
            print(f"[AdsClient] 位置情報更新エラー: {e}")
            return {"success": False, "error": str(e), "mock": False}

    def update_campaign_rsa(self, google_campaign_id: str, headlines: list[str] = None, descriptions: list[str] = None, final_url: str = None, clinic_name: str = None) -> dict:
        """
        キャンペーン内の広告グループのアクティブなRSA（レスポンシブ検索広告）を更新する。
        既存のRSAを検索し、headlinesとdescriptions、およびfinal_urlを新しいもので上書きする。
        """
        if self.mock_mode:
            print(f"[MOCK] RSA更新: campaign={google_campaign_id} H={headlines} D={descriptions} URL={final_url} clinic_name={clinic_name}")
            return {"success": True, "mock": True}

        try:
            token = self._get_rest_access_token()
            cid = self.customer_id
            BASE = f"https://googleads.googleapis.com/v23/customers/{cid}"
            _rest_headers = {
                "Authorization": f"Bearer {token}",
                "developer-token": self._developer_token,
                "login-customer-id": self._login_customer_id,
                "Content-Type": "application/json",
            }

            import requests as _rq

            # 1. キャンペーン内の最初の広告グループを取得
            ag_query = f"SELECT ad_group.resource_name FROM ad_group WHERE campaign.id = '{google_campaign_id}' AND ad_group.status != 'REMOVED' LIMIT 1"
            ag_resp = _rq.post(f"{BASE}/googleAds:searchStream", headers=_rest_headers, json={"query": ag_query})
            ag_rn = None
            if ag_resp.status_code == 200:
                for batch in ag_resp.json():
                    for row in batch.get("results", []):
                        ag_rn = row.get("adGroup", {}).get("resourceName")
                        break
                    if ag_rn: break

            if not ag_rn:
                return {"success": False, "error": "キャンペーン内に広告グループが見つかりません"}

            # 2. 既存の RSA 広告を検索
            # 2. 既存の RSA 広告を検索
            ad_query = (
                f"SELECT ad_group_ad.resource_name, ad_group_ad.ad.final_urls, "
                f"ad_group_ad.ad.responsive_search_ad.headlines, ad_group_ad.ad.responsive_search_ad.descriptions "
                f"FROM ad_group_ad "
                f"WHERE ad_group.resource_name = '{ag_rn}' AND ad_group_ad.status != 'REMOVED' "
                f"AND ad_group_ad.ad.type = 'RESPONSIVE_SEARCH_AD' LIMIT 1"
            )
            ad_resp = _rq.post(f"{BASE}/googleAds:searchStream", headers=_rest_headers, json={"query": ad_query})
            ad_rn = None  # ad_group_ad の resourceName
            existing_final_urls = []
            existing_headlines = []
            existing_descriptions = []
            if ad_resp.status_code == 200:
                for batch in ad_resp.json():
                    for row in batch.get("results", []):
                        ad_group_ad = row.get("adGroupAd", {})
                        ad_rn = ad_group_ad.get("resourceName")
                        ad = ad_group_ad.get("ad", {})
                        existing_final_urls = ad.get("finalUrls", [])
                        
                        rsa = ad.get("responsiveSearchAd", {})
                        existing_headlines = [h.get("text", "") for h in rsa.get("headlines", []) if h.get("text")]
                        existing_descriptions = [d.get("text", "") for d in rsa.get("descriptions", []) if d.get("text")]
                        break
                    if ad_rn: break

            # 3. 広告文を構成
            target_headlines = headlines if headlines is not None else existing_headlines
            target_descriptions = descriptions if descriptions is not None else existing_descriptions

            # Noneや空文字、改行コードを含んだゴミデータの除去・トリミング
            target_headlines = [hl.strip() for hl in target_headlines if hl and hl.strip()]
            target_descriptions = [d.strip() for d in target_descriptions if d and d.strip()]

            # RSAの仕様: 見出しは最低3件、説明文は最低2件必要
            if len(target_headlines) < 3:
                fallbacks = ["静岡県藤枝市の整体院", "肩こり・腰痛はお任せください", "根本改善を目指す整体院"]
                for f in fallbacks:
                    if f not in target_headlines:
                        target_headlines.append(f)
                    if len(target_headlines) >= 3:
                        break

            if len(target_descriptions) < 2:
                fallbacks = ["国家資格保持者による施術で安心。藤枝駅徒歩3分。", "痛みの原因へアプローチし根本改善を目指します。"]
                for f in fallbacks:
                    if f not in target_descriptions:
                        target_descriptions.append(f)
                    if len(target_descriptions) >= 2:
                        break

            formatted_headlines = [{"text": hl[:30]} for hl in target_headlines[:15]]
            formatted_descriptions = [{"text": d[:45]} for d in target_descriptions[:4]]

            final_urls = [final_url] if final_url else existing_final_urls
            if not final_urls:
                final_urls = ["https://michibiki-seitai.com"]

            ad_payload = {
                "finalUrls": final_urls,
                "responsiveSearchAd": {
                    "headlines": formatted_headlines,
                    "descriptions": formatted_descriptions,
                }
            }

            # 4. ミューテーションの作成（Google Ads APIでは広告の中身はUPDATE不可のため、REMOVEして再作成する）
            ops = []
            if ad_rn:
                ops.append({
                    "remove": ad_rn
                })
            
            ops.append({
                "create": {
                    "adGroup": ag_rn,
                    "status": "ENABLED",
                    "ad": ad_payload
                }
            })

            url = f"{BASE}/adGroupAds:mutate"
            resp = _rq.post(url, headers=_rest_headers, json={"operations": ops})
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                new_ad_rn = ""
                for r in results:
                    ref_name = r.get("resourceName", "")
                    if ref_name and "adGroupAds" in ref_name:
                        new_ad_rn = ref_name
                
                # ビジネス名・サイトリンクアセットの自動連携
                if clinic_name and final_urls:
                    self.link_business_name_and_sitelinks(google_campaign_id, clinic_name, final_urls[0])

                if ad_rn:
                    print(f"[AdsClient] 既存のRSA広告 {ad_rn} を削除し、新規に作成しました: {new_ad_rn}")
                    return {"success": True, "updated": True, "resource": new_ad_rn}
                else:
                    print(f"[AdsClient] 新規にRSA広告を作成しました: {new_ad_rn}")
                    return {"success": True, "created": True, "resource": new_ad_rn}
            else:
                try:
                    err_json = resp.json()
                    fail_details = err_json.get("error", {}).get("details", [{}])[0].get("errors", [])
                    err_msgs = [e.get("message", "") for e in fail_details if e.get("message")]
                    err_code = ""
                    if fail_details:
                        err_code = str(fail_details[0].get("errorCode", "")) or str(fail_details[0].get("errorType", ""))
                    err_desc = f"{err_code}: {', '.join(err_msgs)}" if err_msgs else resp.text[:1000]
                except Exception:
                    err_desc = resp.text[:1000]
                print(f"[AdsClient] RSA広告の削除・作成失敗詳細: {err_desc}")
                return {"success": False, "error": f"RSA広告更新エラー: {err_desc}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_accessible_customers(self) -> dict:
        """アクセス可能なGoogle広告のCustomer IDおよびアカウント一覧を取得する。"""
        if self.mock_mode:
            return {
                "success": True,
                "customers": [
                    {"id": "8110558709", "name": "整体院導 (テスト)", "is_manager": False},
                    {"id": "1234567890", "name": "サブ治療院アカウント", "is_manager": False},
                    {"id": "9998887776", "name": "管理用MCCアカウント", "is_manager": True},
                ],
                "mock": True
            }

        try:
            token = self._get_rest_access_token()
            url = "https://googleads.googleapis.com/v23/customers:listAccessibleCustomers"
            headers = {
                "Authorization": f"Bearer {token}",
                "developer-token": self._developer_token,
                "Content-Type": "application/json",
            }
            import requests as _rq
            resp = _rq.get(url, headers=headers)
            if resp.status_code != 200:
                return {"success": False, "error": f"listAccessibleCustomersエラー: {resp.text[:300]}"}
            
            resource_names = resp.json().get("resourceNames", [])
            customers = []
            
            for rn in resource_names:
                cid = rn.split("/")[-1]
                query_headers = {
                    "Authorization": f"Bearer {token}",
                    "developer-token": self._developer_token,
                    "Content-Type": "application/json",
                }
                query_url = f"https://googleads.googleapis.com/v23/customers/{cid}/googleAds:searchStream"
                query = "SELECT customer.id, customer.descriptive_name, customer.manager FROM customer LIMIT 1"
                q_resp = _rq.post(query_url, headers=query_headers, json={"query": query})
                if q_resp.status_code == 200:
                    try:
                        for batch in q_resp.json():
                            for row in batch.get("results", []):
                                c = row.get("customer", {})
                                customers.append({
                                    "id": str(c.get("id", cid)),
                                    "name": c.get("descriptiveName", f"アカウント ({cid})"),
                                    "is_manager": c.get("manager", False)
                                })
                    except Exception:
                        customers.append({"id": cid, "name": f"アカウント ({cid})", "is_manager": False})
                else:
                    customers.append({"id": cid, "name": f"アカウント ({cid})", "is_manager": False})

            return {"success": True, "customers": customers, "mock": False}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def upload_image_asset(self, image_data_b64: str, asset_name: str = None) -> dict:
        """
        Google広告に画像アセットをアップロード・登録する。
        """
        import uuid
        name = asset_name or f"admu_img_{uuid.uuid4().hex[:8]}"

        if self.mock_mode:
            mock_asset_id = str(uuid.uuid4().int)[:12]
            mock_rn = f"customers/{self.customer_id}/assets/{mock_asset_id}"
            print(f"[MOCK] 画像アセット登録: name={name} resource={mock_rn}")
            return {"success": True, "resource_name": mock_rn, "mock": True}

        try:
            token = self._get_rest_access_token()
            cid = self.customer_id
            BASE = f"https://googleads.googleapis.com/v23/customers/{cid}"
            headers_rest = {
                "Authorization": f"Bearer {token}",
                "developer-token": self._developer_token,
                "login-customer-id": self._login_customer_id,
                "Content-Type": "application/json",
            }

            import requests as _rq

            op = {
                "create": {
                    "type": "IMAGE",
                    "name": name,
                    "imageAsset": {
                        "data": image_data_b64
                    }
                }
            }

            url = f"{BASE}/assets:mutate"
            resp = _rq.post(url, headers=headers_rest, json={"operations": [op]})
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                if results and results[0].get("resourceName"):
                    rn = results[0]["resourceName"]
                    print(f"[AdsClient] 画像アセット登録成功: {rn}")
                    return {"success": True, "resource_name": rn, "mock": False}
                else:
                    return {"success": False, "error": "アセット登録結果が空です"}
            else:
                return {"success": False, "error": f"アセット登録エラー: {resp.text[:300]}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def link_asset_to_campaign(self, google_campaign_id: str, asset_resource_name: str, field_type: str = "MARKETING_IMAGE") -> dict:
        """
        画像アセットをキャンペーンに関連付ける (CampaignAssetの登録)。
        """
        if self.mock_mode:
            print(f"[MOCK] アセット関連付け: campaign={google_campaign_id} asset={asset_resource_name} type={field_type}")
            return {"success": True, "mock": True}

        try:
            token = self._get_rest_access_token()
            cid = self.customer_id
            BASE = f"https://googleads.googleapis.com/v23/customers/{cid}"
            headers_rest = {
                "Authorization": f"Bearer {token}",
                "developer-token": self._developer_token,
                "login-customer-id": self._login_customer_id,
                "Content-Type": "application/json",
            }

            import requests as _rq

            op = {
                "create": {
                    "campaign": f"customers/{cid}/campaigns/{google_campaign_id}",
                    "asset": asset_resource_name,
                    "fieldType": field_type.upper()
                }
            }

            url = f"{BASE}/campaignAssets:mutate"
            resp = _rq.post(url, headers=headers_rest, json={"operations": [op]})
            if resp.status_code == 200:
                print(f"[AdsClient] アセットをキャンペーンに関連付けました: {asset_resource_name} -> {google_campaign_id}")
                return {"success": True, "mock": False}
            else:
                return {"success": False, "error": f"キャンペーンアセット関連付けエラー: {resp.text[:300]}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def upload_offline_conversion_value(self, gclid: str, conversion_action_id: str, conversion_time_str: str, value: float) -> dict:
        """
        GCLIDに紐づくオフラインコンバージョン値（LTV売上）をGoogle広告へフィードバックアップロードする。
        """
        if self.mock_mode:
            print(f"[MOCK] オフラインCV値フィードバック: gclid={gclid} action_id={conversion_action_id} value={value}円")
            return {"success": True, "mock": True}

        try:
            token = self._get_rest_access_token()
            cid = self.customer_id
            BASE = f"https://googleads.googleapis.com/v23/customers/{cid}"
            headers_rest = {
                "Authorization": f"Bearer {token}",
                "developer-token": self._developer_token,
                "login-customer-id": self._login_customer_id,
                "Content-Type": "application/json",
            }

            import requests as _rq

            payload = {
                "conversions": [
                    {
                        "gclid": gclid,
                        "conversionAction": f"customers/{cid}/conversionActions/{conversion_action_id}",
                        "conversionDateTime": conversion_time_str,
                        "conversionValue": value,
                        "currencyCode": "JPY"
                    }
                ],
                "partialFailure": True
            }

            url = f"{BASE}:uploadClickConversions"
            resp = _rq.post(url, headers=headers_rest, json=payload)
            if resp.status_code == 200:
                print(f"[AdsClient] オフラインコンバージョン値アップロード成功: gclid={gclid} value={value}")
                return {"success": True, "mock": False}
            else:
                return {"success": False, "error": f"CVアップロードエラー: {resp.text[:300]}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def exclude_campaign_location(self, google_campaign_id: str, geo_target_constant_id: str) -> dict:
        """
        キャンペーンに除外ターゲット地域（Exclusion Area）を追加・登録する。
        """
        if self.mock_mode:
            print(f"[MOCK] キャンペーン除外地域追加: campaign={google_campaign_id} geo_id={geo_target_constant_id}")
            return {"success": True, "mock": True}

        try:
            token = self._get_rest_access_token()
            cid = self.customer_id
            BASE = f"https://googleads.googleapis.com/v23/customers/{cid}"
            headers_rest = {
                "Authorization": f"Bearer {token}",
                "developer-token": self._developer_token,
                "login-customer-id": self._login_customer_id,
                "Content-Type": "application/json",
            }

            import requests as _rq

            op = {
                "create": {
                    "campaign": f"customers/{cid}/campaigns/{google_campaign_id}",
                    "negative": True,
                    "location": {
                        "geoTargetConstant": f"geoTargetConstants/{geo_target_constant_id}"
                    }
                }
            }

            url = f"{BASE}/campaignCriteria:mutate"
            resp = _rq.post(url, headers=headers_rest, json={"operations": [op]})
            if resp.status_code == 200:
                print(f"[AdsClient] キャンペーン除外地域を追加しました: campaign={google_campaign_id} geo_id={geo_target_constant_id}")
                return {"success": True, "mock": False}
            else:
                return {"success": False, "error": f"除外地域登録エラー: {resp.text[:300]}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def link_business_name_and_sitelinks(self, google_campaign_id: str, clinic_name: str, final_url: str) -> dict:
        """
        キャンペーンに対して、クリニックの「ビジネス名」アセットと、予約用「サイトリンク」アセットを自動的に紐付け・登録する。
        """
        if self.mock_mode:
            print(f"[MOCK] ビジネス名・サイトリンクアセット適用: campaign={google_campaign_id} clinic={clinic_name} url={final_url}")
            return {"success": True, "mock": True}

        try:
            token = self._get_rest_access_token()
            cid = self.customer_id
            BASE = f"https://googleads.googleapis.com/v23/customers/{cid}"
            headers_rest = {
                "Authorization": f"Bearer {token}",
                "developer-token": self._developer_token,
                "login-customer-id": self._login_customer_id,
                "Content-Type": "application/json",
            }

            import requests as _rq

            # ビジネス名は最大25文字
            clean_clinic_name = clinic_name.strip()[:25]
            campaign_rn = f"customers/{cid}/campaigns/{google_campaign_id}"

            # ==========================================
            # 1. ビジネス名アセットの検索または作成
            # ==========================================
            business_asset_rn = None
            search_query = f"SELECT asset.resource_name FROM asset WHERE asset.type = 'BUSINESS_NAME'"
            search_resp = _rq.post(f"{BASE}/googleAds:searchStream", headers=headers_rest, json={"query": search_query})
            
            if search_resp.status_code == 200:
                for batch in search_resp.json():
                    for row in batch.get("results", []):
                        asset = row.get("asset", {})
                        # 既存アセットがあれば再利用（同名か簡易確認）
                        if asset.get("businessNameAsset", {}).get("businessName") == clean_clinic_name:
                            business_asset_rn = asset.get("resourceName")
                            break
                    if business_asset_rn: break

            if not business_asset_rn:
                # 存在しない場合は作成
                create_op = {
                    "create": {
                        "type": "BUSINESS_NAME",
                        "businessNameAsset": {
                            "businessName": clean_clinic_name
                        }
                    }
                }
                asset_resp = _rq.post(f"{BASE}/assets:mutate", headers=headers_rest, json={"operations": [create_op]})
                if asset_resp.status_code == 200:
                    business_asset_rn = asset_resp.json().get("results", [{}])[0].get("resourceName")
                    print(f"[AdsClient] ビジネス名アセットを作成しました: {business_asset_rn}")
                else:
                    print(f"[AdsClient] ビジネス名アセット作成スキップ/エラー: {asset_resp.text[:200]}")

            # ビジネス名アセットをキャンペーンに紐付け
            if business_asset_rn:
                link_op = {
                    "create": {
                        "campaign": campaign_rn,
                        "asset": business_asset_rn,
                        "fieldType": "BUSINESS_NAME"
                    }
                }
                # すでに紐付いている場合の重複エラーを許容する
                link_resp = _rq.post(f"{BASE}/campaignAssets:mutate", headers=headers_rest, json={"operations": [link_op]})
                if link_resp.status_code == 200:
                    print(f"[AdsClient] キャンペーンにビジネス名アセットを紐付けました")
                else:
                    print(f"[AdsClient] ビジネス名紐付け（既に存在するかエラー）: {link_resp.text[:150]}")

            # ==========================================
            # 2. サイトリンクアセット（2件）の検索または作成・紐付け
            # ==========================================
            sitelinks_to_create = [
                {
                    "text": "オンライン予約はこちら",
                    "desc1": "24時間LINEから簡単予約受付中",
                    "desc2": "初めての方もお気軽にご相談ください"
                },
                {
                    "text": "当院の特徴・施術メニュー",
                    "desc1": "根本改善を目指す独自の整体技術",
                    "desc2": "腰痛や肩こりなど重症例に対応"
                }
            ]

            # 既存のサイトリンクアセットを取得
            existing_sitelinks = {}
            sl_query = f"SELECT asset.resource_name, asset.sitelink_asset.link_text FROM asset WHERE asset.type = 'SITELINK'"
            sl_resp = _rq.post(f"{BASE}/googleAds:searchStream", headers=headers_rest, json={"query": sl_query})
            if sl_resp.status_code == 200:
                for batch in sl_resp.json():
                    for row in batch.get("results", []):
                        asset = row.get("asset", {})
                        txt = asset.get("sitelinkAsset", {}).get("linkText")
                        if txt:
                            existing_sitelinks[txt] = asset.get("resourceName")

            for sl in sitelinks_to_create:
                sl_text = sl["text"][:25]
                sl_rn = existing_sitelinks.get(sl_text)

                if not sl_rn:
                    # アセット新規作成
                    sl_op = {
                        "create": {
                            "type": "SITELINK",
                            "sitelinkAsset": {
                                "linkText": sl_text,
                                "description1": sl["desc1"][:35],
                                "description2": sl["desc2"][:35],
                                "finalUrls": [final_url]
                            }
                        }
                    }
                    asset_resp = _rq.post(f"{BASE}/assets:mutate", headers=headers_rest, json={"operations": [sl_op]})
                    if asset_resp.status_code == 200:
                        sl_rn = asset_resp.json().get("results", [{}])[0].get("resourceName")
                        print(f"[AdsClient] サイトリンクアセットを作成しました: {sl_rn}")
                    else:
                        print(f"[AdsClient] サイトリンク作成エラー: {asset_resp.text[:200]}")

                if sl_rn:
                    # キャンペーンに紐付け
                    link_op = {
                        "create": {
                            "campaign": campaign_rn,
                            "asset": sl_rn,
                            "fieldType": "SITELINK"
                        }
                    }
                    link_resp = _rq.post(f"{BASE}/campaignAssets:mutate", headers=headers_rest, json={"operations": [link_op]})
                    if link_resp.status_code == 200:
                        print(f"[AdsClient] キャンペーンにサイトリンク「{sl_text}」を紐付けました")
                    else:
                        print(f"[AdsClient] サイトリンク紐付け（既に存在するかエラー）: {link_resp.text[:150]}")

            return {"success": True}

        except Exception as e:
            print(f"[AdsClient] アセット連携に失敗（処理は続行します）: {e}")
            return {"success": False, "error": str(e)}
