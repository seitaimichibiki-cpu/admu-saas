import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db
import monitor

def run_test():
    # テーブル作成
    db.init_db()
    
    # 1. 監査ログとクリーンアップのDBテスト
    cid = db.upsert_clinic({"name": "Audit Test Clinic", "license_key": f"AUDIT-{os.urandom(4).hex()}"})
    
    print("--- [1] 監査ログ追加テスト ---")
    db.add_audit_log(cid, "tester@example.com", "TEST_ACTION", "system", "First test log")
    logs = db.list_audit_logs(cid)
    
    assert len(logs) > 0, "ログが取得できませんでした"
    assert logs[0]["action"] == "TEST_ACTION", "ログの内容が違います"
    print(f"✅ Audit Log 追加・取得 成功 (Count: {len(logs)})")

    print("\n--- [2] クリーンアップのSQL文バリデーション ---")
    res = db.cleanup_old_logs(365)
    print(f"✅ クリーンアップSQL正常実行: {res}")
    
    print("\n--- [3] Monitor の Suspended状態検知テスト ---")
    # アクティブ時
    db.update_clinic_plan_status(cid, "active")
    acc = monitor._get_account_and_notify_config(cid)
    print(f"✅ Active: _get_account_and_notify_config() は {type(acc)} を返しました")
    
    # サスペンド時
    db.update_clinic_plan_status(cid, "suspended")
    acc = monitor._get_account_and_notify_config(cid)
    assert acc == {}, "Suspended 時は空の辞書であるべきです"
    print(f"✅ Suspended: 期待通りスキップ判定(空辞書)を返しました")

    print("\n🎉 すべてのテストに合格しました！")

if __name__ == "__main__":
    run_test()
