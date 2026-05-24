#!/usr/bin/env python3
"""
production_readiness_check.py
=============================
Google Ads API Basic Access 承認後に本番切替を行う前のチェックリストスクリプト。

使い方:
    python3 production_readiness_check.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

# .envファイルの読み込み
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
except ImportError:
    pass

def check(name: str, condition: bool, detail: str = ""):
    status = "✅" if condition else "❌"
    msg = f"  {status} {name}"
    if detail:
        msg += f"  ({detail})"
    print(msg)
    return condition

def main():
    print("=" * 60)
    print("AdMu 本番切替前チェックリスト")
    print("=" * 60)
    
    all_ok = True
    
    # ---- セキュリティ ----
    print("\n📋 セキュリティ設定:")
    jwt = os.environ.get("JWT_SECRET", "")
    all_ok &= check("JWT_SECRET", len(jwt) >= 32 and "CHANGE" not in jwt, f"{len(jwt)}文字")
    
    admin_pw = os.environ.get("ADMIN_PASSWORD", "")
    all_ok &= check("ADMIN_PASSWORD", bool(admin_pw) and admin_pw != "change-me-in-production", "設定済み" if admin_pw else "未設定")
    
    enc_key = os.environ.get("ENCRYPTION_KEY", "")
    all_ok &= check("ENCRYPTION_KEY", bool(enc_key), "設定済み" if enc_key else "未設定")
    
    env = os.environ.get("ENVIRONMENT", "development")
    all_ok &= check("ENVIRONMENT", env == "production", env)
    
    origins = os.environ.get("ALLOWED_ORIGINS", "*")
    all_ok &= check("ALLOWED_ORIGINS", origins != "*", origins[:60])
    
    # ---- Google Ads API ----
    print("\n📋 Google Ads API:")
    dev_token = os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", "") or os.environ.get("MASTER_ADS_DEVELOPER_TOKEN", "")
    all_ok &= check("Developer Token", bool(dev_token), f"{dev_token[:8]}..." if dev_token else "未設定")
    
    client_id = os.environ.get("GOOGLE_ADS_CLIENT_ID", "") or os.environ.get("MASTER_ADS_CLIENT_ID", "")
    all_ok &= check("OAuth Client ID", bool(client_id), f"{client_id[:20]}..." if client_id else "未設定")
    
    client_secret = os.environ.get("GOOGLE_ADS_CLIENT_SECRET", "") or os.environ.get("MASTER_ADS_CLIENT_SECRET", "")
    all_ok &= check("OAuth Client Secret", bool(client_secret), "設定済み" if client_secret else "未設定")
    
    refresh_token = os.environ.get("GOOGLE_ADS_REFRESH_TOKEN", "") or os.environ.get("MASTER_ADS_REFRESH_TOKEN", "")
    all_ok &= check("Refresh Token", bool(refresh_token), "設定済み" if refresh_token else "未設定")
    
    default_cid = os.environ.get("GOOGLE_ADS_DEFAULT_CUSTOMER_ID", "")
    all_ok &= check("Default Customer ID", bool(default_cid), default_cid if default_cid else "未設定")
    
    login_cid = os.environ.get("MASTER_ADS_LOGIN_CUSTOMER_ID", "")
    all_ok &= check("MCC Login Customer ID", bool(login_cid), login_cid if login_cid else "未設定")
    
    mock = os.environ.get("MOCK_ADS_API", "true")
    check("MOCK_ADS_API", mock == "false", f"現在: {mock}（承認後に false に変更）")
    
    # ---- メール・通知 ----
    print("\n📋 メール・通知:")
    smtp_user = os.environ.get("SMTP_USER", "")
    all_ok &= check("SMTP_USER", bool(smtp_user), smtp_user if smtp_user else "未設定")
    
    smtp_pass = os.environ.get("SMTP_PASS", "")
    all_ok &= check("SMTP_PASS", bool(smtp_pass), "設定済み" if smtp_pass else "未設定")
    
    app_url = os.environ.get("APP_BASE_URL", "")
    all_ok &= check("APP_BASE_URL", "localhost" not in app_url, app_url)
    
    # ---- Stripe ----
    print("\n📋 Stripe決済:")
    stripe_key = os.environ.get("STRIPE_API_KEY", "")
    is_live = stripe_key.startswith("sk_live_")
    all_ok &= check("STRIPE_API_KEY", bool(stripe_key), "本番キー" if is_live else "テストキー" if stripe_key else "未設定")
    
    # ---- Sentry ----
    print("\n📋 エラー監視:")
    sentry = os.environ.get("SENTRY_DSN", "")
    check("SENTRY_DSN", bool(sentry), "設定済み" if sentry else "⚠️ 未設定（推奨）")
    
    # ---- google-ads SDK ----
    print("\n📋 依存パッケージ:")
    try:
        from google.ads.googleads.client import GoogleAdsClient
        check("google-ads SDK", True, "インストール済み")
    except ImportError:
        check("google-ads SDK", False, "pip install google-ads が必要")
    
    # ---- サマリー ----
    print("\n" + "=" * 60)
    if all_ok:
        print("🎉 全チェック通過！承認後に MOCK_ADS_API=false に変更するだけで本番稼働できます。")
    else:
        print("⚠️  一部項目が未設定です。上記の ❌ 項目を確認してください。")
    print("=" * 60)
    
    # ---- 本番切替手順 ----
    print("""
📝 Google Ads API 承認後の切替手順:
  1. .env の MOCK_ADS_API=false に変更
  2. Render の環境変数でも MOCK_ADS_API=false に更新
  3. Render の MASTER_ADS_* 環境変数が設定されているか確認
  4. Render をリデプロイ
  5. /admin ダッシュボードでモック表示が消えたことを確認
  6. 実際のキャンペーンデータが表示されれば成功！
""")

if __name__ == "__main__":
    main()
