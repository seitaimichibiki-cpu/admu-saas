# AdMu フロントエンドURLと本番DBパスワード変更について

## 1. フロントエンド（画面）のアクセス方法について
現在、AdMuのフロントエンド画面（`frontend/index.html` など）は **Renderなどのインターネット上にはデプロイされていません**。

Renderの `admu-backend-~.onrender.com` は**「裏側のAPI処理専用のURL」**であるため、ブラウザで開いても画面は真っ白（JSONのテキストのみ）になります。
（※ `render.yaml` の設定により、Renderには `backend/` フォルダの中身のみがアップロードされているため、フロントエンドのファイルはネット上に存在していません。）

**▶︎ 現状の解決策**
今まで通り、ご自身のPCのVSCodeから `AdMu/frontend/index.html` を開き、右下の **「Go Live (Live Server)」** を押して開いてください。
（もしネット上からスマホなどでアクセスしたい場合は、後日 Vercel や Cloudflare Pages などへフロントエンド側をデプロイする必要があります）

## 2. 本番環境（Render PostgreSQL）のパスワード手動変更
ローカルの環境変数やSQLiteではなく、本番のRender上のデータベース（PostgreSQL）の情報を強制的に上書きしてログインできるようにするには、**Render Web Shell** を用います。

### 作業手順
1. Renderダッシュボードの `admu-backend` を開く
2. 左メニューの **「Shell」** をクリック
3. 以下のコードをコピーして貼り付け、Enterを実行する

```python
python -c '
import sys
sys.path.append(".")
import db
from auth import hash_password

email = "seitaimichibiki@gmail.com"
pw_hash = hash_password("gai1124714")

conn = db.get_conn()
cur = conn.cursor()
# !!重要!! PostgreSQLの場合は ? ではなく %s を使用する
cur.execute("SELECT id FROM users WHERE email = %s", (email,))
row = cur.fetchone()

if row:
    cur.execute("UPDATE users SET password_hash = %s WHERE email = %s", (pw_hash, email))
else:
    cur.execute("INSERT INTO users (clinic_id, email, password_hash, role) VALUES (1, %s, %s, %s)", (email, pw_hash, "admin"))

conn.commit()
print("🎉 SUCCESS: 本番環境のユーザー情報を更新しました！")
'
```
この手順により、管理者パスワードが即座に変更され、ローカルのフロントエンド画面からRenderのAPIへログインが可能になります。
