# デプロイ手順書 - AdMu 広告自動運用システム

## 🚀 Renderデプロイ手順

---

## Step 1: GitHubリポジトリの作成

```bash
cd /Users/ishikawagai/Desktop/整体院導/AdMu
git init
git add .
git commit -m "AdMu v1.0 - SaaS対応（PostgreSQL/SQLite自動切替）"
# GitHubで新しいリポジトリ「admu-saas」を作成し、プッシュ
git remote add origin https://github.com/YOUR_USERNAME/admu-saas.git
git branch -M main
git push -u origin main
```

> **注意**: `.env` や `*.db` は `.gitignore` に含まれているため自動的に除外されます。

---

## Step 2: Renderでデプロイ

### 方法A: render.yaml でのBlueprint自動セットアップ（推奨）

1. [render.com](https://render.com) にログイン
2. **New → Blueprint** をクリック
3. GitHubリポジトリ `admu-saas` を選択
4. `render.yaml` が自動検出され、以下が同時作成される：
   - **admu-backend** (Web Service)
   - **admu-db** (PostgreSQL)
5. `DATABASE_URL` は自動で注入される

### 方法B: 手動セットアップ

1. **New → PostgreSQL** で DB作成（名前: `admu-db`）
2. **New → Web Service** でバックエンド作成:
   - **Root Directory**: `backend`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
3. Web Serviceの環境変数に `DATABASE_URL` を手動設定

---

## Step 3: 環境変数の設定

Renderダッシュボード → 対象サービス → **Environment** で以下を設定:

| 変数名 | 値 | 備考 |
|---|---|---|
| `DATABASE_URL` | (自動注入) | Blueprint使用時は不要 |
| `JWT_SECRET` | (自動生成) | Blueprint使用時は不要 |
| `ADMIN_EMAIL` | `admin@admu.jp` | 初回admin作成用 |
| `ADMIN_PASSWORD` | (強力なパスワード) | 初回admin作成用 |
| `ADMIN_INIT_SECRET` | (任意文字列) | 初回セットアップキー |
| `GEMINI_API_KEY` | (APIキー) | AI機能に必要 |
| `MOCK_ADS_API` | `true` | **重要**: Google Ads API審査通過まで必ずtrue |
| `RESEND_API_KEY` | (APIキー) | メール通知に必要 |

---

## Step 4: 初期セットアップ

デプロイ完了後、以下を実行:

```bash
# 1. ヘルスチェック
curl https://admu-xxxx.onrender.com/health

# 2. 管理者アカウント作成
curl -X POST https://admu-xxxx.onrender.com/api/admin/init \
  -H "X-Init-Secret: <ADMIN_INIT_SECRET>"

# 3. ログインテスト
curl -X POST https://admu-xxxx.onrender.com/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@admu.jp","password":"<ADMIN_PASSWORD>"}'
```

---

## Step 5: 動作確認チェックリスト

- [ ] `/health` が `{"status":"ok"}` を返す
- [ ] ログインしてダッシュボードが表示される
- [ ] admin画面でクリニック追加できる
- [ ] AI広告文生成が動作する（GEMINI_API_KEY必要）
- [ ] パスワードリセットメールが届く（RESEND_API_KEY必要）

---

## ⚠️ 注意事項

- **`MOCK_ADS_API=true`** を維持してください。Google Ads APIの実接続は開発者トークン審査後です。
- DB無料プランは **90日で自動削除** されます。本番運用前にStarterプラン($7/月)に変更してください。
- Render Starterプランは常時稼働です（Freeプランは15分スリープ）。

---

## 📊 アーキテクチャ

```
[Render Web Service]          [Render PostgreSQL]
   admu-backend        ←→       admu-db
   FastAPI + Uvicorn           DATABASE_URL自動注入
   
   /               → フロントエンド(index.html)配信
   /admin.html      → 管理画面配信
   /api/*           → REST API
   /health          → ヘルスチェック
```
