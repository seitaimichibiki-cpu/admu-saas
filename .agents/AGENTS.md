# Project Rules and Knowledge

## 1. PostgreSQL Transaction Safety (Avoid Aborted Transactions)
* **Rule**: Never run DDL statements (like `ALTER TABLE ... ADD COLUMN`) inline inside queries or utility functions where errors are simply suppressed with `try/except`.
* **Reason**: In PostgreSQL, any error (such as a duplicate column error during `ALTER TABLE`) instantly marks the current transaction as aborted (`InFailedSqlTransaction`). Subsequent queries on the same connection will fail with `current transaction is aborted, commands ignored until end of transaction block`.
* **Solution**: Ensure all migrations and table alterations are placed in the application startup initialization code (e.g., `init_db()` in `db.py`) wrapped in PostgreSQL `SAVEPOINT` blocks, allowing errors to roll back safely without bricking the active transaction.

## 2. YouTube Ads (Demand Gen Responsive Ads) Update Requirements
* **Required Fields**: In Google Ads API (v23), `DemandGenVideoResponsiveAd` requires the following fields:
  * `ad.name` (must be unique for the operation, e.g., `DemandGenAd_campaignId_random`).
  * `logo_images` (at least 1 image asset resource name).
  * `business_name` (must be non-empty and limited to 25 chars).
* **Asset Deletion Handling**: If a YouTube video reference is deleted (`YOUTUBE_VIDEO_REMOVED`), the API will fail to update. We must allow the user to provide a new YouTube Video URL and dynamically create a new video asset.
* **State Persistence**: Because GAQL (Google Ads Query Language) queries for Demand Gen ads sometimes return 0 rows or omit details (forcing fallbacks), always persist the successfully updated ad contents locally in the database (`campaigns.ad_content_json`). Restore the form data from this local database backup if the live GAQL fetch fails to return full properties.
* **Character Length Limit for Double-Byte (Japanese) Characters**: In Google Ads API, length limits for headlines, long headlines, and descriptions are counted in half-width characters (single-byte). Double-byte characters (Japanese) count as **2 characters each**.
  * **Headlines**: API limit 40 characters -> **Max 20 Japanese characters**.
  * **Long Headlines**: API limit 90 characters -> **Max 45 Japanese characters**.
  * **Descriptions**: API limit 90 characters -> **Max 45 Japanese characters**.
* **Policy Constraints (SYMBOLS & Punctuation)**: Google Ads has strict guidelines regarding symbols in ads. 
  * Avoid parentheses `()`, brackets `【】`, and slashes `/` in headlines/descriptions. Parentheses are flagged as `SYMBOLS` and cause `POLICY_FINDING` api errors.
  * Avoid exclamation marks `!` or `！` in headlines (they are strictly prohibited). In descriptions, at most one exclamation mark is allowed per ad, but it is safer to avoid them entirely to prevent policy disapproval.

## 3. PostgreSQL vs SQLite Row Unpacking (Avoid Tuples/Unpacking)
* **Rule**: Do not unpack query results (e.g. `for pref, city in rows:`) or use index-based access (e.g. `row[0]`) for queries shared between SQLite (local) and PostgreSQL (production).
* **Reason**: While SQLite returns rows as tuples (allowing unpacking and `row[0]`), PostgreSQL dictionary cursors return records as dictionaries or map objects. Attempting to unpack dict-like rows `for a, b in rows` assigns the **column keys** as strings to variables, causing silent data corruption. Index access like `row[0]` causes a `KeyError: 0`.
* **Solution**: Always use explicit column aliases in SQL (e.g., `SELECT COUNT(*) as cnt`) and access values via string keys (e.g., `row["cnt"]`). When iterating, cast to dict first:
  ```python
  for row in rows:
      d = dict(row)
      pref = d.get("address_pref")
  ```

## 4. External Geocoding API Blocks & Local Fallbacks
* **Rule**: When requesting domestic geocoding APIs (like `msearch.gsi.go.jp`) from cloud servers (Render/AWS), always include a browser-like `User-Agent` header. Additionally, implement a local keyword-to-coordinate mapping dictionary as a fail-safe fallback.
* **Reason**: GSI APIs block requests without a `User-Agent` and often restrict overseas cloud server IPs. A fallback dictionary of major target cities (e.g. Shizuoka local areas: Fujieda, Yaizu, Shimada) ensures 100% availability even if the external API blocks the server.
