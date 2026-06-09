import unittest
from unittest.mock import MagicMock, patch
import os
import sys

# プロジェクトのルートディレクトリをパスに追加
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.ads_client import AdsClient
from backend.ad_copy_generator import AdCopyGenerator

class TestAdStrengthOptimization(unittest.TestCase):

    def setUp(self):
        cfg = {
            "developer_token": "test_dev_token",
            "client_id": "test_client_id",
            "client_secret": "test_secret",
            "refresh_token": "test_refresh_token",
            "customer_id": "1234567890",
            "mock_mode": True
        }
        self.client = AdsClient(cfg)

    def test_add_url_parameter(self):
        # 1. パラメータなしURL
        url = "https://michibiki-seitai.com"
        res = self.client._add_url_parameter(url, "sl=reserve")
        self.assertEqual(res, "https://michibiki-seitai.com?sl=reserve")

        # 2. すでにクエリパラメータがあるURL
        url_with_query = "https://michibiki-seitai.com?param=value"
        res = self.client._add_url_parameter(url_with_query, "sl=menu")
        self.assertEqual(res, "https://michibiki-seitai.com?param=value&sl=menu")

        # 3. アンカー（#）付きURL
        url_with_anchor = "https://michibiki-seitai.com#reserve"
        res = self.client._add_url_parameter(url_with_anchor, "sl=reserve")
        self.assertEqual(res, "https://michibiki-seitai.com?sl=reserve#reserve")

        # 4. クエリパラメータとアンカー両方付きURL
        url_both = "https://michibiki-seitai.com?p=1#reserve"
        res = self.client._add_url_parameter(url_both, "sl=reserve")
        self.assertEqual(res, "https://michibiki-seitai.com?p=1&sl=reserve#reserve")

    def test_mock_get_campaign_keywords(self):
        # モックモードでのキーワード取得
        kws = self.client.get_campaign_keywords("12345")
        self.assertIn("腰痛 整体", kws)
        self.assertIn("藤枝 腰痛", kws)

    def test_ad_copy_generator_with_keywords(self):
        # Geminiを使わずフォールバック挙動を確認
        generator = AdCopyGenerator(api_key="")
        context = {
            "clinic_name": "藤枝腰痛整体院",
            "region": "藤枝市",
            "appeal_points": "国家資格保持、駐車場あり",
            "target_issues": "慢性的な腰痛",
            "keywords": ["藤枝 腰痛 整体", "腰痛 治療 近く"]
        }
        res = generator.generate(context)
        self.assertIn("headlines", res)
        self.assertIn("descriptions", res)
        # フォールバックでは例外なく生成されることを確認
        self.assertEqual(len(res["headlines"]), 15)
        self.assertEqual(len(res["descriptions"]), 4)

    @patch("requests.post")
    def test_get_campaign_keywords_rest_api(self, mock_post):
        # REST APIの挙動をモック
        self.client.mock_mode = False
        self.client._get_rest_access_token = MagicMock(return_value="mock_token")

        # 正常レスポンスをモック
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "results": [
                    {
                        "adGroupCriterion": {
                            "keyword": {
                                "text": "腰痛 整体 藤枝"
                            }
                        }
                    },
                    {
                        "adGroupCriterion": {
                            "keyword": {
                                "text": "焼津 腰痛 治療"
                            }
                        }
                    }
                ]
            }
        ]
        mock_post.return_value = mock_response

        kws = self.client.get_campaign_keywords("12345")
        self.assertEqual(len(kws), 2)
        self.assertIn("腰痛 整体 藤枝", kws)
        self.assertIn("焼津 腰痛 治療", kws)

    @patch("requests.post")
    def test_link_business_name_and_sitelinks_with_custom_urls(self, mock_post):
        self.client.mock_mode = False
        self.client._get_rest_access_token = MagicMock(return_value="mock_token")

        sitelink_urls = {
            "price_url": "https://michibiki-seitai.com/menu-detail",
            "reviews_url": "https://michibiki-seitai.com/voice",
            "reserve_url": "https://michibiki-seitai.com/reserve-online"
        }

        post_calls = []
        def side_effect(url, headers, json):
            post_calls.append((url, json))
            r = MagicMock()
            r.status_code = 200
            if "searchStream" in url:
                r.json.return_value = [{"results": []}]
            else:
                r.json.return_value = {"results": [{"resourceName": "customers/123/assets/456"}]}
            return r
        mock_post.side_effect = side_effect

        self.client.link_business_name_and_sitelinks(
            google_campaign_id="12345678",
            clinic_name="整体院導",
            final_url="https://michibiki-seitai.com",
            sitelink_urls=sitelink_urls
        )

        price_url_checked = False
        reviews_url_checked = False
        reserve_url_checked = False

        for url, payload in post_calls:
            if "assets:mutate" in url:
                operations = payload.get("operations", [])
                for op in operations:
                    create = op.get("create", {})
                    if create.get("type") == "SITELINK":
                        sl_asset = create.get("sitelinkAsset", {})
                        urls = sl_asset.get("finalUrls", [])
                        if urls:
                            if urls[0] == "https://michibiki-seitai.com/menu-detail":
                                price_url_checked = True
                            elif urls[0] == "https://michibiki-seitai.com/voice":
                                reviews_url_checked = True
                            elif urls[0] == "https://michibiki-seitai.com/reserve-online":
                                reserve_url_checked = True

        self.assertTrue(price_url_checked)
        self.assertTrue(reviews_url_checked)
        self.assertTrue(reserve_url_checked)

if __name__ == "__main__":
    unittest.main()
