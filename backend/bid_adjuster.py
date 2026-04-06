"""
bid_adjuster.py - ルールベース自動入札調整エンジン
"""
from __future__ import annotations
import db
from ads_client import AdsClient


class BidAdjuster:
    """
    入札ルールを評価し、Google Ads API経由で入札を自動調整する。
    予算には一切触れない（budget_micros は campaign_manager が手動管理）。
    """

    MAX_SINGLE_ADJUSTMENT_PCT = 30.0  # 1回の調整上限

    def __init__(self, clinic_id: int, ads_client: AdsClient):
        self.clinic_id = clinic_id
        self.ads_client = ads_client

    def run(self) -> list[dict]:
        """
        全入札ルールを評価し、条件に一致したルールを実行。
        実行ログのリストを返す。
        """
        rules = db.list_bid_rules(self.clinic_id)
        perf_list = self.ads_client.get_performance_series(days=7)
        if not perf_list:
            return []

        # 直近パフォーマンス（最新日）
        latest = perf_list[-1] if perf_list else {}
        avg = self._avg_perf(perf_list)

        logs = []
        for rule in rules:
            if not rule.get("enabled"):
                continue
            value = self._get_field(latest, avg, rule["condition_field"])
            if value is None:
                continue
            triggered = self._evaluate(value, rule["condition_op"], rule["condition_value"])
            if triggered:
                result = self._apply_action(rule, value)
                logs.append({
                    "rule_id": rule["id"],
                    "rule_name": rule["name"],
                    "field": rule["condition_field"],
                    "value": value,
                    "action": rule["action"],
                    "action_value": rule["action_value"],
                    "result": result,
                })
                db.create_alert(
                    self.clinic_id,
                    f"[入札調整] {rule['name']}: {rule['condition_field']}={value:.2f} → {rule['action']} {rule['action_value']}%",
                    level="INFO",
                )
        return logs

    def _avg_perf(self, perf_list: list[dict]) -> dict:
        if not perf_list:
            return {}
        keys = ["impressions", "clicks", "ctr", "avg_cpc_micros", "cost_micros", "conversions", "cvr"]
        return {k: sum(p.get(k, 0) for p in perf_list) / len(perf_list) for k in keys}

    def _get_field(self, latest: dict, avg: dict, field: str):
        mapping = {
            "ctr": latest.get("ctr"),
            "cvr": latest.get("cvr"),
            "avg_cpc": latest.get("avg_cpc_micros", 0) / 1_000_000,
            "cost": latest.get("cost_micros", 0) / 1_000_000,
            "impressions": latest.get("impressions"),
            "clicks": latest.get("clicks"),
            "conversions": latest.get("conversions"),
            "avg_ctr_7d": avg.get("ctr"),
            "avg_cvr_7d": avg.get("cvr"),
        }
        return mapping.get(field)

    def _evaluate(self, value: float, op: str, threshold: float) -> bool:
        ops = {
            "gt": value > threshold,
            "gte": value >= threshold,
            "lt": value < threshold,
            "lte": value <= threshold,
            "eq": value == threshold,
        }
        return ops.get(op, False)

    def _apply_action(self, rule: dict, current_value: float) -> str:
        action = rule["action"]
        action_value = float(rule["action_value"])
        max_pct = min(float(rule.get("max_adjustment_pct", 20.0)), self.MAX_SINGLE_ADJUSTMENT_PCT)

        if action == "increase_bid_pct":
            pct = min(action_value, max_pct)
            self.ads_client.adjust_keyword_bid("*", "*", int(100 * (1 + pct / 100)))
            return f"入札 +{pct:.1f}%"
        elif action == "decrease_bid_pct":
            pct = min(action_value, max_pct)
            self.ads_client.adjust_keyword_bid("*", "*", int(100 * (1 - pct / 100)))
            return f"入札 -{pct:.1f}%"
        elif action == "pause_campaign":
            if rule.get("campaign_id"):
                campaign = db.get_campaign(rule["campaign_id"])
                if campaign:
                    self.ads_client.update_campaign_status(
                        campaign.get("google_campaign_id", ""), "PAUSED")
            return "キャンペーン一時停止"
        return "不明なアクション"


def run_bid_adjustment(clinic_id: int, account_config: dict) -> list[dict]:
    """外部から呼び出すエントリポイント"""
    client = AdsClient(account_config)
    adjuster = BidAdjuster(clinic_id, client)
    return adjuster.run()
