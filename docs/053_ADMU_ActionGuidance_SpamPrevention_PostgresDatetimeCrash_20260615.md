# ナレッジ: AI運用アクションガイダンス機能の追加、予算自動ブレーキ通知のスパム防止、およびPostgreSQLにおけるdatetime型パース起因の500エラー解消

## 作成日
2026年6月15日

---

## 1. AI運用アクションガイダンス機能の追加

### 概要
広告運用の初期段階や成果の状況に応じて、ユーザーに対して「次に何をすべきか」をダッシュボード上で具体的に指示するアクションガイダンス（アドバイスパネル）を追加しました。

### 技術的実装
* **バックエンド (`backend/main.py`)**:
  * 判定ロジック `_generate_action_guidance` を追加。
  * キャンペーンの経過日数（`created_at` からの経過）、総クリック数、総インプレッション数、総コンバージョン数をもとに、以下の5つのステータスを判定。
    1. **開始初期 (配信7日未満)**: 「様子見推奨」
    2. **LP改善推奨 (クリック30以上でCV 0)**: 「ホームページ（LP）の改善やAIチャットへのLP診断依頼を推奨」
    3. **露出不足 (配信7日以上で表示回数200未満)**: 「キーワード範囲拡大や予算増額を推奨」
    4. **順調 (CV 1以上)**: 「自動運用におまかせ」
    5. **準備中 (キャンペーン未作成など)**: 「新規作成を推奨」
  * `/api/dashboard` のレスポンスに `action_guidance` オブジェクトを統合。
  * フロントエンドで最新のJSを強制ロードさせるため、キャッシュバスターを `app.js?v=20260615-ai-guidance-v1` に更新。
* **フロントエンド (`frontend/index.html`, `frontend/js/app.js`)**:
  * KPIカードの上部に `#actionGuidanceContainer` を設置。
  * `app.js` で `renderActionGuidance` を実装し、返却されたガイダンス情報（ステータスカラー、タイトル、説明文、ToDoアクションリスト）をダッシュボードに動的描画。
  * アクション内の「AIチャットでLP診断を依頼する」といったボタンがクリックされた際、自動でAIチャットタブに遷移し、入力欄にテンプレートテキスト（「ホームページのURL診断をお願いします...」）を挿入・スクロールする `goToLpChatDiagnose` をバインド。

---

## 2. 予算自動ブレーキ通知のスパム防止（24時間制限）

### 概要
「予算自動ブレーキ作動による配信停止」のメールやLINE通知が、バックグラウンド監視処理のループ（毎分）ごとに繰り返し送信されてしまう問題を解消しました。

### 技術的実装
* **モックステータスの同期維持 (`backend/ads_client.py`)**:
  * `update_campaign_status` 呼び出し時、モック環境ではメモリ上の辞書 `_mock_campaign_statuses` にステータス（`PAUSED` など）を保存するように修正。
  * これにより、次の監視ループで再び `ENABLED` として読み込まれ、「毎回新しくブレーキが作動した」と誤判定されるのを防止。
* **24時間以内の同一アラート通知ガード (`backend/monitor.py`)**:
  * `_check_campaigns` の中でアラート判定を行った際、同一の「予算自動ブレーキ作動による配信停止」アラートが過去24時間以内に既に登録されているかをチェック。
  * 既に送信済みの場合は、メール送信、LINE送信、および新規のデータベースへのアラート登録処理をスキップするようガードを実装。

---

## 3. 本番PostgreSQL環境におけるdatetimeパースの500エラー解消

### 概要
上記のデプロイ直後、本番環境のダッシュボードを開くと「ダッシュボードの読み込みに失敗しました」と赤文字でエラーが出る現象が発生。

### 原因
* ローカル環境（SQLite）では、キャンペーンの `created_at` カラムはテキスト型（`str`）として返されるため、`datetime.strptime(c_at, "%Y-%m-%d %H:%M:%S")` によるパースが正常に動作していた。
* しかし、本番環境（PostgreSQL / Render）では、`psycopg2` の `RealDictCursor` が `timestamp` カラムのデータを自動的に `datetime.datetime` オブジェクトにキャストして返却する。
* これにより `strptime` に `datetime` 型が渡され、`TypeError` が発生。この例外がキャッチされずにダッシュボードAPI（`/api/dashboard`）全体が HTTP 500 エラーでクラッシュしていた。

### 対策 (`backend/main.py` の `_generate_action_guidance` 修正)
1. **データ型判定の導入**:
   * `created_at` から得たオブジェクトがすでに `datetime` 型である場合は、タイムゾーン（`tzinfo`）をクリア（naive化）した上でそのままリストに追加する。
   * 文字列である場合は、ミリ秒やタイムゾーン部分（`+00:00` など）を切除（`.split('.')[0].split('+')[0]`）し、堅牢に `strptime` でパースする。
2. **全体的な例外保護**:
   * 万が一DBアクセスや日付の処理で予期せぬ例外が発生した場合でも、ダッシュボードAPI全体を巻き込んで500エラーにならないよう、処理全体を `try-except` で囲み、エラーログを出力して安全にフォールバックするようにした。

---

## 4. 本番動作の確認手順 (デベロッパー向け)

デプロイ後に動作を確認する際は、CSRFトークンの検証が必要となるため、以下の手順で curl コマンドなどから確認できます。

1. **CSRFトークンの取得**:
   ```bash
   curl -i -c cookies.txt https://admu-backend-jxi0.onrender.com/api/csrf-token
   ```
   * レスポンスヘッダーの `set-cookie: csrf_token=...` とレスポンスボディの `{"csrf_token":"..."}` を確認。

2. **ログイン**:
   ```bash
   curl -i -b cookies.txt -c cookies.txt \
     -X POST \
     -H "Content-Type: application/json" \
     -H "X-CSRF-Token: <取得したCSRFトークン>" \
     -d '{"email":"seitaimichibiki@gmail.com","password":"gai1124714"}' \
     https://admu-backend-jxi0.onrender.com/api/auth/login
   ```
   * クッキー `access_token` が正常に書き込まれることを確認。

3. **ダッシュボードAPIの疎通確認**:
   ```bash
   curl -i -b cookies.txt -c cookies.txt \
     -H "X-CSRF-Token: <取得したCSRFトークン>" \
     "https://admu-backend-jxi0.onrender.com/api/dashboard?clinic_id=1"
   ```
   * HTTP 200 が返却され、`action_guidance` キー配下に判定結果のアクション情報が含まれていることを確認する。
