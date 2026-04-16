import os
import logging
from typing import Dict, Any, List

# 本番時に使用するSDK (現状はモック運用のためインポートのみ記述し未初期化)
try:
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        DateRange,
        Dimension,
        Metric,
        RunReportRequest,
    )
except ImportError:
    pass

logger = logging.getLogger(__name__)

class GA4Client:
    """
    GA4 APIへの接続とデータ取得を抽象化するクライアント。
    本番環境の認証情報 (Google Cloud Service Account) が提供されるまではモックデータで応答。
    """
    def __init__(self, property_id: str):
        self.property_id = property_id
        self.is_mock = os.getenv("MOCK_ADS_API", "true").lower() == "true"
        self.client = None
        
        if not self.is_mock:
            # 本番用初期化処理
            try:
                from google.oauth2 import service_account
                creds_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
                if creds_json:
                    import json
                    info = json.loads(creds_json)
                    creds = service_account.Credentials.from_service_account_info(info)
                    self.client = BetaAnalyticsDataClient(credentials=creds)
                elif os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
                    self.client = BetaAnalyticsDataClient()
                else:
                    logger.warning("No GA4 credentials provided. Falling back to mock.")
                    self.is_mock = True
            except Exception as e:
                logger.error(f"GA4 Client initialization failed: {str(e)}")
                self.is_mock = True

    async def get_performance_metrics(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """
        指定期間のパフォーマンスデータをGA4から取得する。
        """
        if self.is_mock or not self.client:
            return self._get_mock_data(start_date, end_date)
            
        try:
            # 本番用APIリクエストの骨組み
            request = RunReportRequest(
                property=f"properties/{self.property_id}",
                dimensions=[Dimension(name="sessionSourceMedium")],
                metrics=[
                    Metric(name="sessions"),
                    Metric(name="conversions")
                ],
                date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
            )
            response = self.client.run_report(request)
            
            # 結果の集計
            total_sessions = 0
            total_conversions = 0
            for row in response.rows:
                total_sessions += int(row.metric_values[0].value)
                total_conversions += int(row.metric_values[1].value)
                
            return {
                "sessions": total_sessions,
                "conversions": total_conversions,
                "bounce_rate": 0.0, # Dummy for now, can query it if needed
                "mock": False
            }
            
        except Exception as e:
            logger.error(f"GA4 report fetch failed: {str(e)}")
            return {"error": str(e)}

    def _get_mock_data(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """モックデータを返す"""
        return {
            "sessions": 1250,
            "conversions": 35,
            "bounce_rate": 45.2,
            "mock": True
        }
