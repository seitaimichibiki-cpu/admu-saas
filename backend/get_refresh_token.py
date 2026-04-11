import os
import sys
import urllib.parse
import requests
from dotenv import load_dotenv

# .env ファイルをロード
load_dotenv()

CLIENT_ID = os.getenv("GOOGLE_ADS_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_ADS_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    print("🚨 エラー: .env ファイルに CLIENT_ID または CLIENT_SECRET がありません。")
    sys.exit(1)

# OOB（コピーペースト方式）は廃止されたため、ローカルホストを使用
REDIRECT_URI = "http://localhost:8080"

auth_url = (
    f"https://accounts.google.com/o/oauth2/v2/auth?"
    f"client_id={CLIENT_ID}&"
    f"redirect_uri={urllib.parse.quote(REDIRECT_URI)}&"
    f"response_type=code&"
    f"scope=https://www.googleapis.com/auth/adwords&"
    f"access_type=offline&"
    f"prompt=consent"
)

print("\n=============================================")
print("  Google Ads API 【リフレッシュトークン】 発行")
print("=============================================\n")
print("【手順 1】 以下のURLをクリックするか、ブラウザに貼り付けて開いてください:\n")
print(auth_url)
print("\n【手順 2】 ご自身のアカウントを選択し、警告が出ても「続行」を押して許可してください。")
print("【手順 3】 最後にブラウザが「このサイトにアクセスできません」や「接続拒否」といったエラー画面になりますが、それで設定は『正常』です！")
print("【手順 4】 そのエラーになった画面の『一番上のアドレスバーのURL（http://localhost:8080/?code=...）』を余すことなく全てコピーしてください。\n")

full_url = input("コピーしたURLをここに貼り付けてEnterを押す: ").strip()

# URLからcode部分を抽出
if "code=" in full_url:
    code = full_url.split("code=")[1].split("&")[0]
    code = urllib.parse.unquote(code)
else:
    code = full_url

# トークンの引き換え
resp = requests.post("https://oauth2.googleapis.com/token", data={
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "code": code,
    "grant_type": "authorization_code",
    "redirect_uri": REDIRECT_URI
})

data = resp.json()

if "refresh_token" in data:
    print("\n✅ リフレッシュトークンの取得に大成功しました！！\n")
    print(f"GOOGLE_ADS_REFRESH_TOKEN={data['refresh_token']}\n")
    
    # .env ファイルに自動保存
    try:
        with open(".env", "r", encoding="utf-8") as f:
            lines = f.readlines()
        with open(".env", "w", encoding="utf-8") as f:
            for line in lines:
                if line.startswith("GOOGLE_ADS_REFRESH_TOKEN="):
                    f.write(f"GOOGLE_ADS_REFRESH_TOKEN={data['refresh_token']}\n")
                else:
                    f.write(line)
        print("👉 .env ファイルへの自動保存も完了しました！")
    except Exception as e:
        print("注意: .env への自動保存に失敗しました。手動で追記してください。")
else:
    print("\n🚨 エラー発生しました:")
    print(data)
