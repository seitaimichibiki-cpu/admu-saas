# AdMu フロントエンドURLと本番DBパスワード変更について

## 1. フロントエンド（画面）のアクセス方法について
現在、AdMuのフロントエンド画面（`frontend/index.html` など）は **Render上に完全にデプロイされており、インターネット上からアクセス可能です**。

RenderのURL **`https://admu-backend-jxi0.onrender.com`** にブラウザでアクセスすることで、PCだけでなく、スマートフォンやタブレットからでも直接システム（管理画面・ログイン画面）を利用することができます。
（※FastAPIのバックエンドからフロントエンドの静的ファイルも同時に配信する構成が有効になっています。）

**▶︎ アクセス方法**
- 本番環境（外部デバイス含む）：上記のRender URLにアクセスしてください。
- ローカル開発時：今まで通り、ご自身のPCのVSCodeから `AdMu/frontend/index.html` を開き、右下の **「Go Live (Live Server)」** を押して動作確認を行うことも可能です。

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
