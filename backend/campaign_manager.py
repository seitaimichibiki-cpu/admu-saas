"""
campaign_manager.py - キャンペーン自動生成マネージャー
"""
import db
from ads_client import AdsClient

# 整体院向けキャンペーンテンプレート
CAMPAIGN_TEMPLATES = {
    "腰痛": {
        "name_suffix": "腰痛・坐骨神経痛",
        "keywords": ["腰痛 整体", "腰痛 治療院", "坐骨神経痛 改善", "ぎっくり腰 治療"],
        "budget_yen_default": 3000,
    },
    "肩こり": {
        "name_suffix": "肩こり・首こり",
        "keywords": ["肩こり 整体", "首こり 治療", "頭痛 整体", "肩こり 改善"],
        "budget_yen_default": 2000,
    },
    "産後骨盤": {
        "name_suffix": "産後骨盤矯正",
        "keywords": ["産後 骨盤矯正", "産後骨盤 整体", "産後 整体院", "骨盤矯正 産後"],
        "budget_yen_default": 3000,
    },
    "姿勢矯正": {
        "name_suffix": "姿勢矯正・猫背",
        "keywords": ["姿勢矯正 整体", "猫背 改善", "姿勢 治療院", "体の歪み 整体"],
        "budget_yen_default": 2000,
    },
    "スポーツ": {
        "name_suffix": "スポーツ障害",
        "keywords": ["スポーツ 整体", "スポーツ障害 治療", "膝痛 整体", "肉離れ 治療"],
        "budget_yen_default": 2000,
    },
}


def auto_create_campaign(clinic_id: int, account_config: dict, params: dict) -> dict:
    """
    キャンペーンを自動生成する。

    params:
        clinic_name: str
        region: str (例: "渋谷区")
        category: str (例: "腰痛")
        budget_yen: int (手動設定。オプション)
    """
    category = params.get("category", "腰痛")
    template = CAMPAIGN_TEMPLATES.get(category, CAMPAIGN_TEMPLATES["腰痛"])
    region = params.get("region", "")
    clinic_name = params.get("clinic_name", "整体院")

    campaign_name = f"[{region}]{clinic_name}_{template['name_suffix']}"
    budget_yen = params.get("budget_yen", template["budget_yen_default"])
    budget_micros = budget_yen * 1_000_000  # 1日予算

    platform = params.get("platform", "google")
    
    if platform == "yahoo":
        from yahoo_ads_client import YahooAdsClient
        client = YahooAdsClient(account_config)
    else:
        from ads_client import AdsClient
        client = AdsClient(account_config)

    google_campaign_id = client.create_campaign(
        name=campaign_name,
        budget_micros=budget_micros,
        target_region=region,
        campaign_type="SEARCH",
    )

    # DBに保存
    campaign_id = db.upsert_campaign(clinic_id, {
        "name": campaign_name,
        "status": "ENABLED",
        "budget_micros": budget_micros,
        "campaign_type": "SEARCH",
        "target_region": region,
        "google_campaign_id": google_campaign_id,
    })

    # デフォルト入札ルールを自動追加
    _add_default_bid_rules(clinic_id, campaign_id, category)

    return {
        "campaign_id": campaign_id,
        "google_campaign_id": google_campaign_id,
        "name": campaign_name,
        "keywords": template["keywords"],
        "budget_yen": budget_yen,
    }


def _add_default_bid_rules(clinic_id: int, campaign_id: int, category: str):
    """カテゴリに応じたデフォルト入札ルールを追加"""
    default_rules = [
        {
            "name": "CTR低下時に入札減少",
            "condition_field": "ctr",
            "condition_op": "lt",
            "condition_value": 1.0,
            "action": "decrease_bid_pct",
            "action_value": 15.0,
            "max_adjustment_pct": 15.0,
            "campaign_id": campaign_id,
        },
        {
            "name": "CTR好調時に入札増加",
            "condition_field": "ctr",
            "condition_op": "gte",
            "condition_value": 5.0,
            "action": "increase_bid_pct",
            "action_value": 10.0,
            "max_adjustment_pct": 20.0,
            "campaign_id": campaign_id,
        },
        {
            "name": "CVR低下時に入札減少",
            "condition_field": "cvr",
            "condition_op": "lt",
            "condition_value": 1.0,
            "action": "decrease_bid_pct",
            "action_value": 10.0,
            "max_adjustment_pct": 20.0,
            "campaign_id": campaign_id,
        },
    ]
    for rule in default_rules:
        db.upsert_bid_rule(clinic_id, rule)
