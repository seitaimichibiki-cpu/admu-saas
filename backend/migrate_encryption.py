"""
migrate_encryption.py
既存のデータベースに残っている平文のAPIクレデンシャル情報を一括で暗号化するマイグレーションスクリプト。
SaaS稼働前に1度だけ実行します。
"""
import os
import sys

# カレントディレクトリをPATHに追加（backend直下でなくても動くように）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import crypto_utils

def run_migration():
    print("--- クレデンシャル暗号化マイグレーション開始 ---")
    migrated_count = 0
    SECRET_FIELDS = [
        "developer_token", "client_secret", "refresh_token", 
        "line_channel_token", "ga4_api_secret", "smtp_pass", 
        "yahoo_client_secret", "yahoo_refresh_token"
    ]
    
    with db.get_conn() as conn:
        # DBによってRowファクトリの動作が異なるため dict で扱う
        rows = conn.execute("SELECT * FROM ads_accounts").fetchall()
        accounts = [dict(r) for r in rows]
        
        for acc in accounts:
            clinic_id = acc["clinic_id"]
            update_data = {}
            needs_update = False
            
            for field in SECRET_FIELDS:
                val = acc.get(field)
                if val and not val.startswith("enc:"):
                    update_data[field] = crypto_utils.encrypt(val)
                    needs_update = True
                    
            if needs_update:
                sets = ", ".join(f"{f}=?" for f in update_data.keys())
                vals = list(update_data.values()) + [clinic_id]
                conn.execute(f"UPDATE ads_accounts SET {sets} WHERE clinic_id=?", vals)
                migrated_count += 1
                print(f"  [OK] Clinic ID: {clinic_id} のクレデンシャルを暗号化しました")
        
        conn.commit()
    
    print(f"--- マイグレーション完了: 合計 {migrated_count} 件更新されました ---")

if __name__ == "__main__":
    run_migration()
