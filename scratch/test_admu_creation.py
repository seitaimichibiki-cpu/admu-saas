import unittest
import os
import sys
import json

# FastAPIテストクライアントの初期化用
sys.path.append(os.path.join(os.path.dirname(__file__), '../backend'))

class TestAdMuConversionCreation(unittest.TestCase):
    def setUp(self):
        # テスト時はモックモードを強制
        os.environ["MOCK_ADS_API"] = "true"
        os.environ["LINE_HARNESS_URL"] = "" # テスト時は同期 fetch をスキップさせるため空文字に設定
        os.environ["LINE_HARNESS_API_KEY"] = ""

        # mainモジュールのインポート
        from main import app
        import db
        db.init_db() # マイグレーションを確実に実行
        
        from fastapi.testclient import TestClient
        self.client = TestClient(app)

    def test_create_conversion_action_api(self):
        print("\n--- Testing POST /api/integration/create-conversion-action ---")
        payload = {
            "conversion_name": "LINE_Harness_来院テスト",
            "conversion_value": 12000.0,
            "clinic_id": 1
        }
        resp = self.client.post("/api/integration/create-conversion-action", json=payload)
        
        print("Status Code:", resp.status_code)
        print("Body:", resp.text)
        
        data = resp.json()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data["success"])
        self.assertIn("Google広告側にのみコンバージョンを作成しました", data["message"])
        self.assertTrue(data["ads_data"]["resource_name"].endswith("conversionActions/mock_action_id"))

    def test_create_conversion_action_with_lh_settings(self):
        print("\n--- Testing POST /api/integration/create-conversion-action with DB Settings ---")
        import db
        from unittest.mock import patch, MagicMock

        # 現在の設定を退避
        original_settings = db.get_ads_account(1) or {}

        try:
            # テスト用のLINE Harness設定をDBに保存
            test_lh_url = "https://lh-test.workers.dev"
            db.save_ads_account(1, {
                "line_harness_url": test_lh_url,
                "line_harness_api_key": "lh-api-key-test",
                "line_harness_account_id": "lh-acc-test"
            })

            # requests.post をモック
            with patch("requests.post") as mock_post:
                # LINE Harnessのレスポンスオブジェクトをモック
                mock_resp = MagicMock()
                mock_resp.status_code = 201
                mock_resp.json.return_value = {"success": True, "id": "cv-point-123"}
                mock_post.return_value = mock_resp

                payload = {
                    "conversion_name": "LINE_Harness_同期テスト",
                    "conversion_value": 15000.0,
                    "clinic_id": 1
                }
                resp = self.client.post("/api/integration/create-conversion-action", json=payload)

                print("Status Code:", resp.status_code)
                print("Body:", resp.text)

                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertTrue(data["success"])
                self.assertIn("Google広告とLINE Harnessの両方にコンバージョンアクションを自動作成・同期しました", data["message"])
                self.assertEqual(data["lh_data"]["id"], "cv-point-123")

                # 正しいヘッダーとペイロードでLINE Harness APIが叩かれたか検証
                mock_post.assert_called_once()
                args, kwargs = mock_post.call_args
                self.assertEqual(args[0], f"{test_lh_url}/api/conversions/points")
                self.assertEqual(kwargs["headers"]["Authorization"], "Bearer lh-api-key-test")
                self.assertEqual(kwargs["headers"]["X-Line-Account-Id"], "lh-acc-test")
                self.assertEqual(kwargs["json"]["name"], "LINE_Harness_同期テスト")
                self.assertEqual(kwargs["json"]["value"], 15000.0)

        finally:
            # 設定を元に戻す
            db.save_ads_account(1, original_settings)

if __name__ == '__main__':
    unittest.main()
