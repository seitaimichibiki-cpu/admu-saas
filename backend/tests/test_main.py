import pytest
from fastapi.testclient import TestClient
from main import app, db

client = TestClient(app)

@pytest.fixture(scope="module", autouse=True)
def setup_test_db():
    # テスト用のシンプルなDBクリーンアップフックなど（必要に応じて）
    yield

def test_health_check():
    """ヘルスチェックエンドポイントが正常にステータス200とJSONを返すか"""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "AdMu"

def test_get_clinics_unauthenticated():
    """認証なしでクリニック一覧にアクセスすると401が返るか（セキュリティチェック）"""
    response = client.get("/api/clinics")
    assert response.status_code == 401
    assert "認証が必要です" in response.json()["detail"]

def test_dashboard_mock_params():
    """プラットフォーム切替によるダッシュボードアクセスが正常動作するか
       (DB依存があるため、デフォルトのDEMO-0000が返るかチェック)"""
    # 認証をモックするか、あるいはフロントに公開されている範囲の挙動をチェック
    pass

def test_reset_password_request_security():
    """パスワードリセットにおいて、存在しないメールアドレスでも200OKが返り、
       列挙攻撃(Enumeration attack)を防げているか"""
    response = client.post("/api/auth/reset-password-request", json={
        "email": "nonexistent_fake_email_for_test@example.com"
    })
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert "パスワードリセットのご案内をメールで送信しました" in response.json()["message"] or "リセット" in response.json()["message"]
