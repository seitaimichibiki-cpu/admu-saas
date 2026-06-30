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
