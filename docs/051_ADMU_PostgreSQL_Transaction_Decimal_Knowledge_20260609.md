# PostgreSQL環境における型変換とトランザクション管理に関する開発ナレッジ

本ドキュメントは、AdMuのSaaS化（SQLiteからPostgreSQLへの移行）に伴って発生したバグとその解決策をナレッジとして記録するものです。

---

## 1. `decimal.Decimal` と `float` の除算エラー

### 現象
管理者画面の概要（Overview）や実績分析の集計データを取得する際、以下のエラーが発生して画面取得に失敗する。
```json
{
    "detail": "Overview error: unsupported operand type(s) for /: 'decimal.Decimal' and 'float'"
}
```

### 原因
SQLiteでは `SUM()` などの集計結果が数値型（`int` や `float`）として返されますが、**PostgreSQL（psycopg2など）では `numeric` 型の集計結果がPythonの `decimal.Decimal` 型として返されます。**
Pythonでは `Decimal` 型と `float` 型の間で直接除算（`/`）を行おうとすると `TypeError` になります。

### 対策
データベースから集計した数値データを割り算に使用する前に、明示的にキャスト処理を噛ませます。
```python
# 悪い例
cost_yen = (r["cost_micros"] or 0) / 1_000_000

# 良い例（キャストを適用）
cost_yen = float(r["cost_micros"] or 0) / 1_000_000
```

---

## 2. トランザクション・アボートエラー (`current transaction is aborted`)

### 現象
クリニックの削除時、関連する一部のテーブル（例：`invitations` など）が存在しないか削除エラーが起きた際、以下のエラーが発生して削除が失敗する。
```
削除エラー: current transaction is aborted, commands ignored until end of transaction block
```

### 原因
SQLiteではクエリの失敗を `try...except` で握りつぶせば次のクエリを実行可能ですが、**PostgreSQLではトランザクション内でクエリが1度でも失敗すると、トランザクション全体が「aborted（中止）」状態になります。**
アボート状態になったトランザクションは、`ROLLBACK` を実行して終了するまで、その後のクエリ（親であるクリニック本体の削除など）をすべて無視します。

### 対策
例外が発生しうる個別のクエリを実行する際、**`SAVEPOINT`（セーブポイント）**で保護し、エラー発生時はそのセーブポイントまでロールバックさせてアボート状態をクリアします。
```python
for t_spec in tables:
    try:
        if USE_PG:
            conn.execute("SAVEPOINT del_sp")
        conn.execute(f"DELETE FROM {t_spec}", (clinic_id,))
        if USE_PG:
            conn.execute("RELEASE SAVEPOINT del_sp")
    except Exception as e:
        if USE_PG:
            conn.execute("ROLLBACK TO SAVEPOINT del_sp")
        print(f"Skip table delete error: {e}")
```

---

## 3. 静的HTMLのブラウザキャッシュ問題

### 現象
フロントエンド（`admin.html` など）のプログラムを本番環境にデプロイし、デプロイが完了したのにもかかわらず、ユーザー画面に古いUIや機能が表示され続ける。

### 原因
FastAPIの `FileResponse` で静的HTMLファイルを配信する際、デフォルトでは `Cache-Control` ヘッダーが指定されないため、ブラウザ側が強力にHTMLファイルをキャッシュしてしまいます。

### 対策
HTMLを配信するAPIエンドポイントにて、レスポンスヘッダーにキャッシュを完全に無効化する `Cache-Control` を明示的に付与します。
```python
@app.get("/admin.html", include_in_schema=False)
def serve_admin():
    admin_path = os.path.join(FRONTEND_DIR, "admin.html")
    if os.path.exists(admin_path):
        from fastapi.responses import FileResponse as FR
        resp = FR(admin_path, media_type="text/html")
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return resp
    raise HTTPException(404, "admin.html not found")
```
