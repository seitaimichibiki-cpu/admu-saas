import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db
import auth
from fastapi.testclient import TestClient
from main import app

def run_idor_test():
    client = TestClient(app)
    db.init_db()
    
    print("--- [1] テスト用ユーザー作成 ---")
    # Aクリニック(本来なら既存の1などを避けて新規作成)
    cid_a = db.upsert_clinic({"name": "Clinic A", "license_key": f"A-{os.urandom(4).hex()}"})
    cid_b = db.upsert_clinic({"name": "Clinic B", "license_key": f"B-{os.urandom(4).hex()}"})
    
    import random
    email_a = f"user_a_{random.randint(1000, 9999)}@test.com"
    email_b = f"user_b_{random.randint(1000, 9999)}@test.com"
    uid_a = db.create_user(cid_a, email_a, auth.hash_password("pass"))
    uid_b = db.create_user(cid_b, email_b, auth.hash_password("pass"))
    
    # JWTトークン取得
    token_a = auth.create_access_token(uid_a, email_a, cid_a, "user")
    headers_a = {"Authorization": f"Bearer {token_a}"}

    print("\n--- [2] 明示的な clinic_id 指定による IDOR テスト ---")
    # AのトークンでBのデータを要求する
    res = client.get(f"/api/dashboard?clinic_id={cid_b}", headers=headers_a)
    print(f"GET /api/dashboard?clinic_id={cid_b} -> Status: {res.status_code}")
    assert res.status_code == 403, "IDOR: 他院のデータをクエリ指定で取得できてしまいました"
    
    res_post = client.post("/api/settings/ads-account", headers=headers_a, json={
        "clinic_id": cid_b, "customer_id": "HACKED"
    })
    print(f"POST /api/settings/ads-account json={{clinic_id:{cid_b}}} -> Status: {res_post.status_code}")
    assert res_post.status_code == 403, "IDOR: 他院のデータをBODY指定で更新できてしまいました"

    print("\n--- [3] clinic_id 省略のデフォルト値 (1) テスト ---")
    # clinic_idを指定せずに送信した場合、エンドポイントのデフォルト引数 (clinic_id=1) が使用され、
    # ミドルウェアは「クエリパラメータが無い」ためスキップする。
    # すると、ユーザーA(cid_a)が、デモクリニック(cid 1)のデータを操作できる脆弱性が存在するか？
    res_default = client.get("/api/dashboard", headers=headers_a)
    print(f"GET /api/dashboard (省略) -> Status: {res_default.status_code}")
    
    # 仮にこのステータスが200であれば、Aが1のデータを閲覧できているか確認する
    if res_default.status_code == 200:
        data = res_default.json()
        print(f"Response dashboard data: {data}")
        # clinic_idを含まないエンドポイントもあるため、テストでわかりやすい campaigns を見ます
        res_campaigns = client.get("/api/campaigns", headers=headers_a)
        comp_data = res_campaigns.json()
        print(f"GET /api/campaigns (省略) -> Data: {comp_data}")
        # ここにデフォルト値(1)が含まれていないなら、注入成功。
    else:
        print("✅ デフォルト値の影響は防がれています（またはエラー等で閲覧不可）")

    print("\n🎉 テスト完了")

if __name__ == "__main__":
    run_idor_test()
