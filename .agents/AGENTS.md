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
## 5. Render Server Plan Status (No Cold Starts)
* **Knowledge**: The Render server backend and database are running on a **paid plan** (active, non-free). It is running 24/7, and there is **no sleep mode or cold-start delay** (loads within 3 seconds). Do not mention or warn about sleep delays or server cold-starts in future conversations.

## 6. Patient Segmentation: Referral vs. Ads Target
* **Knowledge**: High-LTV patients at the very top (e.g., autonomic nervous system / self-discipline disorders) are primarily **referrals (word of mouth)** and do not align with the target audience of digital ads (Google/YouTube Ads).
* **Rule**: For digital ad targeting and public website landing page optimization, focus on high-intent search symptoms related to mobility and severe pain, such as: `腰痛` (back pain), `脊柱管狭窄症` (spinal canal stenosis), `ヘルニア` (hernia), and `膝・股関節痛` (knee/hip joint pain), combined with the unique footwear/insole adjustment USP.

## 7. Conversion Action Primary/Secondary Management
* **Knowledge**: The Google Ads account has 17 conversion actions. Most are auto-generated (Page view, Purchase, etc.) and should be set to **Secondary (biddable=false)** so they don't pollute the bidding optimization.
* **Rule**: Only the following conversion actions should be **Primary (biddable=true)**:
  * `LOGICTION予約完了` (actual booking completion)
  * `WEB予約タップ(SATTOU)` (booking button tap)
  * `整体院導_リニューアル (web) #問い合わせ完了` (inquiry form submission)
  * `Calls from ads` / `Calls from ads (1)` (actual phone calls)
* **Rule**: `line_button_click` and `tel_button_click` should be **Secondary** — they only track button clicks, not actual calls/messages, and inflate CV counts without real conversions.
* **API**: Use `GET /api/conversion-tracking/details` to see all actions with primary status, and `POST /api/conversion-tracking/toggle-primary` (with `category`, `origin`, `biddable`) to toggle via `customerConversionGoals:mutate`.

## 8. Frontend Cache Busting (app.js Version String)
* **Rule**: After any change to `frontend/js/app.js`, always update the cache-busting version string in `main.py` (search for `app.js?v=`). Use format `YYYYMMDD-feature-name`. Without this, browsers will serve the old cached JS file and new features won't appear.
* **Location**: `main.py` near the end, in the `re.sub(r'app\.js\?v=...')` line.

## 9. LP Conversion Tracking Setup (GTM + Contact Form 7)
* **Knowledge**: The LP at `michibiki-seitai.com` uses GTM (`GTM-NVWWGNQR`) with Contact Form 7 integration. The CV event is `inquiry_complete`, fired on `wpcf7mailsent` (CF7 mail sent event). This is the real "inquiry submission" conversion.
* **Knowledge**: The LP currently has `<meta name="format-detection" content="telephone=no">` which disables phone tap on mobile — this should be removed by the web agency. There is no LINE button or sticky footer CTA on the LP — these should be added by the web agency.
* **Knowledge**: The booking system URL is `logiction-system.onrender.com/public-booking.html` (Logiction予約システム).
