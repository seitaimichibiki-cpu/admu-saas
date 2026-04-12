import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db

def run_test():
    cid = db.upsert_clinic({"name": "テストクリニック(暗号化)", "license_key": f"TEST-KEY-{os.urandom(4).hex()}"})
    
    # 平文で保存
    db.save_ads_account(cid, {
        "customer_id": "TEST-CUST-1",
        "developer_token": "my_super_secret_token_123",
        "smtp_pass": "smtp_secret_pass"
    })
    
    with db.get_conn() as conn:
        row = conn.execute("SELECT developer_token, smtp_pass FROM ads_accounts WHERE clinic_id=?", (cid,)).fetchone()
        print("--- [1] データベース内の生データ (Raw DB values) ---")
        print(f"developer_token: {row['developer_token']}")
        print(f"smtp_pass: {row['smtp_pass']}")
        assert row["developer_token"].startswith("enc:"), "DB内で暗号化されていません"
        assert row["smtp_pass"].startswith("enc:"), "DB内で暗号化されていません"
        
    data = db.get_ads_account(cid)
    print("\n--- [2] 復号して取得されたデータ (Decrypted values) ---")
    print(f"developer_token: {data['developer_token']}")
    print(f"smtp_pass: {data['smtp_pass']}")
    assert data["developer_token"] == "my_super_secret_token_123", "正しく復号されていません"
    assert data["smtp_pass"] == "smtp_secret_pass", "正しく復号されていません"
    
    print("\n✅ すべての暗号化/復号化テストをパスしました。")

if __name__ == "__main__":
    run_test()
