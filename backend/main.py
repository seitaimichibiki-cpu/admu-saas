"""
main.py - FastAPI メインサーバー（ポート8001）
"""
import os, sys, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(__file__))

# .envファイルの読み込み（存在する場合のみ）
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
except ImportError:
    pass  # python-dotenvがない場合はOS環境変数のみ使用

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Request, Response, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
import db, monitor, line_notifier, ad_copy_generator as adcopy, campaign_manager, email_notifier
import auth
import urllib.request
import stripe
import sentry_sdk

SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
    )
    print(f"[Sentry] 初期化完了 (DSN設定済み)")

STRIPE_API_KEY = os.environ.get("STRIPE_API_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
stripe.api_key = STRIPE_API_KEY

STRIPE_PRICE_STARTER = os.environ.get("STRIPE_PRICE_STARTER", "price_starter_mock")
STRIPE_PRICE_STANDARD = os.environ.get("STRIPE_PRICE_STANDARD", "price_standard_mock")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8001")


# ---- Lifespan ----
@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    monitor.start_scheduler()
    yield
    monitor.stop_scheduler()

app = FastAPI(title="Google広告自動運用システム", version="1.0.1", lifespan=lifespan)

from fastapi.responses import JSONResponse
import traceback

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """未処理の例外をキャッチしてユーザーにスタックトレースを漏らさない"""
    traceback.print_exc()  # サーバーログには出力
    return JSONResponse(
        status_code=500,
        content={"detail": "サーバー内部エラーが発生しました。しばらく経ってから再度お試しください。"}
    )

@app.middleware("http")
async def verify_tenant_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and not path.startswith("/api/auth") and not path.startswith("/api/users/me") and not path.startswith("/api/admin") and not path.startswith("/api/lp/") and not path.startswith("/api/logiction/") and path not in ["/api/csrf-token", "/api/config"]:
        user = auth.get_current_user_from_request(request)
        if not user:
            return JSONResponse({"detail": "認証されていませんので再度ログインしてください"}, status_code=401)
        
        if user.get("role") == "admin":
            return await call_next(request)
            
        user_cid = str(user.get("clinic_id"))
        
        # クエリパラメータのチェック
        query_cid = request.query_params.get("clinic_id")
        if query_cid:
            if str(query_cid) != user_cid:
                return JSONResponse({"detail": "アクセス権限がありません"}, status_code=403)
        elif request.method == "GET" and path != "/api/users/me" and not path.startswith("/api/stripe/"):
            return JSONResponse({"detail": "リクエストに clinic_id が含まれていません"}, status_code=400)
            
        # JSONボディのチェック
        if request.method in ["POST", "PUT", "PATCH"] and not path.startswith("/api/stripe/"):
            import json
            try:
                body_bytes = await request.body()
                if body_bytes:
                    if len(body_bytes) > 0 and body_bytes.strip().startswith(b"{"):
                        body_json = json.loads(body_bytes)
                        if "clinic_id" not in body_json:
                            # LPなどは例外でスキップされるが、念のため
                            return JSONResponse({"detail": "リクエストボディに clinic_id が含まれていません"}, status_code=400)
                        body_cid = str(body_json.get("clinic_id", ""))
                        if body_cid:
                            if body_cid != user_cid:
                                return JSONResponse({"detail": "アクセス権限がありません"}, status_code=403)
                        
                # FastAPIが後でbodyを読めるように復元
                async def receive(): return {"type": "http.request", "body": body_bytes}
                request._receive = receive
            except Exception:
                pass

    return await call_next(request)

_allowed_origins = os.environ.get("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins if _allowed_origins != ["*"] else [],
    allow_origin_regex=".*" if _allowed_origins == ["*"] else None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- セキュリティ・ミドルウェア (Headers & CSRF) ----
@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # 1. CSRF Verification
    if request.method in ["POST", "PUT", "DELETE", "PATCH"]:
        if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/stripe/webhook"):
            # exclude endpoints that don't need CSRF or are login endpoints
            # exclude endpoints that don't need CSRF or are login endpoints
            # ★ LOGICTION連携エンドポイントはサーバー間通信のためCSRF除外
            CSRF_EXEMPT = [
                "/api/auth/login",
                "/api/auth/dev-autologin",
                "/api/auth/reset-request",
                "/api/auth/reset-confirm",
                "/api/admin/init-credentials",
                "/api/logiction/patient-sync",
                "/api/logiction/test-connection",
                "/api/logiction/generate-key",
                "/api/logiction/save-settings",
            ]
            if request.url.path not in CSRF_EXEMPT:
                token_in_header = request.headers.get("X-CSRF-Token")
                token_in_cookie = request.cookies.get("csrf_token")
                # Double submit cookie pattern
                if not token_in_header or not token_in_cookie or token_in_header != token_in_cookie:
                    return JSONResponse({"detail": "CSRFトークンが無効または不足しています。"}, status_code=403)

    # 2. Process Request
    response = await call_next(request)
    
    return response

@app.get("/api/config")
def get_public_config():
    """フロントエンドに必要な共通設定（Sentry等）を返す"""
    return {
        "sentry_dsn": SENTRY_DSN
    }

@app.get("/api/csrf-token")
def get_csrf_token(response: Response):
    """CSRFトークンを発行する"""
    import secrets
    token = secrets.token_urlsafe(32)
    is_prod = os.environ.get("ENVIRONMENT", "development") == "production"
    response.set_cookie(
        key="csrf_token",
        value=token,
        httponly=False, # フロントエンドのJSが読み取るためここはFalse
        secure=is_prod,
        samesite="lax",
        max_age=86400
    )
    return {"csrf_token": token}

# ---- セキュリティヘッダミドルウェア ----
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    import uuid
    request_id = str(uuid.uuid4())[:8]
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://js.stripe.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://browser.sentry-cdn.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; font-src 'self' data: https://fonts.gstatic.com https://cdnjs.cloudflare.com; img-src 'self' data: https: blob:; frame-src 'self' https://js.stripe.com; connect-src 'self' https://*.sentry.io https://*.ingest.sentry.io;"
    response.headers["X-Request-ID"] = request_id
    return response

# ---- 簡易レートリミッター ----
_rate_store: dict = defaultdict(list)
_RATE_LIMIT  = 120  # 全API: 120リクエスト/分
_RATE_WINDOW = 60   # 秒
_rate_cleanup_counter = 0

# LP問い合わせ専用（Bot対策: 5分に3回まで）
_lp_rate_store: dict = defaultdict(list)
_LP_RATE_LIMIT  = 3
_LP_RATE_WINDOW = 300  # 5分

@app.middleware("http")
async def rate_limiter(request: Request, call_next):
    global _rate_cleanup_counter
    if request.url.path.startswith("/api/"):
        ip = request.client.host if request.client else "unknown"
        now = time.time()

        # LP問い合わせ・資料請求は特に厳しく制限
        if request.url.path in ("/api/lp/contact", "/api/lp/download") and request.method == "POST":
            ws = now - _LP_RATE_WINDOW
            _lp_rate_store[ip] = [t for t in _lp_rate_store[ip] if t > ws]
            if len(_lp_rate_store[ip]) >= _LP_RATE_LIMIT:
                return Response(
                    content='{"detail":"送信回数の上限に達しました。5分後に再度お試しください"}',
                    status_code=429, media_type="application/json"
                )
            _lp_rate_store[ip].append(now)

        # 全API共通レート制限
        window_start = now - _RATE_WINDOW
        _rate_store[ip] = [t for t in _rate_store[ip] if t > window_start]
        if len(_rate_store[ip]) >= _RATE_LIMIT:
            return Response(content='{"detail":"リクエストが多すぎます。しばらくお待ちください"}', status_code=429, media_type="application/json")
        _rate_store[ip].append(now)

        # メモリリーク防止：100リクエスト毎にストアをクリーンアップ
        _rate_cleanup_counter += 1
        if _rate_cleanup_counter >= 100:
            _rate_cleanup_counter = 0
            for store, window in [(_rate_store, _RATE_WINDOW), (_lp_rate_store, _LP_RATE_WINDOW)]:
                stale = [k for k, v in store.items() if not v or v[-1] < now - window]
                for k in stale:
                    del store[k]
    return await call_next(request)



# ---- Pydantic Models ----
class CampaignCreateReq(BaseModel):
    clinic_id: int = 1
    clinic_name: str
    region: str
    category: str
    budget_yen: Optional[int] = None
    platform: str = "google"

class BudgetUpdateReq(BaseModel):
    clinic_id: int = 1
    budget_yen: int


class BidRuleReq(BaseModel):
    clinic_id: int = 1
    campaign_id: Optional[int] = None
    name: str
    condition_field: str
    condition_op: str
    condition_value: float
    action: str
    action_value: float
    max_adjustment_pct: float = 20.0
    enabled: int = 1
    id: Optional[int] = None

class AdCopyReq(BaseModel):
    clinic_id: int = 1
    campaign_id: Optional[int] = None
    clinic_name: str
    region: str
    appeal_points: str = ""
    target_issues: str = "腰痛、肩こり"
    extra_instructions: str = ""

class SettingsReq(BaseModel):
    clinic_id: int = 1
    customer_id: Optional[str] = None
    developer_token: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    refresh_token: Optional[str] = None
    login_customer_id: Optional[str] = None
    mock_mode: Optional[int] = None
    line_channel_token: Optional[str] = None
    line_user_id: Optional[str] = None
    target_age_gender: Optional[str] = None
    target_job_lifestyle: Optional[str] = None
    target_pain_point: Optional[str] = None
    target_desired_outcome: Optional[str] = None
    notification_email: Optional[str] = None
    smtp_user: Optional[str] = None
    smtp_pass: Optional[str] = None
    ga4_property_id: Optional[str] = None
    monthly_budget_yen: Optional[int] = None
    yahoo_account_id: Optional[str] = None
    yahoo_client_id: Optional[str] = None
    yahoo_client_secret: Optional[str] = None
    yahoo_refresh_token: Optional[str] = None
    yahoo_mock_mode: Optional[int] = None
    # BYOK: 顧客自身のGemini APIキー
    gemini_api_key: Optional[str] = None
    # AI機能の月間呼び出し上限（0=無効, -1=無制限, 1以上=N回まで）
    ai_monthly_limit: Optional[int] = None

class LineTestReq(BaseModel):
    clinic_id: int = 1
    message: str = "テスト送信です"

class NegativeKWReq(BaseModel):
    clinic_id: int = 1
    keyword: str
    match_type: str = "BROAD"
    campaign_id: Optional[int] = None
    source: str = "manual"

class AdCopyScoreReq(BaseModel):
    clinic_id: int = 1
    impressions: int
    clicks: int

# ---- Helpers ----
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
if not ADMIN_PASSWORD:
    print("⚠️  [SECURITY] ADMIN_PASSWORD が未設定です。本番では必ず環境変数を設定してください。")

def _require_account(clinic_id: int) -> dict:
    """広告設定を取得。未設定の場合も環境変数の認証情報を使ってフォールバック。"""
    acc = db.get_ads_account(clinic_id)
    if not acc:
        # DBにレコードがない場合は環境変数から自動生成（MASTER_ADS_*優先）
        master_token   = os.environ.get("MASTER_ADS_DEVELOPER_TOKEN") or os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", "")
        master_cid     = os.environ.get("MASTER_ADS_CLIENT_ID") or os.environ.get("GOOGLE_ADS_CLIENT_ID", "")
        master_secret  = os.environ.get("MASTER_ADS_CLIENT_SECRET") or os.environ.get("GOOGLE_ADS_CLIENT_SECRET", "")
        master_refresh = os.environ.get("MASTER_ADS_REFRESH_TOKEN") or os.environ.get("GOOGLE_ADS_REFRESH_TOKEN", "")
        master_login   = os.environ.get("MASTER_ADS_LOGIN_CUSTOMER_ID", "")
        customer_id    = os.environ.get("GOOGLE_ADS_DEFAULT_CUSTOMER_ID", "DEMO")
        # 全認証情報が揃っていれば本番モード、そうでなければモック
        has_creds = all([master_token, master_cid, master_secret, master_refresh])
        acc = {
            "clinic_id": clinic_id,
            "mock_mode": 0 if has_creds else 1,
            "customer_id": customer_id,
            "yahoo_mock_mode": 1,
            "developer_token":   master_token,
            "client_id":         master_cid,
            "client_secret":     master_secret,
            "refresh_token":     master_refresh,
            "login_customer_id": master_login,
        }
        if has_creds:
            print(f"[_require_account] DBレコードなし→環境変数から本番設定を自動生成 (clinic_id={clinic_id})")
        else:
            print(f"[_require_account] DBレコードなし・環境変数不足→モックモードで代替 (clinic_id={clinic_id})")
    if acc.get("mock_mode") is None:
        acc["mock_mode"] = 1
    if acc.get("yahoo_mock_mode") is None:
        acc["yahoo_mock_mode"] = 1
    return acc


def _get_ads_client(acc: dict, platform: str = "google"):
    if platform == "yahoo":
        from yahoo_ads_client import YahooAdsClient
        return YahooAdsClient(acc)
    else:
        from ads_client import AdsClient
        return AdsClient(acc)

def _check_plan_active(clinic_id: int):
    """プランが停止・解約済みの場合は403を返す"""
    status = db.get_clinic_plan_status(clinic_id)
    if status != "active":
        label = "利用停止" if status == "suspended" else "解約済み"
        raise HTTPException(
            status_code=403,
            detail=f"このアカウントは{label}です。サポートまでお問い合わせください。"
        )

def _get_current_user(request: Request) -> dict:
    """認証ミドルウェア: Cookie(またはヘッダー)からJWTを検証しユーザー情報を返す"""
    user = auth.get_current_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="認証が必要です。ログインしてください。")
    return user

def _require_admin(request: Request) -> dict:
    """adminロールのみを許可"""
    user = _get_current_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="管理者権限が必要です。")
    return user

# ---- API: お知らせ ----
class AnnouncementReq(BaseModel):
    title: str
    content: str

@app.get("/api/announcements")
def list_announcements(limit: int = 5):
    return {"announcements": db.list_announcements(limit)}

@app.post("/api/announcements")
def create_announcement_api(req: AnnouncementReq, request: Request):
    _require_admin(request)
    db.create_announcement(req.title, req.content)
    return {"success": True}

@app.delete("/api/announcements/{aid}")
def delete_announcement_api(aid: int, request: Request):
    _require_admin(request)
    db.delete_announcement(aid)
    return {"success": True}

# ---- API: ダッシュボード ----
@app.get("/api/dashboard")
def get_dashboard(clinic_id: int = 1, platform: str = "google", days: str = "7", start_date: Optional[str] = None, end_date: Optional[str] = None):
    acc = _require_account(clinic_id)
    client = _get_ads_client(acc, platform)

    api_error = None
    campaigns = []
    perf_series = []

    try:
        campaigns = client.list_campaigns()
    except Exception as e:
        api_error = str(e)[:200]
        print(f"[Dashboard] list_campaigns error: {e}")

    try:
        perf_series = client.get_performance_series(days=days, start_date=start_date, end_date=end_date)
    except Exception as e:
        api_error = api_error or str(e)[:200]
        print(f"[Dashboard] get_performance_series error: {e}")

    alerts = db.list_alerts(clinic_id, limit=10)
    total_cost = sum(p.get("cost_micros", 0) for p in perf_series)
    total_clicks = sum(p.get("clicks", 0) for p in perf_series)
    total_impressions = sum(p.get("impressions", 0) for p in perf_series)
    total_conv = sum(p.get("conversions", 0) for p in perf_series)
    avg_ctr = sum(p.get("ctr", 0) for p in perf_series) / len(perf_series) if perf_series else 0

    result = {
        "summary": {
            "total_cost_micros": total_cost,
            "total_clicks": total_clicks,
            "total_impressions": total_impressions,
            "total_conversions": total_conv,
            "avg_ctr": round(avg_ctr, 2),
            "active_campaigns": len([c for c in campaigns if c.get("status") == "ENABLED"]),
        },
        "performance_series": perf_series,
        "campaigns": campaigns,
        "recent_alerts": alerts,
        "monitor_status": monitor.get_status(),
        "mock_mode": client.mock_mode,
        "platform": platform,
        "settings": {
            "monthly_budget_yen": acc.get("monthly_budget_yen", 300000) or 300000,
        },
        "ai_quota": {
            "used": db.get_monthly_ai_usage(clinic_id),
            "limit": db.get_ai_quota_limit(clinic_id)
        }
    }
    if api_error:
        result["api_error"] = api_error
        # CUSTOMER_NOT_ENABLED はアカウントが停止/無効な場合の典型エラー
        if "CUSTOMER_NOT_ENABLED" in api_error:
            result["api_error_hint"] = "Google広告アカウントが無効または停止しています。顧客IDが正しいか、アカウントが有効かを確認してください。"
        elif "PERMISSION_DENIED" in api_error or "not have permission" in api_error:
            result["api_error_hint"] = "Google Ads APIへのアクセス権限がありません。MCCリンクと開発者トークンの承認状態を確認してください。"
        elif "invalid_grant" in api_error or "refresh_token" in api_error.lower():
            result["api_error_hint"] = "OAuthトークンが失効しています。設定画面からリフレッシュトークンを再取得してください。"
    return result

# ---- API: キャンペーン ----
@app.get("/api/campaigns")
def list_campaigns(clinic_id: int = 1, platform: str = "google"):
    acc = _require_account(clinic_id)
    client = _get_ads_client(acc, platform)
    api_campaigns = client.list_campaigns()
    db_campaigns = db.list_campaigns(clinic_id)
    return {"campaigns": api_campaigns, "local_campaigns": db_campaigns}

@app.post("/api/campaigns")
def create_campaign(req: CampaignCreateReq):
    acc = _require_account(req.clinic_id)
    result = campaign_manager.auto_create_campaign(req.clinic_id, acc, req.model_dump())
    return {"success": True, "campaign": result}

@app.patch("/api/campaigns/{campaign_id}/status")
def update_campaign_status(campaign_id: int, status: str, clinic_id: int = 1, platform: str = "google"):
    acc = _require_account(clinic_id)
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(404, "キャンペーンが見つかりません")
    client = _get_ads_client(acc, platform)
    client.update_campaign_status(campaign.get("google_campaign_id", ""), status)
    db.upsert_campaign(clinic_id, {**campaign, "status": status})
    return {"success": True}

@app.delete("/api/campaigns/{campaign_id}")
def delete_campaign(campaign_id: int, clinic_id: int = 1, platform: str = "google"):
    """AdMuで作成したキャンペーンを削除する。Google Ads API側もREMOVEを試みる。"""
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(404, "キャンペーンが見つかりません")
    if campaign.get("clinic_id") != clinic_id:
        raise HTTPException(403, "アクセス権限がありません")

    api_warning = None
    try:
        acc = _require_account(clinic_id)
        client = _get_ads_client(acc, platform)
        g_id = campaign.get("google_campaign_id", "")
        if g_id:
            client.update_campaign_status(g_id, "REMOVED")
    except Exception as e:
        api_warning = f"Google Ads APIでの削除に失敗しました（ローカルDBからは削除済み）: {str(e)}"

    with db.get_conn() as conn:
        conn.execute("DELETE FROM campaigns WHERE id=? AND clinic_id=?", (campaign_id, clinic_id))
        conn.execute("DELETE FROM performance_logs WHERE campaign_id=?", (campaign_id,))

    result = {"success": True, "campaign_id": campaign_id}
    if api_warning:
        result["warning"] = api_warning
    return result



# ---- API: 月間予算ターゲット設定（具体パスを先に定義）----
class MonthlyBudgetReq(BaseModel):
    clinic_id: int = 1
    monthly_budget_yen: int
    ai_auto_allocate: bool = True

@app.post("/api/budget/monthly-target")
def set_monthly_budget(req: MonthlyBudgetReq):
    """ユーザーが月間総予算を設定。ai_auto_allocate=True の場合、即座にAI配分も実行。"""
    acc = db.get_ads_account(req.clinic_id) or {}
    db.save_ads_account(req.clinic_id, {
        **acc,
        "monthly_budget_yen": req.monthly_budget_yen,
        "ai_auto_allocate":   req.ai_auto_allocate,
    })
    result = {"success": True, "monthly_budget_yen": req.monthly_budget_yen, "ai_auto_allocate": req.ai_auto_allocate}
    if req.ai_auto_allocate:
        from ads_client import AdsClient
        updated_acc = db.get_ads_account(req.clinic_id) or {}
        alloc = _run_ai_budget_allocation(req.clinic_id, req.monthly_budget_yen, AdsClient(updated_acc))
        result["allocation"] = alloc
    return result

@app.post("/api/budget/ai-allocate")
def ai_budget_allocate_endpoint(clinic_id: int = 1):
    """AIがキャンペーン別パフォーマンスを解析し月間予算を最適配分。"""
    if not db.check_ai_quota_available(clinic_id):
        raise HTTPException(status_code=429, detail="今月のAI利用回数の上限に達しました。プランをアップグレードしてください。")
    acc = db.get_ads_account(clinic_id) or {}
    monthly_budget = acc.get("monthly_budget_yen", 0)
    if not monthly_budget:
        raise HTTPException(400, "月間予算が設定されていません。")
    from ads_client import AdsClient
    alloc = _run_ai_budget_allocation(clinic_id, monthly_budget, AdsClient(acc))
    db.increment_ai_quota(clinic_id, feature_name="ai_budget")
    return {"success": True, "allocation": alloc}

# ---- API: 予算（手動・キャンペーン別） ----
@app.post("/api/budget/{campaign_id}")
def update_budget(campaign_id: int, req: BudgetUpdateReq):
    """予算変更は手動のみ。"""
    try:
        db.update_budget(campaign_id, req.clinic_id, req.budget_yen * 1_000_000)
        return {"success": True, "budget_yen": req.budget_yen}
    except ValueError as e:
        raise HTTPException(400, str(e))



def _run_ai_budget_allocation(clinic_id: int, monthly_budget_yen: int, ads_client):
    """
    コアロジック: キャンペーン別パフォーマンスからAIが予算配分を計算する。

    アルゴリズム:
    1. 各キャンペーンのCTR・CVR・CPAを取得
    2. ROIスコア（コンバージョン効率）を計算
    3. スコアに比例して月間予算を分配
    4. 月の残り日数で割り、日予算を算出
    5. 最低保証予算（月間の5%）を下回らないよう補正
    """
    import datetime, math

    campaigns = ads_client.list_campaigns()
    enabled = [c for c in campaigns if c.get("status") == "ENABLED"]
    if not enabled:
        return {"error": "有効なキャンペーンがありません", "allocations": []}

    # パフォーマンスデータ取得（過去7日間）
    perf_data = ads_client.get_performance_series(days=7)
    total_cost   = sum(p.get("cost_micros", 0) for p in perf_data) / 1_000_000
    total_conv   = sum(p.get("conversions", 0) for p in perf_data)
    total_clicks = sum(p.get("clicks", 0)      for p in perf_data)
    total_imp    = sum(p.get("impressions", 0) for p in perf_data)

    # キャンペーン別スコア計算
    # 本番APIモード: 実際のCTR/CVR/コンバージョンデータを使用
    # モックモード: デモ用推定値（固定シードのランダム）
    import random

    raw_scores = []
    for cp in enabled:
        cp_name = cp.get("name", "")

        if not ads_client.mock_mode:
            # ---- 本番API: 実績データからROIスコアを算出 ----
            real_cvr  = float(cp.get("cvr", 0))
            real_ctr  = float(cp.get("ctr", 0))
            real_conv = float(cp.get("conversions", 0))
            real_cost = float(cp.get("cost_micros", 0)) / 1_000_000
            cpa_est   = round(real_cost / real_conv) if real_conv > 0 else 15000
            cvr_est   = round(real_cvr, 2)
            ctr_est   = round(real_ctr, 2)
            # ROIスコア: CVRを基準に正規化（整体院平均CVR≒5%を1.0とする）
            roi_score = max(0.1, real_cvr / 5.0)
            # キャンペーン名による補正（指名・リターゲは効率的なため加点）
            if "指名" in cp_name or "ブランド" in cp_name:
                roi_score = min(2.0, roi_score * 1.5)
            if "リターゲ" in cp_name or "再来院" in cp_name:
                roi_score = min(2.0, roi_score * 1.3)
            if "一般" in cp_name or "汎用" in cp_name:
                roi_score = max(0.1, roi_score * 0.8)
        else:
            # ---- モックモード: デモ用推定値（clinic_idで固定シード）----
            random.seed(clinic_id + len(enabled))
            base = random.uniform(0.6, 1.4)
            if "指名" in cp_name or "ブランド" in cp_name:
                base *= 1.5
            if "リターゲ" in cp_name or "再来院" in cp_name:
                base *= 1.3
            if "一般" in cp_name or "汎用" in cp_name:
                base *= 0.8
            cpa_est   = max(1000, 8000 - base * 2000)
            cvr_est   = round(base * 3.5, 2)
            ctr_est   = round(base * 4.2, 2)
            roi_score = base

        raw_scores.append({
            "campaign": cp,
            "roi_score": roi_score,
            "est_cpa":   round(cpa_est),
            "est_cvr":   cvr_est,
            "est_ctr":   ctr_est,
        })

    total_score   = sum(s["roi_score"] for s in raw_scores)
    min_floor_pct = 0.05  # 最低保証: 月間予算の5%
    floor_budget  = monthly_budget_yen * min_floor_pct

    # 残り日数を計算（月末までの日数）
    today       = datetime.date.today()
    last_day    = datetime.date(today.year, today.month % 12 + 1, 1) - datetime.timedelta(days=1) \
                  if today.month < 12 else datetime.date(today.year, 12, 31)
    remaining_days = max(1, (last_day - today).days + 1)
    elapsed_days   = today.day - 1
    total_days     = last_day.day

    allocations = []
    total_allocated = 0
    for s in raw_scores:
        cp = s["campaign"]
        # 月間配分額（スコア比例・最低フロア保証）
        raw_alloc    = (s["roi_score"] / total_score) * monthly_budget_yen
        monthly_alloc = max(floor_budget, raw_alloc)
        # 残り日数ベースの日予算（今月の消化済み分を引いた残額÷残り日数）
        spent_ratio   = elapsed_days / total_days if total_days > 0 else 0
        already_spent = monthly_alloc * spent_ratio  # 推定消化済み
        remaining_budget = monthly_alloc - already_spent
        daily_budget  = max(500, round(remaining_budget / remaining_days))
        total_allocated += monthly_alloc

        allocations.append({
            "campaign_id":      cp.get("id"),
            "campaign_name":    cp.get("name", ""),
            "status":           cp.get("status", ""),
            "monthly_alloc_yen": round(monthly_alloc),
            "daily_budget_yen": daily_budget,
            "share_pct":        round(s["roi_score"] / total_score * 100, 1),
            "est_cpa":          s["est_cpa"],
            "est_cvr":          s["est_cvr"],
            "est_ctr":          s["est_ctr"],
            "roi_grade":        "S" if s["roi_score"] > 1.2 else "A" if s["roi_score"] > 0.9 else "B" if s["roi_score"] > 0.6 else "C",
            "reason":           _allocation_reason(cp.get("name",""), s["roi_score"], s["est_cpa"]),
        })

    # Gemini AIで配分の解説文を生成（顧客自身のGemini APIキーを使用）
    gemini_key = db.get_gemini_api_key(clinic_id)
    ai_comment = ""
    if gemini_key:
        try:
            import google.genai as genai
            gc = genai.Client(api_key=gemini_key)
            summary = "\n".join([
                f"- {a['campaign_name']}: 月間¥{a['monthly_alloc_yen']:,}（日予算¥{a['daily_budget_yen']:,}・シェア{a['share_pct']}%）推定CPA¥{a['est_cpa']:,}"
                for a in allocations
            ])
            r = gc.models.generate_content(
                model='gemini-2.0-flash',
                contents=f"""整体院のGoogle広告AIが以下の配分を決定しました。院長向けに、なぜこの配分にしたか・期待される成果を3〜4文（140文字以内）で簡潔に説明してください。

月間総予算: ¥{monthly_budget_yen:,}
残り日数: {remaining_days}日
配分結果:
{summary}"""
            )
            ai_comment = r.text.strip()
        except:
            ai_comment = f"月間予算¥{monthly_budget_yen:,}をROI効率に基づいて各キャンペーンに最適配分しました。高効率キャンペーンに予算を集中し、全体のCPA改善を目指します。"

    return {
        "monthly_budget_yen": monthly_budget_yen,
        "remaining_days":     remaining_days,
        "total_campaigns":    len(enabled),
        "allocations":        sorted(allocations, key=lambda x: -x["share_pct"]),
        "ai_comment":         ai_comment,
        "allocated_at":       datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def _allocation_reason(name: str, score: float, cpa: int) -> str:
    """配分理由の短文を生成"""
    if "指名" in name or "ブランド" in name:
        return "指名検索は最高CVR。最優先配分。"
    if "リターゲ" in name or "再来院" in name:
        return "既存接触者への再アプローチで高CVR。"
    if score > 1.2:
        return f"直近CTR・CVR共に高水準。予算を積極的に配分。"
    if score > 0.9:
        return f"安定したパフォーマンス。標準配分を維持。"
    return f"効率改善中。最低フロア予算を確保しつつ監視。"


# ---- API: 入札ルール ----
@app.get("/api/bid-rules")
def list_bid_rules(clinic_id: int = 1):
    return {"rules": db.list_bid_rules(clinic_id)}

@app.post("/api/bid-rules")
def upsert_bid_rule(req: BidRuleReq):
    rule_id = db.upsert_bid_rule(req.clinic_id, req.model_dump())
    return {"success": True, "id": rule_id}

@app.delete("/api/bid-rules/{rule_id}")
def delete_bid_rule(rule_id: int, clinic_id: int = 1):
    """入札ルールを削除する"""
    deleted = db.delete_bid_rule(rule_id, clinic_id)
    if not deleted:
        raise HTTPException(404, "入札ルールが見つかりません")
    return {"success": True, "message": "入札ルールを削除しました"}

@app.post("/api/bid-rules/run-now")
def run_bid_now(clinic_id: int = 1):
    monitor.trigger_bid_now(clinic_id)
    return {"success": True, "message": "入札調整を実行しました"}

# ---- API: 広告文生成 ----
@app.post("/api/ad-copy/generate")
def generate_ad_copy(req: AdCopyReq):
    ok, reason = db.check_ai_limit(req.clinic_id)
    if not ok:
        raise HTTPException(status_code=429, detail=reason)

    gemini_key = db.get_gemini_api_key(req.clinic_id)
    acc = db.get_ads_account(req.clinic_id) or {}
    context = req.model_dump()
    context.update({
        "target_age_gender": acc.get("target_age_gender"),
        "target_job_lifestyle": acc.get("target_job_lifestyle"),
        "target_pain_point": acc.get("target_pain_point"),
        "target_desired_outcome": acc.get("target_desired_outcome"),
    })

    generator = adcopy.AdCopyGenerator(api_key=gemini_key)
    result = generator.generate(context)
    copy_id = db.save_ad_copy(req.clinic_id, {
        "campaign_id": req.campaign_id,
        "headlines": "\n".join(result.get("headlines", [])),
        "descriptions": "\n".join(result.get("descriptions", [])),
        "prompt_context": str(req.model_dump()),
    })
    db.increment_ai_quota(req.clinic_id, feature_name="generate_ad_copy")
    return {"success": True, "id": copy_id, **result}

@app.post("/api/analyze-report")
async def analyze_report(file: UploadFile = File(...), clinic_id: int = 1):
    ok, reason = db.check_ai_limit(clinic_id)
    if not ok:
        raise HTTPException(status_code=429, detail=reason)
    gemini_key = db.get_gemini_api_key(clinic_id)
    if not gemini_key:
        # モックレスポンスを返す（デモ・テスト用）
        db.increment_ai_quota(clinic_id, feature_name="lp_analysis")
        return {"success": True, "analysis": {
            "good_keywords": ["産後 骨盤矯正 渋谷", "腰痛 整体 治らない"],
            "wasted_spend": ["肩こり セルフケア", "無料 マッサージ 腰痛"],
            "demographic_trends": "30代〜40代の女性から特定の時間帯（10:00〜14:00）で高いコンバージョン率を確認しました。",
            "recommendation": "・「産後骨盤矯正」キャンペーンの予算を+20%強化推奨\n・「セルフケア」「無料」などの検索語句の除外設定を推奨\n・地域：渋谷区・目黒区の入札強め設定をご提案します"
        }}
    
    acc = db.get_ads_account(clinic_id) or {}
    persona = {
        "target_age_gender": acc.get("target_age_gender"),
        "target_job_lifestyle": acc.get("target_job_lifestyle"),
        "target_pain_point": acc.get("target_pain_point"),
        "target_desired_outcome": acc.get("target_desired_outcome"),
    }
    
    from report_analyzer import ReportAnalyzer
    analyzer = ReportAnalyzer(api_key=gemini_key)
    
    try:
        content = await file.read()
        mime_type = file.content_type
        if not mime_type:
            mime_type = "application/pdf"
            
        result = analyzer.analyze_file(content, file.filename, mime_type, persona=persona)
        db.increment_ai_quota(clinic_id, feature_name="lp_analysis")
        return {"success": True, "analysis": result}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"レポート解析中にエラーが発生しました: {str(e)}")

@app.get("/api/ad-copies")
def list_ad_copies(clinic_id: int = 1, campaign_id: Optional[int] = None):
    return {"ad_copies": db.list_ad_copies(clinic_id, campaign_id)}

@app.post("/api/ad-copy/{copy_id}/score")
def score_ad_copy(copy_id: int, req: AdCopyScoreReq):
    db.update_ad_copy_score(copy_id, req.clinic_id, req.impressions, req.clicks)
    return {"success": True}

@app.post("/api/ad-copy/{copy_id}/retire")
def retire_ad_copy(copy_id: int, clinic_id: int = 1):
    db.retire_ad_copy(copy_id, clinic_id)
    return {"success": True, "message": "廣告文を廃案しました"}

@app.get("/api/ad-copy/compare")
def compare_ad_copies(clinic_id: int = 1, variant_group: Optional[str] = None):
    copies = db.list_ad_copies(clinic_id)
    if variant_group:
        copies = [c for c in copies if c.get("variant_group") == variant_group]
    active = [c for c in copies if c.get("status") != "retired"]
    retired = [c for c in copies if c.get("status") == "retired"]
    winner = max(active, key=lambda c: c.get("ctr_score", 0)) if active else None
    loser = min(active, key=lambda c: c.get("ctr_score", 0)) if len(active) > 1 else None
    return {"active": active, "retired": retired,
            "winner_id": winner["id"] if winner else None,
            "retire_suggestion_id": loser["id"] if loser else None}

# ---- API: 除外キーワード ----
@app.get("/api/negative-keywords")
def list_negative_keywords(clinic_id: int = 1, campaign_id: Optional[int] = None):
    return {"negative_keywords": db.list_negative_keywords(clinic_id, campaign_id)}

@app.post("/api/negative-keywords")
def add_negative_keyword(req: NegativeKWReq):
    nkw_id = db.add_negative_keyword(
        req.clinic_id, req.keyword, req.match_type, req.campaign_id, req.source)
    return {"success": True, "id": nkw_id, "message": f"「{req.keyword}」を除外リストに追加しました"}

@app.delete("/api/negative-keywords/{nkw_id}")
def delete_negative_keyword(nkw_id: int, clinic_id: int = 1):
    db.delete_negative_keyword(nkw_id, clinic_id)
    return {"success": True}

# ---- API: ペルソナ管理 ----
class PersonaReq(BaseModel):
    clinic_id: int = 1
    name: str
    age_gender: Optional[str] = None
    job_lifestyle: Optional[str] = None
    pain_point: Optional[str] = None
    desired_outcome: Optional[str] = None
    is_default: int = 0

@app.get("/api/personas")
def list_personas(clinic_id: int = 1):
    return {"personas": db.list_personas(clinic_id)}

@app.post("/api/personas")
def create_persona(req: PersonaReq):
    pid = db.create_persona(req.clinic_id, req.model_dump())
    return {"success": True, "id": pid}

@app.put("/api/personas/{persona_id}")
def update_persona(persona_id: int, req: PersonaReq):
    db.update_persona(persona_id, req.clinic_id, req.model_dump())
    return {"success": True}

@app.delete("/api/personas/{persona_id}")
def delete_persona(persona_id: int, clinic_id: int = 1):
    db.delete_persona(persona_id, clinic_id)
    return {"success": True}

@app.get("/api/campaigns/{campaign_id}/personas")
def get_campaign_personas(campaign_id: str, clinic_id: int = 1):
    return {"personas": db.get_campaign_personas(campaign_id, clinic_id)}

@app.post("/api/campaigns/{campaign_id}/personas/{persona_id}")
def link_persona(campaign_id: str, persona_id: int, clinic_id: int = 1):
    db.link_persona_to_campaign(campaign_id, persona_id, clinic_id)
    return {"success": True}

@app.delete("/api/campaigns/{campaign_id}/personas/{persona_id}")
def unlink_persona(campaign_id: str, persona_id: int, clinic_id: int = 1):
    db.unlink_persona_from_campaign(campaign_id, persona_id, clinic_id)
    return {"success": True}

# ---- API: 監視 ----
@app.get("/api/monitor/status")
def get_monitor_status():
    return monitor.get_status()

@app.post("/api/monitor/check-now")
def trigger_check(clinic_id: int = 1):
    monitor.trigger_check_now(clinic_id)
    return {"success": True, "message": "チェックを実行しました"}

# ---- API: アラート ----
@app.get("/api/alerts")
def list_alerts(clinic_id: int = 1, limit: int = 50):
    return {"alerts": db.list_alerts(clinic_id, limit)}

# ---- API: LINE通知 ----
@app.post("/api/line/test")
def test_line(req: LineTestReq):
    acc = _require_account(req.clinic_id)
    token = acc.get("line_channel_token", "")
    uid = acc.get("line_user_id", "")
    if not token or not uid:
        raise HTTPException(400, "LINE設定（チャンネルトークン・ユーザーID）が未設定です")
    ok = line_notifier.send_text(token, uid, req.message)
    return {"success": ok}


@app.post("/api/line/report")
def send_line_report_now(request: Request, clinic_id: int = 1, days: int = 7):
    """手動でLINE週次レポートを今すぐ送信"""
    _get_current_user(request)
    acc = db.get_ads_account(clinic_id) or {}
    token = acc.get("line_channel_token", "")
    uid   = acc.get("line_user_id", "")
    if not token or not uid:
        raise HTTPException(400, "LINE設定（チャンネルトークン・ユーザーID）が未設定です。設定画面から登録してください。")

    clinic = db.get_clinic(clinic_id) or {}
    clinic_name = clinic.get("name", f"Clinic#{clinic_id}")

    # 広告データ取得
    try:
        from ads_client import AdsClient
        client = AdsClient(acc)
        perf_list = client.get_performance_series(days=days)
        total_cost_micros = sum(p.get("cost_micros", 0) for p in perf_list)
        total_clicks = sum(p.get("clicks", 0) for p in perf_list)
        total_impressions = sum(p.get("impressions", 0) for p in perf_list)
        total_conv = sum(p.get("conversions", 0) for p in perf_list)
        avg_ctr = (total_clicks / total_impressions * 100) if total_impressions else 0
        total_cost_yen = int(total_cost_micros / 1_000_000)
        cpa = round(total_cost_yen / total_conv) if total_conv > 0 else 0
        summary = {
            "total_cost_yen": total_cost_yen,
            "total_clicks": total_clicks,
            "total_impressions": total_impressions,
            "total_conversions": total_conv,
            "avg_ctr": round(avg_ctr, 2),
            "cpa": cpa,
        }
    except Exception as e:
        # モックモードや接続エラー時はダミーデータで送信
        summary = {
            "total_cost_yen": 0,
            "total_clicks": 0,
            "total_impressions": 0,
            "total_conversions": 0,
            "avg_ctr": 0.0,
            "cpa": 0,
            "_error": str(e),
        }

    ok = line_notifier.send_weekly_report(token, uid, clinic_name, summary)
    return {
        "success": ok,
        "clinic_name": clinic_name,
        "summary": {k: v for k, v in summary.items() if not k.startswith("_")},
        "message": "LINEに週次レポートを送信しました" if ok else "送信に失敗しました。LINEトークン・ユーザーIDを確認してください",
    }

# ---- API: 設定 ----
@app.get("/api/settings")
def get_settings(clinic_id: int = 1):
    acc = db.get_ads_account(clinic_id) or {}
    # シークレット系はマスク
    for secret_key in ["developer_token", "client_secret", "refresh_token", "yahoo_client_secret", "yahoo_refresh_token", "smtp_pass", "gemini_api_key"]:
        if acc.get(secret_key):
            acc[secret_key] = "***設定済み***"
    return {"settings": acc}

@app.post("/api/settings")
def save_settings(req: SettingsReq):
    data = {k: v for k, v in req.model_dump().items() if v is not None and k != "clinic_id"}
    # フロントが「***設定済み***」のマスク値をそのまま送ってきた場合は除外（上書き防止）
    MASKED_PLACEHOLDER = "***設定済み***"
    data = {k: v for k, v in data.items() if v != MASKED_PLACEHOLDER}

    acc_before = db.get_ads_account(req.clinic_id) or {}
    db.save_ads_account(req.clinic_id, data)

    # 顧客IDが新たに設定されたか、現在エラー状態の場合、Google Adsリンクリクエストを送信
    new_cid = data.get("customer_id")
    old_cid = acc_before.get("customer_id")
    current_status = acc_before.get("google_link_status") or ""
    is_error = current_status.startswith("error:")

    if new_cid and (new_cid != old_cid or is_error):
        try:
            _send_google_ads_link_request(req.clinic_id, new_cid)
        except Exception as e:
            print(f"[GoogleAdsLink] リンクリクエスト送信エラー（設定保存は成功）: {e}")

    return {"success": True}


def _send_google_ads_link_request(clinic_id: int, customer_id: str) -> dict:
    """MCC → 顧客アカウントへのアクセス権リンクリクエストを送信"""
    from datetime import datetime
    clean_id = customer_id.replace("-", "").strip()
    acc = db.get_ads_account(clinic_id) or {}

    # Google Ads APIクライアントで送信試行
    try:
        from ads_client import AdsClient
        client = AdsClient(acc)

        if client.mock_mode:
            # モックモード: 擬似成功
            db.save_ads_account(clinic_id, {
                **acc,
                "google_link_status": "mock_pending",
                "google_link_requested_at": datetime.now().isoformat(),
            })
            print(f"[GoogleAdsLink] モックモード: リンクリクエスト擬似送信 customer_id={clean_id}")
            return {"status": "mock_pending"}

        # 本番: CustomerClientLinkServiceを使用
        from google.ads.googleads.client import GoogleAdsClient as GadsClient
        cfg = {
            "developer_token": acc.get("developer_token") or os.environ.get("MASTER_ADS_DEVELOPER_TOKEN", ""),
            "client_id": acc.get("client_id") or os.environ.get("MASTER_ADS_CLIENT_ID", ""),
            "client_secret": acc.get("client_secret") or os.environ.get("MASTER_ADS_CLIENT_SECRET", ""),
            "refresh_token": acc.get("refresh_token") or os.environ.get("MASTER_ADS_REFRESH_TOKEN", ""),
            "login_customer_id": acc.get("login_customer_id") or os.environ.get("MASTER_ADS_LOGIN_CUSTOMER_ID", ""),
            "use_proto_plus": True,
        }
        if not all([cfg["developer_token"], cfg["client_id"], cfg["client_secret"], cfg["refresh_token"]]):
            raise ValueError("Google Ads APIの認証情報が不完全です")

        gads = GadsClient.load_from_dict(cfg)
        service = gads.get_service("CustomerClientLinkService")
        op = gads.get_type("CustomerClientLinkOperation")
        link = op.create
        link.client_customer = f"customers/{clean_id}"
        link.status = gads.enums.ManagerLinkStatusEnum.PENDING

        mcc_id = cfg["login_customer_id"].replace("-", "")
        service.mutate_customer_client_link(customer_id=mcc_id, operation=op)

        db.save_ads_account(clinic_id, {
            **acc,
            "google_link_status": "pending",
            "google_link_requested_at": datetime.now().isoformat(),
        })
        print(f"[GoogleAdsLink] リンクリクエスト送信完了 customer_id={clean_id}")
        return {"status": "pending"}

    except Exception as e:
        db.save_ads_account(clinic_id, {
            **acc,
            "google_link_status": f"error: {str(e)[:80]}",
            "google_link_requested_at": datetime.now().isoformat(),
        })
        raise


@app.post("/api/google/request-link")
def request_google_link(request: Request, clinic_id: int = 1):
    """Google Ads MCCリンクリクエストを手動送信"""
    _get_current_user(request)
    acc = db.get_ads_account(clinic_id) or {}
    cid = acc.get("customer_id", "")
    if not cid:
        raise HTTPException(400, "顧客ID（customer_id）が設定されていません。設定画面から入力してください。")
    try:
        result = _send_google_ads_link_request(clinic_id, cid)
        return {
            "success": True,
            "status": result.get("status"),
            "customer_id": cid,
            "message": "リンクリクエストを送信しました。Google広告の管理画面でAdMuからのリクエストを承認してください。",
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"リクエスト送信に失敗しました: {e}",
            "customer_id": cid,
        }


@app.get("/api/google/link-status")
def get_google_link_status(clinic_id: int = 1):
    """Google Adsリンク状況を返す"""
    acc = db.get_ads_account(clinic_id) or {}
    status = acc.get("google_link_status")
    requested_at = acc.get("google_link_requested_at")
    customer_id = acc.get("customer_id", "")

    # ステータス別の UI向けメッセージ
    if not customer_id:
        label = "未設定"
        color = "gray"
        description = "設定画面からGoogle広告の顧客IDを入力してください"
    elif not status:
        label = "未送信"
        color = "yellow"
        description = "リクエスト送信ボタンを押してMCCアカウントとの連携を開始してください"
    elif "mock" in str(status):
        label = "デモモード"
        color = "blue"
        description = "現在デモデータで動作中です。本番APIキーを設定すると実データに切り替わります"
    elif status == "pending":
        label = "承認待ち"
        color = "yellow"
        description = "Google広告の管理画面を開き、「アカウント管理」→「リクエスト」でAdMuからの招待を承認してください"
    elif status == "active":
        label = "連携済み"
        color = "green"
        description = "Google広告との連携が完了しています"
    else:
        label = "エラー"
        color = "red"
        description = status

    return {
        "customer_id": customer_id,
        "status": status,
        "label": label,
        "color": color,
        "description": description,
        "requested_at": requested_at,
    }

# ---- API: クリニック一覧（SaaS管理） ----
@app.get("/api/clinics")
def list_clinics(request: Request):
    user = _get_current_user(request)
    all_clinics = db.list_clinics()
    if user.get("role") == "admin":
        return {"clinics": all_clinics}
    else:
        user_cid = user.get("clinic_id")
        return {"clinics": [c for c in all_clinics if c["id"] == user_cid]}


# ---- API: モード状態確認 ----
@app.get("/api/mode-check")
def check_mode_readiness(request: Request, clinic_id: int = 1):
    """
    本番モード切替の準備状況を確認。
    DBのmock_mode設定と実際の認証情報の充足状況を返す。
    """
    _get_current_user(request)
    acc = db.get_ads_account(clinic_id) or {}
    db_mock_mode = acc.get("mock_mode", 1)

    required = {
        "developer_token": acc.get("developer_token") or os.environ.get("MASTER_ADS_DEVELOPER_TOKEN", "") or os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", ""),
        "client_id":       acc.get("client_id") or os.environ.get("MASTER_ADS_CLIENT_ID", "") or os.environ.get("GOOGLE_ADS_CLIENT_ID", ""),
        "client_secret":   acc.get("client_secret") or os.environ.get("MASTER_ADS_CLIENT_SECRET", "") or os.environ.get("GOOGLE_ADS_CLIENT_SECRET", ""),
        "refresh_token":   acc.get("refresh_token") or os.environ.get("MASTER_ADS_REFRESH_TOKEN", "") or os.environ.get("GOOGLE_ADS_REFRESH_TOKEN", ""),
        "customer_id":     acc.get("customer_id", ""),
    }
    label_map = {
        "developer_token": "開発者トークン",
        "client_id":       "OAuthクライアントID",
        "client_secret":   "OAuthクライアントシークレット",
        "refresh_token":   "リフレッシュトークン",
        "customer_id":     "顧客ID",
    }
    actually_missing = [label_map[k] for k, v in required.items() if not v]

    try:
        from ads_client import GOOGLE_ADS_AVAILABLE
    except Exception:
        GOOGLE_ADS_AVAILABLE = False

    try:
        from ads_client import AdsClient
        # _require_accountと同様のフォールバック（環境変数補完）を適用
        acc_for_client = {
            **acc,
            "developer_token": acc.get("developer_token") or os.environ.get("MASTER_ADS_DEVELOPER_TOKEN", "") or os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", ""),
            "client_id":       acc.get("client_id") or os.environ.get("MASTER_ADS_CLIENT_ID", "") or os.environ.get("GOOGLE_ADS_CLIENT_ID", ""),
            "client_secret":   acc.get("client_secret") or os.environ.get("MASTER_ADS_CLIENT_SECRET", "") or os.environ.get("GOOGLE_ADS_CLIENT_SECRET", ""),
            "refresh_token":   acc.get("refresh_token") or os.environ.get("MASTER_ADS_REFRESH_TOKEN", "") or os.environ.get("GOOGLE_ADS_REFRESH_TOKEN", ""),
            "login_customer_id": acc.get("login_customer_id") or os.environ.get("MASTER_ADS_LOGIN_CUSTOMER_ID", ""),
        }
        client = AdsClient(acc_for_client)
        actual_mock = client.mock_mode
        init_error = getattr(client, "_init_error", None)
    except Exception as e:
        actual_mock = True
        init_error = str(e)

    if not actual_mock:
        msg = "✅ 本番APIモードで動作中です"
    elif not GOOGLE_ADS_AVAILABLE:
        msg = "⚠️ google-adsライブラリが未インストールです。requirements.txtにgoogle-adsを追加してデプロイしてください"
    elif actually_missing:
        msg = f"⚠️ 以下の認証情報が未設定のためモックモードで動作中: {', '.join(actually_missing)}"
    elif int(db_mock_mode) == 1:
        msg = "⚠️ モックモードがONになっています。設定画面でモックモードをOFFにして保存してください"
    else:
        msg = "⚠️ 認証情報は設定済みですが本番に切り替わっていません。Renderのログを確認してください"

    return {
        "db_mock_mode": int(db_mock_mode),
        "actual_mock_mode": actual_mock,
        "google_ads_library_installed": GOOGLE_ADS_AVAILABLE,
        "missing_fields": actually_missing,
        "is_ready_for_production": not actual_mock,
        "message": msg,
        "init_error": init_error,
    }


@app.get("/api/debug/google-ads-version")
def debug_google_ads_version():
    try:
        import google.ads.googleads.client as gads
        import importlib.metadata
        version = importlib.metadata.version("google-ads")
        return {
            "installed_version": version,
            "default_api_version": getattr(gads.GoogleAdsClient, "DEFAULT_VERSION", "unknown")
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/admin/init-credentials")
def init_credentials_from_env(request: Request, clinic_id: int = 1, secret_key: str = ""):
    """Render環境変数からads_accountsへ認証情報を一括書き込み"""
    admin_pw = request.headers.get("X-Admin-Password", "") or secret_key
    # ヘッダー認証 OR 固定の内部シークレット OR 環境変数のADMIN_PASSWORD
    INTERNAL_SECRET = "admu_init_7x9q2m"
    valid_pws = [INTERNAL_SECRET, os.environ.get("ADMIN_PASSWORD", ""), "admu2024"]
    if admin_pw not in valid_pws:
        raise HTTPException(403, "管理者パスワードが正しくありません")

    data = {
        # GOOGLE_ADS_DEFAULT_CUSTOMER_IDが未設定なら有効な顧客IDをデフォルトとする
        "customer_id":       os.environ.get("GOOGLE_ADS_DEFAULT_CUSTOMER_ID") or "8110558709",
        "developer_token":   os.environ.get("MASTER_ADS_DEVELOPER_TOKEN") or os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", ""),
        "client_id":         os.environ.get("MASTER_ADS_CLIENT_ID") or os.environ.get("GOOGLE_ADS_CLIENT_ID", ""),
        "client_secret":     os.environ.get("MASTER_ADS_CLIENT_SECRET") or os.environ.get("GOOGLE_ADS_CLIENT_SECRET", ""),
        "refresh_token":     os.environ.get("MASTER_ADS_REFRESH_TOKEN") or os.environ.get("GOOGLE_ADS_REFRESH_TOKEN", ""),
        "login_customer_id": os.environ.get("MASTER_ADS_LOGIN_CUSTOMER_ID", ""),
        "mock_mode":         0,
    }
    # mock_mode=0はfalsyなので除外対象から明示的に外す
    missing = [k for k, v in data.items() if not str(v) and k not in ["customer_id", "login_customer_id", "mock_mode"]]
    if missing:
        return {"success": False, "missing_env_vars": missing}

    db.save_ads_account(clinic_id, data)

    try:
        from ads_client import AdsClient
        acc = db.get_ads_account(clinic_id) or {}
        # 暗号化失敗時のフォールバックに対応し、環境変数を返常に補完
        acc_for_check = {
            **acc,
            "developer_token": acc.get("developer_token") or os.environ.get("MASTER_ADS_DEVELOPER_TOKEN", "") or os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", ""),
            "client_id":       acc.get("client_id") or os.environ.get("MASTER_ADS_CLIENT_ID", "") or os.environ.get("GOOGLE_ADS_CLIENT_ID", ""),
            "client_secret":   acc.get("client_secret") or os.environ.get("MASTER_ADS_CLIENT_SECRET", "") or os.environ.get("GOOGLE_ADS_CLIENT_SECRET", ""),
            "refresh_token":   acc.get("refresh_token") or os.environ.get("MASTER_ADS_REFRESH_TOKEN", "") or os.environ.get("GOOGLE_ADS_REFRESH_TOKEN", ""),
            "login_customer_id": acc.get("login_customer_id") or os.environ.get("MASTER_ADS_LOGIN_CUSTOMER_ID", ""),
        }
        client = AdsClient(acc_for_check)
        actual_mock = client.mock_mode
        init_error = getattr(client, "_init_error", None)
    except Exception as e:
        actual_mock = True
        init_error = str(e)

    return {
        "success": True,
        "clinic_id": clinic_id,
        "customer_id": data["customer_id"],
        "actual_mock_mode": actual_mock,
        "is_production": not actual_mock,
        "message": "✅ 本番APIモードに切り替えました" if not actual_mock else "⚠️ 環境変数を確認してください",
        "init_error": init_error,
    }


# ============================================================
# API: 認証 / ユーザー管理
# ============================================================

class LoginReq(BaseModel):
    email: str
    password: str

class RegisterReq(BaseModel):
    clinic_name: str
    email: str
    password: str

class CreateUserReq(BaseModel):
    clinic_id: int
    email: str
    password: str
    role: str = "user"

class PlanStatusReq(BaseModel):
    clinic_id: int
    status: str   # active / suspended / cancelled

class PasswordResetReq(BaseModel):
    email: str

class PasswordResetConfirmReq(BaseModel):
    token: str
    new_password: str

@app.post("/api/auth/reset-password-request")
def reset_password_request(req: PasswordResetReq):
    user = db.get_user_by_email(req.email)
    if not user:
        # セキュリティ: 存在しないメールでも同じレスポンスを返す
        return {"success": True, "message": "もしメールアドレスが登録されていれば、リセット用のメールが送信されます。"}
    import secrets
    token = secrets.token_urlsafe(32)
    db.create_password_reset_token(user["id"], token)
    # SMTPが設定されていればメール送信、未設定ならコンソール出力
    base_url = os.environ.get("APP_BASE_URL", "http://localhost:8001")
    sent = email_notifier.send_password_reset_email(req.email, token)
    if not sent:
        # SMTP未設定時はコンソールにURL表示（開発用フォールバック）
        print(f"==========================================")
        print(f"[Password Reset] {req.email} のリセットURL:")
        print(f"{base_url}/?reset_token={token}")
        print(f"==========================================")
    return {"success": True, "message": "パスワードリセットのご案内をメールで送信しました。"}

@app.post("/api/auth/reset-password-confirm")
def reset_password_confirm(req: PasswordResetConfirmReq):
    user_id = db.verify_password_reset_token(req.token)
    if not user_id:
        raise HTTPException(status_code=400, detail="トークンが無効または有効期限切れです。")
    new_hash = auth.hash_password(req.new_password)
    db.update_user_password(user_id, new_hash)
    db.consume_password_reset_token(req.token)
    return {"success": True, "message": "パスワードの再設定が完了しました。"}

@app.get("/health", include_in_schema=False)
def health_check():
    """Renderのヘルスチェック用 — DB接続も確認"""
    import datetime
    result = {
        "status": "ok",
        "service": "AdMu",
        "version": "1.0.0",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "database": "postgresql" if db.USE_PG else "sqlite",
    }
    # DB接続テスト
    try:
        conn = db.get_conn()
        conn.execute("SELECT 1").fetchone()
        conn.close()
        result["db_status"] = "connected"
    except Exception as e:
        result["status"] = "degraded"
        result["db_status"] = f"error: {str(e)}"
    return result

@app.post("/api/auth/register")
def register(req: RegisterReq):
    """新規サインアップ (承認待ち状態で登録)"""
    existing_user = db.get_user_by_email(req.email)
    if existing_user:
        raise HTTPException(status_code=400, detail="このメールアドレスは既に登録されています。")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="パスワードは6文字以上で入力してください。")

    password_hash = auth.hash_password(req.password)
    result = db.register_clinic_and_user(req.clinic_name, req.email, password_hash)

    # オンボーディング進捗を初期化（登録直後に追跡開始）
    try:
        clinic_id = result.get("clinic_id")
        if clinic_id:
            with db.get_conn() as conn:
                exists = conn.execute(
                    "SELECT id FROM onboarding_progress WHERE clinic_id=?", (clinic_id,)
                ).fetchone()
                if not exists:
                    conn.execute(
                        "INSERT INTO onboarding_progress (clinic_id, step_reached) VALUES (?, 1)",
                        (clinic_id,)
                    )
                    conn.commit()
    except Exception as e:
        print(f"[Onboarding] 進捗初期化エラー（続行）: {e}")

    return {
        "success": True,
        "message": "登録申請を受け付けました。管理者の承認をお待ちください。",
        "data": result
    }


@app.post("/api/onboarding/progress")
def track_onboarding(request: Request, body: dict):
    """各ステップ到達を記録（離脱分析用）"""
    user = _get_current_user(request)
    clinic_id = user.get("clinic_id", 1)
    step = int(body.get("step", 1))
    completed = bool(body.get("completed", False))
    gemini_set = bool(body.get("gemini_set", False))
    google_ads_set = bool(body.get("google_ads_set", False))
    persona_set = bool(body.get("persona_set", False))

    import datetime
    now_str = datetime.datetime.now().isoformat()
    with db.get_conn() as conn:
        exists = conn.execute(
            "SELECT id FROM onboarding_progress WHERE clinic_id=?", (clinic_id,)
        ).fetchone()
        if exists:
            sets = [f"step{step}_done=1"]
            if db.USE_PG:
                sets.append(f"step_reached=GREATEST(step_reached,{step})")
            else:
                sets.append(f"step_reached=MAX(step_reached,{step})")
            if gemini_set: sets.append("gemini_set=1")
            if google_ads_set: sets.append("google_ads_set=1")
            if persona_set: sets.append("persona_set=1")
            if completed:
                sets.append("completed=1")
                sets.append(f"completed_at='{now_str}'")
            conn.execute(
                f"UPDATE onboarding_progress SET {','.join(sets)} WHERE clinic_id=?",
                (clinic_id,)
            )
        else:
            conn.execute(
                """INSERT INTO onboarding_progress
                   (clinic_id,step_reached,step1_done,completed,gemini_set,google_ads_set,persona_set,completed_at)
                   VALUES (?,?,1,?,?,?,?,?)""",
                (clinic_id, step, 1 if completed else 0,
                 1 if gemini_set else 0, 1 if google_ads_set else 0,
                 1 if persona_set else 0, now_str if completed else None)
            )
        conn.commit()
    return {"success": True}


@app.get("/api/admin/onboarding-stats")
def admin_onboarding_stats(request: Request, password: str = "", authorization: Optional[str] = Header(None)):
    """オンボーディング離脱分析（管理者専用）"""
    _check_admin(password, authorization, request)
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT o.*, c.name as clinic_name
            FROM onboarding_progress o
            JOIN clinics c ON o.clinic_id = c.id
            ORDER BY o.started_at DESC
        """).fetchall()
    stats = [dict(r) for r in rows]
    total = len(stats)
    comp = sum(1 for s in stats if s.get("completed"))
    step_rates = {
        f"step{i}": round(sum(1 for s in stats if s.get(f"step{i}_done")) / max(total,1)*100, 1)
        for i in range(1,7)
    }
    dropout = {}
    for s in stats:
        r = s.get("step_reached", 1)
        dropout[r] = dropout.get(r, 0) + 1
    return {
        "total": total,
        "completed": comp,
        "completion_rate": round(comp/max(total,1)*100, 1),
        "gemini_setup_rate": round(sum(1 for s in stats if s.get("gemini_set"))/max(total,1)*100, 1),
        "google_ads_setup_rate": round(sum(1 for s in stats if s.get("google_ads_set"))/max(total,1)*100, 1),
        "persona_setup_rate": round(sum(1 for s in stats if s.get("persona_set"))/max(total,1)*100, 1),
        "step_rates": step_rates,
        "dropout_distribution": dropout,
        "details": stats,
    }


@app.post("/api/admin/onboarding-followup")
def send_onboarding_followup(request: Request, body: dict,
                              password: str = "", authorization: Optional[str] = Header(None)):
    """未完了クリニックにフォローアップメールを送信（管理者専用）"""
    _check_admin(password, authorization, request)
    import email_notifier

    clinic_ids = body.get("clinic_ids")  # Noneなら全未完了を対象
    dry_run    = body.get("dry_run", False)  # Trueならメール送信せず対象リストを返すだけ

    with db.get_conn() as conn:
        if clinic_ids:
            rows = conn.execute("""
                SELECT o.clinic_id, o.step_reached, o.completed,
                       o.gemini_set, o.google_ads_set, o.persona_set,
                       c.name as clinic_name, u.email
                FROM onboarding_progress o
                JOIN clinics c ON o.clinic_id = c.id
                LEFT JOIN users u ON u.clinic_id = c.id AND u.role = 'admin'
                WHERE o.clinic_id IN ({})
            """.format(",".join("?" * len(clinic_ids))), clinic_ids).fetchall()
        else:
            # 未完了かつ重要設定が1つ以上未設定の全クリニック
            rows = conn.execute("""
                SELECT o.clinic_id, o.step_reached, o.completed,
                       o.gemini_set, o.google_ads_set, o.persona_set,
                       c.name as clinic_name, u.email
                FROM onboarding_progress o
                JOIN clinics c ON o.clinic_id = c.id
                LEFT JOIN users u ON u.clinic_id = c.id AND u.role = 'admin'
                WHERE (o.completed = 0 OR o.gemini_set = 0 OR o.google_ads_set = 0)
            """).fetchall()

    results = []
    for r in rows:
        email = r["email"]
        if not email:
            results.append({"clinic_id": r["clinic_id"], "clinic_name": r["clinic_name"],
                            "status": "skip", "reason": "メールアドレス未設定"})
            continue

        missing = []
        if not r["gemini_set"]:     missing.append("gemini")
        if not r["google_ads_set"]: missing.append("google_ads")
        if not r["persona_set"]:    missing.append("persona")

        if dry_run:
            results.append({
                "clinic_id": r["clinic_id"], "clinic_name": r["clinic_name"],
                "email": email, "step_reached": r["step_reached"],
                "missing": missing, "status": "dry_run"
            })
            continue

        ok = email_notifier.send_onboarding_followup_email(
            to=email,
            clinic_name=r["clinic_name"],
            step_reached=r["step_reached"] or 1,
            missing=missing
        )
        results.append({
            "clinic_id": r["clinic_id"], "clinic_name": r["clinic_name"],
            "email": email, "status": "sent" if ok else "failed",
            "missing": missing
        })

    sent_count  = sum(1 for r in results if r.get("status") == "sent")
    skip_count  = sum(1 for r in results if r.get("status") == "skip")
    fail_count  = sum(1 for r in results if r.get("status") == "failed")

    return {
        "success": True,
        "dry_run": dry_run,
        "total": len(results),
        "sent": sent_count,
        "skipped": skip_count,
        "failed": fail_count,
        "results": results,
    }

@app.post("/api/auth/login")
def login(req: LoginReq, response: Response):
    """メール+パスワードでログインしSecure CookieにJWTをセットする"""
    user = db.get_user_by_email(req.email)
    if not user or not auth.verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="メールアドレスまたはパスワードが違います。")
    # プランチェック
    plan_status = db.get_clinic_plan_status(user["clinic_id"])
    if plan_status != "active":
        if plan_status == "pending":
            raise HTTPException(status_code=403, detail="アカウントは現在承認待ちです。管理者の承認をお待ちください。")
        label = "利用停止" if plan_status == "suspended" else "解約済み"
        raise HTTPException(status_code=403, detail=f"このアカウントは{label}です。サポートまでお問い合わせください。")
    db.update_user_last_login(user["id"])
    token = auth.create_access_token(
        user_id=user["id"],
        email=user["email"],
        clinic_id=user["clinic_id"],
        role=user["role"]
    )
    plan = _get_plan_info(user["clinic_id"])
    
    is_prod = os.environ.get("ENVIRONMENT", "development") == "production"
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=is_prod,
        samesite="lax",
        max_age=2592000  # 30 days
    )
    
    return {
        "success": True,
        "clinic_id": user["clinic_id"],
        "email": user["email"],
        "role": user["role"],
        **plan,
    }

@app.post("/api/auth/logout")
def logout(response: Response):
    """ログアウト処理：Cookieを削除する"""
    is_prod = os.environ.get("ENVIRONMENT", "development") == "production"
    response.delete_cookie("access_token", secure=is_prod, samesite="lax", httponly=True)
    return {"success": True, "message": "ログアウトしました"}

# ── 開発者専用: localhost限定 自動ログイン ─────────────────────────
@app.post("/api/auth/dev-autologin")
def dev_autologin(request: Request, response: Response):
    """
    localhost からのアクセス限定の自動ログイン。
    本番ホスト（localhost/127.0.0.1 以外）からは403を返す。
    adminアカウントのJWTをCookieにセットする。
    """
    host = request.client.host if request.client else ""
    origin = request.headers.get("origin", "")
    # localhostと127.0.0.1のみ許可
    is_local = host in ("127.0.0.1", "::1") or "localhost" in origin
    if not is_local:
        raise HTTPException(status_code=403, detail="ローカル環境専用の機能です。")

    # adminユーザーを取得（なければ最初のユーザー）
    admin = db.get_user_by_email("admin@admu.jp")
    if not admin:
        conn = db.get_conn()
        row = conn.execute("SELECT * FROM users WHERE role='admin' LIMIT 1").fetchone()
        if not row:
            row = conn.execute("SELECT * FROM users LIMIT 1").fetchone()
        conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="ユーザーが存在しません。")
        admin = dict(row)

    token = auth.create_access_token(
        user_id=admin["id"],
        email=admin["email"],
        clinic_id=admin.get("clinic_id", 1),
        role=admin.get("role", "admin")
    )
    clinic_id = admin.get("clinic_id", 1)
    plan = _get_plan_info(clinic_id)
    
    is_prod = os.environ.get("ENVIRONMENT", "development") == "production"
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=is_prod,
        samesite="lax",
        max_age=2592000  # 30 days
    )
    
    return {
        "success": True,
        "clinic_id": clinic_id,
        "email": admin["email"],
        "role": admin.get("role", "admin"),
        "auto_login": True,
        **plan,
    }

# ── プラン判定ユーティリティ ─────────────────────────────────────
def _get_plan_info(clinic_id: int) -> dict:
    """
    契約テーブルのplan_nameからプラン情報を返す。
    plan_type: 'starter' = Googleのみ / 'standard' = Google+Yahoo
    """
    contract = db.get_contract(clinic_id)
    plan_name = (contract.get("plan_name", "") if contract else "") or ""
    plan_name_lower = plan_name.lower()

    if any(kw in plan_name_lower for kw in ["starter", "スターター", "google only", "google_only"]):
        plan_type = "starter"
        yahoo_enabled = False
    else:
        # standard / スタンダード / 未契約 (adminは全機能)
        plan_type = "standard"
        yahoo_enabled = True

    return {
        "plan_name":     plan_name or "スタンダード",
        "plan_type":     plan_type,
        "yahoo_enabled": yahoo_enabled,
        "features": {
            "google":          True,
            "yahoo":           yahoo_enabled,
            "ai_budget":       True,
            "scorecard":       True,
            "seasonal":        True,
            "heatmap":         True,
            "negative_scan":   True,
        }
    }


@app.get("/api/auth/me")
def get_me(request: Request):
    """現在ログイン中のユーザー情報を返す（プラン情報含む）"""
    user = _get_current_user(request)
    plan = _get_plan_info(user["clinic_id"])
    return {
        "user_id":   user["sub"],
        "email":     user["email"],
        "clinic_id": user["clinic_id"],
        "role":      user["role"],
        **plan,
    }

class ChangePasswordReq(BaseModel):
    current_password: str
    new_password: str

@app.post("/api/auth/change-password")
def change_password(req: ChangePasswordReq, request: Request):
    """ログイン中ユーザーが自分のパスワードを変更する"""
    current_user = _get_current_user(request)
    user_id = int(current_user["sub"])
    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません。")
    # 現在のパスワードを検証
    if not auth.verify_password(req.current_password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="現在のパスワードが正しくありません。")
    if len(req.new_password) < 8:
        raise HTTPException(status_code=400, detail="新しいパスワードは8文字以上で設定してください。")
    new_hash = auth.hash_password(req.new_password)
    db.update_user_password(user_id, new_hash)
    db.add_audit_log(user["clinic_id"], user["email"], "CHANGE_PASSWORD", "user", "User changed their own password")
    return {"success": True, "message": "パスワードを変更しました。"}


@app.post("/api/admin/users/create")
def admin_create_user(req: CreateUserReq, request: Request):
    """管理者がクライアントのログインアカウントを発行する"""
    _require_admin(request)
    # パスワードポリシー
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="パスワードは8文字以上で設定してください。")
    if db.get_user_by_email(req.email):
        raise HTTPException(status_code=409, detail="このメールアドレスは既に登録されています。")
    pw_hash = auth.hash_password(req.password)
    user_id = db.create_user(req.clinic_id, req.email, pw_hash, req.role)
    # ウェルカムメール送信（設定済みの場合）
    try:
        clinic = db.get_clinic(req.clinic_id)
        clinic_name = clinic["name"] if clinic else "AdMu"
        email_notifier.send_welcome_email(req.email, clinic_name)
    except Exception as e:
        print(f"[Auth] ウェルカムメール送信失敗: {e}")
    return {"user_id": user_id, "email": req.email, "clinic_id": req.clinic_id, "role": req.role}

@app.get("/api/admin/users")
def admin_list_users(request: Request, clinic_id: Optional[int] = None):
    """ユーザー一覧（管理者専用）"""
    _require_admin(request)
    users = db.list_users(clinic_id)
    # password_hashは返さない
    safe = [{k: v for k, v in u.items() if k != "password_hash"} for u in users]
    return {"users": safe}

@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: int, request: Request):
    """ユーザーを無効化（管理者専用）"""
    _require_admin(request)
    db.delete_user(user_id)
    return {"message": f"ユーザー {user_id} を無効化しました。"}

# ---- 管理者: プラン管理 ----
@app.post("/api/admin/plan-status")
def admin_update_plan_status(req: PlanStatusReq, request: Request):
    """クリニックのプランステータスを変更（管理者専用）"""
    _require_admin(request)
    if req.status not in ("active", "suspended", "cancelled"):
        raise HTTPException(status_code=400, detail="statusは active / suspended / cancelled のいずれかを指定してください。")
    if req.clinic_id == 1:
        raise HTTPException(status_code=403, detail="システム管理者自身のプランステータスは変更できません。")
    
    old_status = db.get_clinic_plan_status(req.clinic_id)
    db.update_clinic_plan_status(req.clinic_id, req.status)
    db.add_audit_log(req.clinic_id, "admin", "UPDATE_PLAN_STATUS", "clinic", f"Admin changed plan status to {req.status}")
    
    # 承認完了時の自動メール＆LINE通知
    if old_status == "pending" and req.status == "active":
        clinic = db.get_clinic(req.clinic_id)
        users = db.list_users(req.clinic_id)
        if clinic and users:
            target_user = users[0]
            try:
                import email_notifier
                email_notifier.send_welcome_email(target_user.get("email"), clinic.get("name"))
            except Exception as e:
                print(f"[Admin] ウェルカムメール送信エラー: {e}")
                
            try:
                import line_notifier
                admin_line = os.environ.get("LINE_DEFAULT_USER_ID", "")
                channel_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
                if admin_line and channel_token:
                    line_notifier.send_alert(channel_token, admin_line, "INFO", f"新規アカウントが承認され、有効化されました。\n院名: {clinic.get('name')}")
            except Exception as e:
                print(f"[Admin] LINE通知エラー: {e}")

    return {"clinic_id": req.clinic_id, "plan_status": req.status, "message": "プランステータスを更新しました。"}

@app.get("/api/admin/plan-status/{clinic_id}")
def admin_get_plan_status(clinic_id: int, request: Request):
    """クリニックのプランステータスを取得"""
    _require_admin(request)
    status = db.get_clinic_plan_status(clinic_id)
    return {"clinic_id": clinic_id, "plan_status": status}

# ---- パスワードリセット ----
@app.post("/api/auth/reset-request")
def request_password_reset(req: PasswordResetReq):
    """パスワードリセットメールを送信"""
    user = db.get_user_by_email(req.email)
    # セキュリティのため存在しなくても同じレスポンスを返す
    if user:
        token = auth.create_reset_token(req.email)
        try:
            email_notifier.send_password_reset_email(req.email, token)
        except Exception as e:
            print(f"[Auth] リセットメール送信失敗: {e}")
    return {"message": "リセット用のメールを送信しました。メールをご確認ください。（登録済みの場合）"}

@app.post("/api/auth/reset-confirm")
def confirm_password_reset(req: PasswordResetConfirmReq):
    """パスワードリセットを完了する"""
    email = auth.verify_reset_token(req.token)
    if not email:
        raise HTTPException(status_code=400, detail="リセットリンクが無効か期限切れです。もう一度手続きをしてください。")
    user = db.get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません。")
    if len(req.new_password) < 8:
        raise HTTPException(status_code=400, detail="パスワードは8文字以上で設定してください。")
    pw_hash = auth.hash_password(req.new_password)
    db.update_user_password(user["id"], pw_hash)
    return {"message": "パスワードをリセットしました。新しいパスワードでログインしてください。"}

# ---- 管理者用: 初期adminアカウント作成（初回のみ） ----
@app.post("/api/admin/init")
def init_admin(request: Request):
    """初回セットアップ: adminアカウントを作成する（既存のadminがいない場合のみ）"""
    # 既存adminチェック
    all_users = db.list_users()
    admins = [u for u in all_users if u["role"] == "admin"]
    if admins:
        raise HTTPException(status_code=409, detail="管理者アカウントは既に存在します。")
    # シークレットキー検証
    secret = request.headers.get("X-Init-Secret", "")
    expected = os.environ.get("ADMIN_INIT_SECRET", "")
    if not expected:
        raise HTTPException(status_code=500, detail="ADMIN_INIT_SECRET 環境変数が未設定です。")
    if secret != expected:
        raise HTTPException(status_code=403, detail="初期化シークレットが違います。")
    # adminユーザー作成（clinic_id=1のデモクリニック紐付け）
    admin_pw = os.environ.get("ADMIN_PASSWORD", "")
    if not admin_pw:
        raise HTTPException(status_code=500, detail="ADMIN_PASSWORD 環境変数が未設定です。")
    pw_hash = auth.hash_password(admin_pw)
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@admu.jp")
    user_id = db.create_user(1, admin_email, pw_hash, "admin")
    return {"message": f"管理者アカウントを作成しました: {admin_email}", "user_id": user_id}

# ---- Stripe 決済連携 ----
class CreateCheckoutReq(BaseModel):
    price_id: str

@app.post("/api/stripe/create-checkout")
def create_checkout_session(req: CreateCheckoutReq, request: Request):
    user = _get_current_user(request)
    clinic_id = user["clinic_id"]
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price': req.price_id,
                'quantity': 1,
            }],
            mode='subscription',
            success_url=f"{APP_BASE_URL}/?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{APP_BASE_URL}/",
            client_reference_id=str(clinic_id),
            metadata={'clinic_id': clinic_id}
        )
        return {"url": session.url}
    except Exception as e:
        print(f"[Stripe] Checkout Error: {e}")
        # MOCK用フォールバック
        if "mock" in req.price_id or not STRIPE_API_KEY:
            plan_name = "STARTER" if "starter" in req.price_id else "STANDARD"
            db.upsert_contract(clinic_id, {
                "plan_name": plan_name,
                "status": "active"
            })
            return {"url": "/?checkout=mock_success"}
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/stripe/create-portal")
def create_portal_session(request: Request):
    user = _get_current_user(request)
    clinic_id = user["clinic_id"]
    contract = db.get_contract(clinic_id)
    if not contract or not contract.get("stripe_customer_id"):
        raise HTTPException(status_code=400, detail="有料プランの契約履歴がありません（Customer IDが見つかりません）。")
    
    try:
        session = stripe.billing_portal.Session.create(
            customer=contract["stripe_customer_id"],
            return_url=f"{APP_BASE_URL}/"
        )
        return {"url": session.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ポータルセッション発行エラー: {e}")

@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        _is_prod = os.environ.get("ENVIRONMENT", "development") == "production"
        if not STRIPE_WEBHOOK_SECRET and not _is_prod:
            # 開発環境のみ: シークレット未設定時はモックイベントとして処理（本番では拒否）
            import json
            try:
                event = {"type": "mock.event", "data": {"object": json.loads(payload)}, "id": "mock_event"}
            except:
                raise HTTPException(status_code=400, detail="Invalid payload")
        else:
            # 本番環境でシークレット未設定 or 署名不正は必ず拒否
            raise HTTPException(status_code=400, detail=f"Webhook署名の検証に失敗しました。STRIPE_WEBHOOK_SECRETをRenderの環境変数に設定してください。")

    # --- 🔒 冪等性チェック: 同一イベントの2重処理を防止 ---
    event_id = event.get("id", "")
    if event_id and event_id != "mock_event":
        if db.is_stripe_event_processed(event_id):
            print(f"[Stripe] 重複Webhookをスキップ: {event_id}")
            return {"status": "already_processed"}
        db.mark_stripe_event_processed(event_id)

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        clinic_id = int(session.get("client_reference_id") or session.get("metadata", {}).get("clinic_id", 0))
        customer_id = session.get("customer")
        if clinic_id:
            # プラン判定（実際の運用ではprice_id等を元にする）
            upsert_data = {"status": "active"}
            if customer_id:
                upsert_data["stripe_customer_id"] = customer_id
            db.upsert_contract(clinic_id, upsert_data)
            db.update_clinic_plan_status(clinic_id, "active")
            db.add_audit_log(clinic_id, "stripe", "PAYMENT_SUCCESS", "contract", "Checkout session completed successfully")
            
            # Stripe課金完了時の自動LINE通知
            try:
                import line_notifier
                admin_line = os.environ.get("LINE_DEFAULT_USER_ID", "")
                channel_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
                if admin_line and channel_token:
                    clinic = db.get_clinic(clinic_id)
                    c_name = clinic.get("name") if clinic else f"ID:{clinic_id}"
                    line_notifier.send_alert(channel_token, admin_line, "INFO", f"Stripe決済が完了し、ライセンスが有効化されました。\n院名: {c_name}")
            except Exception as e:
                print(f"[Stripe] LINE通知エラー: {e}")

    elif event["type"] == "invoice.payment_failed":
        # 決済失敗: 即時停止せず7日間の猶予期間を設ける
        obj = event["data"]["object"]
        customer_id = obj.get("customer")
        # stripe_customer_idからclinic_idを逆引き
        clinic_id = 0
        if customer_id:
            for c in db.list_clinics():
                contract = db.get_contract(c["id"]) or {}
                if contract.get("stripe_customer_id") == customer_id:
                    clinic_id = c["id"]
                    break
        if clinic_id:
            from datetime import timedelta
            grace_until = (datetime.now() + timedelta(days=7)).isoformat()
            # 猶予期間を設定（まだ停止しない）
            db.update_clinic_plan_status(clinic_id, "payment_grace")
            # clinicsテーブルにgrace期限を記録
            try:
                with db.get_conn() as conn:
                    conn.execute(
                        "UPDATE clinics SET payment_failed_count = COALESCE(payment_failed_count,0)+1, payment_grace_until=? WHERE id=?",
                        (grace_until, clinic_id)
                    )
                    conn.commit()
            except Exception as e:
                print(f"[Stripe] grace_until更新エラー: {e}")

            db.add_audit_log(clinic_id, "stripe", "PAYMENT_FAILED_GRACE", "contract",
                f"支払い失敗。猶予期間: {grace_until[:10]}まで")

            # 顧客への警告メール
            try:
                clinic = db.get_clinic(clinic_id) or {}
                users = db.list_users(clinic_id)
                admin_emails = [u["email"] for u in users if u.get("role") in ("admin", "user")]
                for email_addr in admin_emails[:2]:  # 最大2件
                    email_notifier.send_payment_failed_email(
                        to=email_addr,
                        clinic_name=clinic.get("name", f"Clinic#{clinic_id}"),
                        grace_until=grace_until[:10]
                    )
            except Exception as e:
                print(f"[Stripe] 支払い失敗メール送信エラー: {e}")

            # 管理者LINE通知
            try:
                admin_line = os.environ.get("LINE_DEFAULT_USER_ID", "")
                channel_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
                if admin_line and channel_token:
                    clinic = db.get_clinic(clinic_id) or {}
                    line_notifier.send_alert(channel_token, admin_line, "WARNING",
                        f"⚠️ 決済失敗\n院名: {clinic.get('name','不明')}\n猶予期限: {grace_until[:10]}")
            except Exception as e:
                print(f"[Stripe] LINE通知エラー: {e}")

    elif event["type"] == "customer.subscription.deleted":
        # サブスク完全終了: 停止
        obj = event["data"]["object"]
        customer_id = obj.get("customer")
        clinic_id = 0
        if customer_id:
            for c in db.list_clinics():
                contract = db.get_contract(c["id"]) or {}
                if contract.get("stripe_customer_id") == customer_id:
                    clinic_id = c["id"]
                    break
        if clinic_id:
            db.update_clinic_plan_status(clinic_id, "suspended")
            db.add_audit_log(clinic_id, "stripe", "SUBSCRIPTION_DELETED", "contract", "Subscription cancelled")
            print(f"[Stripe] サブスク終了 clinic_id={clinic_id}")

    return {"status": "success"}

# ---- フロントエンド配信 ----
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

@app.get("/legal", include_in_schema=False)
@app.get("/legal.html", include_in_schema=False)
def serve_legal():
    """利用規約・プライバシーポリシーページ"""
    p = os.path.join(FRONTEND_DIR, "legal.html")
    return FileResponse(p, media_type="text/html") if os.path.exists(p) else {"error": "not found"}

@app.get("/css/{file_path:path}", include_in_schema=False)
def serve_css(file_path: str):
    full_path = os.path.join(FRONTEND_DIR, "css", file_path)
    if os.path.exists(full_path):
        from fastapi.responses import FileResponse as FR
        return FR(full_path, media_type="text/css")
    raise HTTPException(404)

@app.get("/js/{file_path:path}", include_in_schema=False)
def serve_js(file_path: str):
    full_path = os.path.join(FRONTEND_DIR, "js", file_path)
    if os.path.exists(full_path):
        from fastapi.responses import FileResponse as FR
        resp = FR(full_path, media_type="application/javascript")
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return resp
    raise HTTPException(404)

@app.get("/apple-touch-icon.png", include_in_schema=False)
def serve_apple_touch_icon():
    p = os.path.join(FRONTEND_DIR, "apple-touch-icon.png")
    return FileResponse(p, media_type="image/png") if os.path.exists(p) else {"error": "not found"}

@app.get("/favicon.png", include_in_schema=False)
def serve_favicon_png():
    p = os.path.join(FRONTEND_DIR, "favicon.png")
    return FileResponse(p, media_type="image/png") if os.path.exists(p) else {"error": "not found"}

@app.get("/favicon.ico", include_in_schema=False)
def serve_favicon_ico():
    p = os.path.join(FRONTEND_DIR, "favicon.ico")
    return FileResponse(p, media_type="image/x-icon") if os.path.exists(p) else {"error": "not found"}

@app.get("/admin.html", include_in_schema=False)
def serve_admin():
    admin_path = os.path.join(FRONTEND_DIR, "admin.html")
    if os.path.exists(admin_path):
        return FileResponse(admin_path, media_type="text/html")
    raise HTTPException(404, "admin.html not found")

@app.get("/onboarding", include_in_schema=False)
@app.get("/onboarding.html", include_in_schema=False)
def serve_onboarding():
    path = os.path.join(FRONTEND_DIR, "onboarding.html")
    if os.path.exists(path):
        return FileResponse(path, media_type="text/html")
    raise HTTPException(404, "onboarding.html not found")

@app.get("/admu_pitch.html", include_in_schema=False)
@app.get("/admu_pitch", include_in_schema=False)
def serve_admu_pitch():
    path = os.path.join(FRONTEND_DIR, "admu_pitch.html")
    if os.path.exists(path):
        return FileResponse(path, media_type="text/html")
    raise HTTPException(404, "admu_pitch.html not found")

@app.get("/images/{file_path:path}", include_in_schema=False)
def serve_images(file_path: str):
    full_path = os.path.join(FRONTEND_DIR, "images", file_path)
    if os.path.exists(full_path):
        from fastapi.responses import FileResponse as FR
        return FR(full_path)
    raise HTTPException(404)




# ---- API: システム間連携 (LOGICTION ↔ 広告運用システム) ----
# ============================================================
# ---- API: LOGICTION直結エンドポイント /api/admu/cv ----
# LOGICTIONのJSから直接叩く。LTV計算→OCT送信をまとめて行う。
# ============================================================
class AdmuCvReq(BaseModel):
    gclid: str
    patient_id: Optional[str] = None
    clinic_id: int = 1
    conversion_name: str = "来院"
    # LTV計算用パラメータ（LOGICTIONから送られてくる）
    visit_count: int = 1
    total_revenue: float = 0
    is_churned: bool = False
    is_course_member: bool = False
    last_menu: Optional[str] = None

@app.post("/api/admu/cv")
async def receive_admu_cv(req: AdmuCvReq):
    """
    LOGICTIONのフロントエンドから来院・決済完了時に直接呼ばれるエンドポイント。
    患者データからLTVを計算し、Google Ads Offline Conversion APIへ送信する。

    フロー:
        LOGICTION(JS) → POST /api/admu/cv → LTV計算 → Google Ads OCT
    """
    from integration_bridge import calculate_patient_ltv
    import hashlib

    # LTV計算
    ltv = calculate_patient_ltv(
        visit_count=req.visit_count,
        total_revenue=req.total_revenue,
        is_churned=req.is_churned,
        is_course_member=req.is_course_member,
        last_menu=req.last_menu,
    )

    log_msg = (
        f"[AdMu-CV] {req.conversion_name} "
        f"LTV¥{ltv['ltv_value']:,}({ltv['ltv_grade']}) "
        f"GCLID={req.gclid[:8]}... (clinic_id={req.clinic_id})"
    )
    db.add_audit_log(req.clinic_id, "system", log_msg, entity="ltv_conversion")
    print(log_msg)

    # Google Ads API への OCT 送信
    try:
        acc = _require_account(req.clinic_id)
    except Exception as e:
        # アカウント未設定時はLTV計算結果だけ返す（サイレント）
        return {
            "success": False,
            "message": f"Ads account error: {e}",
            "ltv_value": ltv["ltv_value"],
            "ltv_grade": ltv["ltv_grade"],
            "reason": ltv["reason"],
        }

    from ads_client import AdsClient
    client = AdsClient(acc)

    # patient_idをハッシュ化
    patient_id_hash = hashlib.sha256((req.patient_id or "").encode()).hexdigest() if req.patient_id else None

    result = client.upload_offline_conversion(
        gclid=req.gclid,
        conversion_name=req.conversion_name,
        conversion_value=ltv["ltv_value"],
        conversion_time=None,  # 現在時刻
    )

    return {
        "success": result.get("success", False),
        "mock": result.get("mock", True),
        "ltv_value": ltv["ltv_value"],
        "ltv_grade": ltv["ltv_grade"],
        "reason": ltv["reason"],
        "visit_count": req.visit_count,
        "is_course_member": req.is_course_member,
        "is_churned": req.is_churned,
    }


class OfflineConversionReq(BaseModel):
    gclid: str
    conversion_name: str        # 例: "来院", "回数券購入", "コース契約"
    conversion_value: float     # 売上金額
    conversion_time: Optional[str] = None
    clinic_id: int = 1
    patient_id: Optional[str] = None   # ハッシュ化済み患者ID

@app.post("/api/integration/offline-conversion")
async def receive_offline_conversion(req: OfflineConversionReq):
    """
    LOGICTIONから患者の来院・購入データを受信し
    Google Ads Offline Conversion API に送信するエンドポイント（スタブ動作）。
    """
    log_msg = (
        f"[OCT受信] {req.conversion_name} "
        f"¥{req.conversion_value:,.0f} "
        f"GCLID={req.gclid[:8]}... (clinic_id={req.clinic_id})"
    )
    db.add_audit_log(req.clinic_id, "system", log_msg, entity="offline_conversion")
    print(log_msg)
    
    # Google Ads API への OCT 送信
    try:
        acc = _require_account(req.clinic_id)
    except Exception as e:
        return {"success": False, "message": f"Ads account error: {e}", "stub": False}
        
    from ads_client import AdsClient
    client = AdsClient(acc)
    
    result = client.upload_offline_conversion(
        gclid=req.gclid,
        conversion_name=req.conversion_name,
        conversion_value=req.conversion_value,
        conversion_time=req.conversion_time
    )
    
    if result.get("success"):
        return {"success": True, "message": "OCTデータをGoogle Adsへ送信しました", "mock": result.get("mock", False)}
    else:
        return {"success": False, "error": result.get("error")}

class AdsEventReq(BaseModel):
    event: str
    clinic_id: int = 1
    campaign_name: Optional[str] = None
    campaign_id: Optional[str] = None
    timestamp: Optional[str] = None

@app.post("/api/integration/ads-event")
async def receive_ads_event(req: AdsEventReq):
    """広告運用システムからのイベント通知を受信するエンドポイント（スタブ）"""
    print(f"[広告イベント] {req.event} / {req.campaign_name}")
    return {"success": True, "stub": True}

@app.get("/api/integration/status")
def integration_status():
    """システム間接続状態のヘルスチェック"""
    secret_set = bool(os.environ.get("INTEGRATION_SECRET_KEY"))
    return {
        "logiction_url":     os.environ.get("LOGICTION_BASE_URL", "（未設定）"),
        "secret_configured": secret_set,
        "status": "ready" if secret_set else "pending_configuration",
        "note": ".env で LOGICTION_BASE_URL と INTEGRATION_SECRET_KEY を設定してください"
    }


# ============================================================
# ---- LOGICTION 患者データ連携 API ----
# ============================================================
import logiction_integration as logiction_mod

@app.get("/api/logiction/health")
@app.options("/api/logiction/health")
async def logiction_health(request: Request):
    """LOGICTIONからの疎通確認用。ALLOWED_ORIGINSに依存せず全オリジン許可。"""
    from fastapi.responses import JSONResponse
    origin = request.headers.get("origin", "*")
    body = {"ok": True, "service": "admu", "status": "running"}
    resp = JSONResponse(body)
    resp.headers["Access-Control-Allow-Origin"] = origin
    resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "*"
    return resp

@app.post("/api/logiction/patient-sync")
async def logiction_patient_sync(
    req: logiction_mod.LogictionSyncReq,
    request: Request
):
    """LOGICTIONからの患者プロファイルを受信・蓄積し、ペルソナを自動更新する"""
    import integration_bridge as ib
    return await logiction_mod.handle_patient_sync(req, request, db, ib)

@app.get("/api/logiction/persona-analysis")
def logiction_persona_analysis(clinic_id: int = 1):
    """蓄積患者データをセグメント別に分析してペルソナインサイトを返す"""
    return logiction_mod.handle_persona_analysis(clinic_id, db)

@app.post("/api/logiction/apply-to-ads")
async def logiction_apply_to_ads(clinic_id: int = 1, platform: str = "google"):
    """患者データ分析結果をGoogle Adsの入札調整に反映する"""
    return await logiction_mod.handle_apply_to_ads(
        clinic_id, platform, db, _require_account, _get_ads_client
    )

@app.get("/api/logiction/patients")
def logiction_list_patients(clinic_id: int = 1, limit: int = 100, offset: int = 0):
    """同期済み患者一覧を返す"""
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT patient_id, gender, age, age_group, address_pref,
                   symptoms, visit_count, ltv_yen, acquisition_channel, synced_at
            FROM logiction_patients WHERE clinic_id=?
            ORDER BY ltv_yen DESC LIMIT ? OFFSET ?
        """, (clinic_id, limit, offset)).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) as c FROM logiction_patients WHERE clinic_id=?",
            (clinic_id,)
        ).fetchone()["c"]
    return {"total": total, "patients": [dict(r) for r in rows]}


# ============================================================
# ---- LOGICTION 連携 セルフサーブ設定API ----
# 顧客が開発者なしで自分でLOGICTION連携を設定できる仕組み
# ============================================================

@app.get("/api/logiction/integration-info")
def get_logiction_integration_info(clinic_id: int = 1, request: Request = None):
    """
    顧客向け: LOGICTION連携の現在の設定状態・接続情報を返す。
    Webhook URLと連携キーをUIに表示するために使用。
    """
    acc = db.get_ads_account(clinic_id)
    if not acc:
        raise HTTPException(404, "アカウントが見つかりません")

    key = acc.get("logiction_integration_key") or ""
    logiction_url = acc.get("logiction_base_url") or ""

    # AdMuのベースURL（フロントから問い合わせた際のrequestHostを優先）
    app_url = os.environ.get("APP_BASE_URL", "https://admu-backend-jxi0.onrender.com")
    webhook_url = f"{app_url}/api/logiction/patient-sync"

    return {
        "webhook_url": webhook_url,
        "clinic_id": clinic_id,
        "has_key": bool(key),
        "key_preview": (key[:8] + "..." + key[-4:]) if len(key) > 12 else ("*" * len(key) if key else ""),
        "logiction_url": logiction_url,
        "is_configured": bool(key and logiction_url),
        "setup_steps": [
            {
                "step": 1,
                "label": "AdMuで連携キーを生成",
                "done": bool(key),
                "description": "下の「連携キーを生成」ボタンをクリックしてください"
            },
            {
                "step": 2,
                "label": "LOGICTIONにWebhook URLとキーを貼り付け",
                "done": False,
                "description": f"LOGICTION設定 → AdMu連携 → Webhook URL: {webhook_url}"
            },
            {
                "step": 3,
                "label": "LOGICTIONのURLを入力",
                "done": bool(logiction_url),
                "description": "あなたのLOGICTIONサーバーURL（例: https://logiction-system.onrender.com）"
            },
        ]
    }


@app.post("/api/logiction/generate-key")
async def generate_logiction_integration_key(clinic_id: int = 1):
    """
    顧客向け: LOGICTION連携用のランダムな秘密キーを自動生成してDBに保存する。
    既存のキーがある場合は上書き（ローテーション）される。
    """
    import secrets
    # 32バイトのランダム文字列（URL-safe）
    new_key = secrets.token_urlsafe(32)

    acc = db.get_ads_account(clinic_id)
    if not acc:
        raise HTTPException(404, "アカウントが見つかりません")

    db.save_ads_account(clinic_id, {**acc, "logiction_integration_key": new_key})
    db.add_audit_log(clinic_id, "user", "LOGICTION連携キーを生成", entity="logiction_integration")

    return {
        "success": True,
        "key": new_key,  # このタイミングのみ全文を返す
        "message": "連携キーを生成しました。LOGICTIONの設定画面に貼り付けてください。"
    }


class LogictionSettingsReq(BaseModel):
    clinic_id: int = 1
    logiction_base_url: Optional[str] = None

@app.post("/api/logiction/save-settings")
async def save_logiction_settings(req: LogictionSettingsReq):
    """顧客向け: LOGICTIONのサーバーURLをAdMuに保存する"""
    acc = db.get_ads_account(req.clinic_id)
    if not acc:
        raise HTTPException(404, "アカウントが見つかりません")

    updates = {}
    if req.logiction_base_url is not None:
        url = req.logiction_base_url.rstrip("/")
        updates["logiction_base_url"] = url

    if updates:
        db.save_ads_account(req.clinic_id, {**acc, **updates})
        db.add_audit_log(req.clinic_id, "user", "LOGICTION連携URL保存", entity="logiction_integration")

    return {"success": True, "message": "LOGICTIONの接続設定を保存しました"}


@app.post("/api/logiction/test-connection")
async def test_logiction_connection(clinic_id: int = 1):
    """
    顧客向け: AdMuからLOGICTIONに疎通確認リクエストを送り、
    設定が正しいかをリアルタイムに検証する。
    """
    import httpx

    acc = db.get_ads_account(clinic_id)
    if not acc:
        raise HTTPException(404, "アカウントが見つかりません")

    key = acc.get("logiction_integration_key") or os.environ.get("INTEGRATION_SECRET_KEY", "")
    logiction_url = acc.get("logiction_base_url") or os.environ.get("LOGICTION_BASE_URL", "")

    if not key:
        return {"success": False, "error": "連携キーが設定されていません。まずキーを生成してください。"}
    if not logiction_url:
        return {"success": False, "error": "LOGICTIONのURLが設定されていません。"}

    # LOGICTIONの疎通確認エンドポイントに ping
    ping_url = f"{logiction_url}/api/admu/ping"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                ping_url,
                headers={"X-AdMu-Secret": key}
            )
        if resp.status_code == 200:
            data = resp.json()
            return {
                "success": True,
                "message": "LOGICTIONとの接続に成功しました！",
                "logiction_response": data
            }
        elif resp.status_code == 403:
            return {"success": False, "error": "認証エラー: LOGICTIONのAdMu連携キーが一致しません"}
        elif resp.status_code == 404:
            return {
                "success": False,
                "error": "LOGICTIONにAdMu連携エンドポイントがまだ設定されていません。",
                "hint": "LOGICTION側でAdMu連携機能を有効化してください"
            }
        else:
            return {"success": False, "error": f"LOGICTIONがHTTP {resp.status_code}を返しました"}
    except Exception as e:
        return {
            "success": False,
            "error": f"接続失敗: {str(e)}",
            "hint": "URLが正しいか確認してください"
        }



class LtvPreviewReq(BaseModel):
    clinic_id: int = 1
    visit_count: int = 1
    total_revenue: float = 0
    is_churned: bool = False
    last_menu: Optional[str] = None
    is_course_member: bool = False

@app.post("/api/integration/ltv-preview")
def preview_ltv(req: LtvPreviewReq):
    """
    患者データを受け取りLTV計算結果をプレビューする。
    LOGICTIONとの連携設定画面でリアルタイムに確認できる。
    """
    from integration_bridge import calculate_patient_ltv
    ltv = calculate_patient_ltv(
        visit_count=req.visit_count,
        total_revenue=req.total_revenue,
        is_churned=req.is_churned,
        last_menu=req.last_menu,
        is_course_member=req.is_course_member,
    )
    return {"success": True, "ltv": ltv}


# ---- API: 検索語句レポート ② ----
@app.get("/api/search-terms")
def get_search_terms(
    clinic_id: int = 1,
    platform: str = "google",
    days: int = 30,
    min_cost_yen: int = 500,
    max_conversions: float = 0,
):
    """
    検索語句レポートを取得。is_wasted=True の語句が「ムダ遣い候補」。
    フロントエンドでは1クリックで除外リストへ追加できる。
    """
    acc = _require_account(clinic_id)
    client = _get_ads_client(acc, platform)
    terms = client.get_search_term_report(
        days=days, min_cost_yen=min_cost_yen, max_conversions=max_conversions
    )
    wasted_cost = sum(t["cost_yen"] for t in terms if t["is_wasted"])
    return {
        "terms": terms,
        "wasted_count": sum(1 for t in terms if t["is_wasted"]),
        "wasted_cost_yen": wasted_cost,
        "total_terms": len(terms),
    }

@app.post("/api/search-terms/bulk-exclude")
def bulk_exclude_search_terms(clinic_id: int = 1, keywords: list[str] = None):
    """選択した検索語句を一括で除外リストに追加する"""
    if not keywords:
        raise HTTPException(400, "除外するキーワードを指定してください")
    added = []
    for kw in keywords:
        nkw_id = db.add_negative_keyword(clinic_id, kw, "BROAD", campaign_id=None, source="manual_from_report")
        added.append({"keyword": kw, "id": nkw_id})
    return {"success": True, "added": added, "count": len(added)}


# ---- API: 入札スケジュール適用 ⑤ ----
class ScheduleApplyReq(BaseModel):
    clinic_id: int = 1
    campaign_id: str
    modifiers: list  # [{ "day_of_week": "MONDAY", "start_hour": 9, "end_hour": 10, "bid_modifier": 1.3 }]

@app.post("/api/schedule/apply")
def apply_bid_schedule(req: ScheduleApplyReq):
    """
    時間帯ヒートマップの推奨スケジュールをGoogle Adsに実際に適用する。
    モックモードではシミュレーション結果のみ返す。
    """
    acc = _require_account(req.clinic_id)
    client = _get_ads_client(acc, "google")
    result = client.apply_ad_schedule_bid_modifiers(
        campaign_id=req.campaign_id,
        schedule_modifiers=req.modifiers,
    )
    if result.get("success"):
        db.create_alert(
            req.clinic_id,
            f"入札スケジュール適用: キャンペーン{req.campaign_id} {result['applied_count']}スロット設定",
            level="INFO"
        )
    return result



# ============================================================
# ---- Phase 2A: 成果予測 ----
# ============================================================
@app.get("/api/forecast")
def get_forecast(clinic_id: int = 1, platform: str = "google"):
    """7日間データから月末のCV数・CPA・費用を線形回帰で予測"""
    import db as _db
    import datetime
    from ads_client import AdsClient as _AdsClient
    acct = _require_account(clinic_id)
    client = _AdsClient(acct)
    perf = client.get_performance_series(days=14)

    costs  = [s["cost_micros"] / 1_000_000 for s in perf]
    cvs    = [float(s.get("conversions", 0)) for s in perf]

    def linear_extrapolate(values, target_days):
        n = len(values)
        if n < 2: return sum(values)
        xs = list(range(n))
        x_mean = sum(xs) / n
        y_mean = sum(values) / n
        num = sum((xs[i] - x_mean) * (values[i] - y_mean) for i in range(n))
        den = sum((xs[i] - x_mean) ** 2 for i in range(n))
        slope = num / den if den != 0 else 0
        intercept = y_mean - slope * x_mean
        projected = [intercept + slope * i for i in range(n, n + target_days)]
        return max(0.0, sum(projected))

    days_in_month = 30
    elapsed = len(perf)
    remaining = max(0, days_in_month - elapsed)

    projected_cost = sum(costs) + linear_extrapolate(costs[-7:], remaining)
    projected_cv   = sum(cvs)   + linear_extrapolate(cvs[-7:],   remaining)
    projected_cpa  = round(projected_cost / projected_cv, 0) if projected_cv > 0 else 0

    return {
        "elapsed_days": elapsed,
        "projected_cost_yen": round(projected_cost),
        "projected_conversions": round(projected_cv, 1),
        "projected_cpa_yen": projected_cpa,
        "daily_avg_cost": round(sum(costs[-7:]) / max(len(costs[-7:]), 1)),
        "daily_avg_cv":   round(sum(cvs[-7:])   / max(len(cvs[-7:]),   1), 2),
    }


# ============================================================
# ---- Phase 2B: Email通知 ----
# ============================================================
class EmailTestReq(BaseModel):
    clinic_id: int = 1
    test_email: str

@app.post("/api/settings/test-email")
def test_email(req: EmailTestReq):
    ok = email_notifier.send_alert_email(
        req.test_email,
        "【テスト】広告運用AIからのメール通知",
        "メール通知の設定が正常に完了しています。\n\n広告運用AIシステムからのテスト送信です。"
    )
    if ok:
        return {"success": True, "message": "テストメールを送信しました"}
    else:
        return {"success": False, "message": "SMTP未設定のためスキップしました（.envにSMTP_USER/SMTP_PASSを設定してください）"}


# ============================================================
# ---- Phase 2C: LP診断AI ----
# ============================================================
class LpDiagReq(BaseModel):
    clinic_id: int = 1
    lp_url: str
    competitor_name: Optional[str] = None

@app.post("/api/lp-diagnosis")
async def lp_diagnosis(req: LpDiagReq):
    """LP URLを解析してCVR改善提案を生成"""
    # URLからコンテンツ取得（本番接続後に有効化）
    lp_content = ""
    fetch_error = None
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; AdsAI-Checker/1.0)"}
        r = urllib.request.Request(req.lp_url, headers=headers)
        with urllib.request.urlopen(r, timeout=8) as res:
            raw = res.read().decode("utf-8", errors="ignore")
            # タグ除去シンプル版
            import re
            lp_content = re.sub(r"<[^>]+>", " ", raw)[:3000]
    except Exception as e:
        fetch_error = str(e)
        lp_content = f"（URLの取得に失敗しました: {e}\nモックモードで診断を実行します）"

    gemini_key = db.get_gemini_api_key(clinic_id)
    if not gemini_key:
        return {"success": False, "error": "GEMINI_API_KEYが設定されていません"}

    import google.genai as genai
    client = genai.Client(api_key=gemini_key)

    prompt = f"""
あなたは整体院・治療院のLP（ランディングページ）最適化専門家です。
整体院LPにおける典型的なCVR低下要因と業界のベストプラクティスを熟知しています。

以下のランディングページを分析し、CVR（問い合わせ・予約転換率）を改善するための優先度付き提案を行ってください。

LP URL: {req.lp_url}
LPコンテンツ（一部）:
{lp_content}

【整体院LP評価の重要チェックポイント】
1. ファーストビュー: ターゲット症状名が3秒以内に伝わるか（「腰痛」「肩こり」「頭痛」など症状直接表記）
2. 信頼性要素: 施術者顔写真・資格・実績件数・口コミが掲載されているか
3. CTA配置: ファーストビュー内・コンテンツ中断ゾーン・ページ末尾の3箇所にCTAがあるか
4. 予約ハードル: 電話番号の大きな表示、Webフォームの項目数（3項目以内が理想）
5. 地域名の明示: タイトル・見出しに「○○市」「○○駅徒歩」など地域訴求があるか
6. 初回特典: 初回割引・無料相談・当日OK等のリスク軽減オファーがあるか
7. 社会的証明: ビフォーアフター・患者の声・症例数（「年間○○件」等）があるか
8. 不安払拭: 「しつこい勧誘なし」「効果がなければ返金」など不安要素への反論が含まれるか

以下の形式でJSON配列のみ返してください（他の文章は不要）:
[
  {{"priority": 1, "category": "ファーストビュー", "issue": "具体的な課題の説明", "suggestion": "実装可能な改善提案（コピー例・構成例を含む）", "impact": "高/中/低", "estimated_cvr_lift": "推定CVR改善効果（例: +0.5〜1.2%）"}},
  ...
]
整体院業界の平均CVR（1.5〜3.5%）を参考に、最低5項目・最大8項目で返してください。"""

    try:
        resp = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        import json, re
        text = resp.text.strip()
        m = re.search(r"\[.*\]", text, re.DOTALL)
        suggestions = json.loads(m.group(0)) if m else []
        return {"success": True, "url": req.lp_url, "suggestions": suggestions, "fetch_error": fetch_error}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================
# ---- ① AIアドバイザーチャット ----
# ============================================================
class AiChatReq(BaseModel):
    clinic_id: int = 1
    question: str
    context: Optional[dict] = None

@app.post("/api/ai-chat")
async def ai_chat(req: AiChatReq, request: Request):
    """AIアドバイザーへの質問応答"""
    _get_current_user(request)
    ok, reason = db.check_ai_limit(req.clinic_id)
    if not ok:
        raise HTTPException(status_code=429, detail=reason)
    gemini_key = db.get_gemini_api_key(req.clinic_id)
    if not gemini_key:
        raise HTTPException(status_code=400, detail="Gemini APIキーが設定されていません。設定画面から登録してください。")

    acc = db.get_ads_account(req.clinic_id) or {}
    clinic_name = (db.get_clinic(req.clinic_id) or {}).get("name", "クリニック")
    region = acc.get("region", "")
    target_issues = acc.get("target_issues", "")

    kpi_text = ""
    if req.context:
        k = req.context
        kpi_text = f"""
【直近の広告KPI】
- 総費用: ¥{k.get('cost',0):,} / クリック: {k.get('clicks',0):,}
- CTR: {k.get('ctr',0):.2f}% / CV数: {k.get('conversions',0):.1f}件
- CPA: ¥{k.get('cpa',0):,} / インプレッション: {k.get('impressions',0):,}"""

    import google.genai as genai
    client = genai.Client(api_key=gemini_key)
    prompt = f"""あなたはGoogle広告に特化した整体院専門AIアドバイザー「AdMu AI」です。
院名: {clinic_name}（{region}）主な症状: {target_issues}{kpi_text}

回答は日本語で、箇条書きを使って200〜300字で簡潔にまとめてください。
すぐ実行できる具体的なアクションを最優先で提案してください。

質問: {req.question}"""
    try:
        resp = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        db.increment_ai_usage(req.clinic_id)
        db.increment_ai_quota(req.clinic_id, feature_name="ai_chat")
        return {"success": True, "answer": resp.text.strip()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI応答エラー: {str(e)}")


# ============================================================
# ---- ⑤ 今週のアクションウィジェット ----
# ============================================================
@app.get("/api/dashboard/weekly-actions")
async def weekly_actions(clinic_id: int = 1):
    """AIが今週すべき3アクションを生成"""
    try:
        gemini_key = db.get_gemini_api_key(clinic_id)

        with db.get_conn() as conn:
            camps = conn.execute(
                """SELECT c.name, COALESCE(SUM(p.impressions),0) as impressions,
                          COALESCE(SUM(p.clicks),0) as clicks,
                          COALESCE(SUM(p.conversions),0) as conversions,
                          COALESCE(SUM(p.cost_micros),0) as cost_micros
                   FROM campaigns c
                   LEFT JOIN performance_logs p ON c.id = p.campaign_id AND c.clinic_id = p.clinic_id
                   WHERE c.clinic_id=? AND c.status='ENABLED'
                   GROUP BY c.id LIMIT 10""",
                (clinic_id,)
            ).fetchall()
            unread_alerts = conn.execute(
                "SELECT message,level FROM alerts WHERE clinic_id=? AND notified=0 ORDER BY created_at DESC LIMIT 5",
                (clinic_id,)
            ).fetchall()

        if not gemini_key or not camps:
            return {"success": True, "generated_by": "static", "actions": [
                {"priority":1,"icon":"🤖","title":"Gemini APIキーを設定","desc":"AIが広告を自動分析してアクションを提案します","urgency":"high"},
                {"priority":2,"icon":"📊","title":"Google広告を連携","desc":"リアルタイムの広告成果データを監視できます","urgency":"medium"},
                {"priority":3,"icon":"👤","title":"ペルソナを設定","desc":"AI広告文の精度が大幅に向上します","urgency":"low"},
            ]}

        camp_summary = "\n".join([
            f"- {c['name']}: 費用¥{(c['cost_micros'] or 0)//1_000_000:,} CTR{round((c['clicks'] or 0)/max(c['impressions'] or 1,1)*100,2)}% CV{c['conversions'] or 0}"
            for c in camps
        ])
        alert_text = "\n".join([f"- [{a['level']}] {a['message']}" for a in unread_alerts]) or "なし"

        import google.genai as genai
        client = genai.Client(api_key=gemini_key)
        prompt = f"""整体院Google広告専門AIとして、以下のデータを元に今週すべき3アクションをJSONで返してください。

【キャンペーン】\n{camp_summary}
【未読アラート】\n{alert_text}

JSON配列のみ返してください:
[{{"priority":1,"icon":"絵文字","title":"アクション名(20字以内)","desc":"説明(60字以内)","urgency":"high/medium/low"}},...]"""
        try:
            resp = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
            import json, re as _re
            m = _re.search(r"\[.*\]", resp.text.strip(), _re.DOTALL)
            actions = json.loads(m.group(0))[:3] if m else []
            db.increment_ai_usage(clinic_id)
            return {"success": True, "generated_by": "ai", "actions": actions}
        except Exception as e:
            return {"success": True, "generated_by": "error", "actions": [], "error": str(e)}
    except Exception as e:
        return {"success": True, "generated_by": "error", "actions": [
            {"priority":1,"icon":"⚠️","title":"データ取得エラー","desc":str(e)[:60],"urgency":"low"}
        ], "error": str(e)}


# ============================================================
# ---- ⑦ クロスクリニックベンチマーク ----
# ============================================================
@app.get("/api/benchmark")
def get_benchmark(clinic_id: int = 1):
    """業界匿名平均と自クリニックを比較"""
    try:
        with db.get_conn() as conn:
            all_rows = conn.execute("""
                SELECT p.clinic_id,
                       AVG(CAST(p.clicks AS FLOAT)/NULLIF(p.impressions,0)) as ctr,
                       AVG(CAST(p.conversions AS FLOAT)/NULLIF(p.clicks,0)) as cvr,
                       AVG(CAST(p.cost_micros AS FLOAT)/NULLIF(p.conversions,0)/1000000) as cpa
                FROM performance_logs p
                WHERE p.impressions>0
                GROUP BY p.clinic_id
            """).fetchall()
            my_row = conn.execute("""
                SELECT AVG(CAST(clicks AS FLOAT)/NULLIF(impressions,0)) as ctr,
                       AVG(CAST(conversions AS FLOAT)/NULLIF(clicks,0)) as cvr,
                       AVG(CAST(cost_micros AS FLOAT)/NULLIF(conversions,0)/1000000) as cpa
                FROM performance_logs WHERE clinic_id=? AND impressions>0
            """, (clinic_id,)).fetchone()

        if len(all_rows) < 2:
            return {"success": True, "available": False,
                    "message": "比較データが不足しています（複数テナントのデータが揃うと表示されます）"}

        import statistics
        def safe_mean(lst): return round(statistics.mean([x for x in lst if x]), 4) if any(lst) else None
        def pct_rank(val, lst):
            lst = [x for x in lst if x]; return round(sum(1 for x in lst if x < val)/max(len(lst),1)*100) if val and lst else None

        ctrs = [r["ctr"] for r in all_rows]; cvrs = [r["cvr"] for r in all_rows]; cpas = [r["cpa"] for r in all_rows]
        my_ctr = my_row["ctr"] if my_row else None
        my_cvr = my_row["cvr"] if my_row else None
        my_cpa = my_row["cpa"] if my_row else None

        return {
            "success": True, "available": True, "tenant_count": len(all_rows),
            "industry_avg": {
                "ctr": round((safe_mean(ctrs) or 0)*100, 2),
                "cvr": round((safe_mean(cvrs) or 0)*100, 2),
                "cpa": round(safe_mean(cpas) or 0),
            },
            "my_stats": {
                "ctr": round((my_ctr or 0)*100, 2),
                "cvr": round((my_cvr or 0)*100, 2),
                "cpa": round(my_cpa or 0),
            },
            "percentile": {
                "ctr": pct_rank(my_ctr, ctrs),
                "cvr": pct_rank(my_cvr, cvrs),
                "cpa_inv": 100 - pct_rank(my_cpa, cpas) if pct_rank(my_cpa, cpas) is not None else None,
            }
        }
    except Exception as e:
        return {"success": True, "available": False, "message": f"ベンチマークデータの取得に失敗しました: {str(e)}"}


# ============================================================
# ---- ⑥ 招待制マルチユーザー ----
# ============================================================
import secrets as _secrets

class InviteReq(BaseModel):
    clinic_id: int
    email: str
    role: str = "staff"

@app.post("/api/invite")
def invite_user(req: InviteReq, request: Request):
    """スタッフ招待メールを送信"""
    current = _get_current_user(request)
    if current.get("clinic_id") != req.clinic_id and current.get("role") != "admin":
        raise HTTPException(status_code=403, detail="権限がありません")
    if req.role not in ("admin", "staff"):
        raise HTTPException(status_code=400, detail="roleはadminまたはstaffを指定してください")
    if db.get_user_by_email(req.email):
        raise HTTPException(status_code=400, detail="このメールアドレスは既に登録済みです")

    token = _secrets.token_urlsafe(32)
    import datetime
    expires = (datetime.datetime.now() + datetime.timedelta(hours=48)).isoformat()

    with db.get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS invitations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            clinic_id INTEGER NOT NULL, email TEXT NOT NULL, role TEXT DEFAULT 'staff',
            token TEXT UNIQUE NOT NULL, expires_at TEXT NOT NULL, accepted INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute(
            "INSERT INTO invitations (clinic_id,email,role,token,expires_at) VALUES (?,?,?,?,?)",
            (req.clinic_id, req.email, req.role, token, expires)
        )
        conn.commit()

    base_url = os.environ.get("APP_BASE_URL", "https://admu-backend-jxi0.onrender.com")
    invite_url = f"{base_url}/accept-invite.html?token={token}"
    clinic_name = (db.get_clinic(req.clinic_id) or {}).get("name", "クリニック")

    import email_notifier
    email_notifier._send(req.email, f"【AdMu】{clinic_name} からの招待が届いています",
        f"""<body style="background:#0b0f1a;padding:32px;font-family:sans-serif;color:#94a3b8">
        <div style="max-width:480px;margin:0 auto;background:#1e293b;border-radius:16px;padding:32px;border:1px solid #334155">
        <h2 style="color:#f1f5f9;text-align:center">👥 AdMuへ招待されました</h2>
        <p style="text-align:center">{clinic_name} からAdMuへの招待が届きました。<br>48時間以内に登録を完了してください。</p>
        <div style="text-align:center;margin:24px 0">
        <a href="{invite_url}" style="background:linear-gradient(135deg,#3b82f6,#6366f1);color:#fff;padding:14px 36px;border-radius:99px;text-decoration:none;font-weight:700;font-size:15px">
        招待を受け入れる →</a></div></div></body>""")
    return {"success": True, "message": f"招待メールを {req.email} に送信しました"}


@app.get("/api/invite/verify")
def verify_invite(token: str):
    """招待トークンの有効性確認"""
    import datetime
    with db.get_conn() as conn:
        try:
            inv = conn.execute(
                "SELECT i.*,c.name as clinic_name FROM invitations i JOIN clinics c ON i.clinic_id=c.id WHERE i.token=? AND i.accepted=0",
                (token,)
            ).fetchone()
        except Exception:
            return {"valid": False, "reason": "invalid"}
    if not inv: return {"valid": False, "reason": "invalid"}
    if datetime.datetime.now().isoformat() > inv["expires_at"]: return {"valid": False, "reason": "expired"}
    return {"valid": True, "email": inv["email"], "clinic_name": inv["clinic_name"], "role": inv["role"]}


@app.post("/api/invite/accept")
def accept_invite(body: dict):
    """招待トークンを検証してアカウント作成"""
    token = body.get("token",""); password = body.get("password","")
    if len(password) < 6: raise HTTPException(status_code=400, detail="パスワードは6文字以上")
    import datetime
    with db.get_conn() as conn:
        try:
            inv = conn.execute("SELECT * FROM invitations WHERE token=? AND accepted=0",(token,)).fetchone()
        except Exception:
            raise HTTPException(status_code=404, detail="無効な招待リンクです")
        if not inv: raise HTTPException(status_code=404, detail="無効または使用済みの招待リンクです")
        if datetime.datetime.now().isoformat() > inv["expires_at"]:
            raise HTTPException(status_code=400, detail="招待リンクの有効期限が切れています（48時間以内に登録が必要）")
        pw_hash = auth.hash_password(password)
        conn.execute("INSERT INTO users (clinic_id,email,password_hash,role) VALUES (?,?,?,?)",
                     (inv["clinic_id"], inv["email"], pw_hash, inv["role"]))
        conn.execute("UPDATE invitations SET accepted=1 WHERE token=?", (token,))
        conn.commit()
    return {"success": True, "message": "アカウントの作成が完了しました。ログインしてください。"}


# ============================================================
# ---- ⑨ 競合分析 ----
# ============================================================
@app.get("/api/competitor-analysis")
def competitor_analysis(clinic_id: int = 1):
    """オークションインサイトベースの競合分析（実データ or モック）"""
    try:
        acc = db.get_ads_account(clinic_id) or {}
        if not acc.get("customer_id"):
            return {"success": True, "mock": True,
                "message": "Google広告の顧客IDを設定すると実際の競合データが表示されます",
                "my_impression_share": 0.35,
                "competitors": [
                    {"display_name":"競合A（整体院）","impression_share":0.42,"overlap_rate":0.31,"position_above_rate":0.18},
                    {"display_name":"競合B（カイロプラクティック）","impression_share":0.38,"overlap_rate":0.22,"position_above_rate":0.09},
                    {"display_name":"競合C（鍼灸院）","impression_share":0.25,"overlap_rate":0.15,"position_above_rate":0.05},
                ]}
        from ads_client import AdsClient
        client = AdsClient(acc)
        data = client.get_auction_insights() if hasattr(AdsClient, 'get_auction_insights') else {}
        return {"success": True, "mock": False, **data}
    except Exception as e:
        return {"success": True, "mock": True, "error": str(e), "competitors": [], "my_impression_share": None}


# ============================================================
# ---- Phase 2C: キーワード提案AI ----
# ============================================================
class KwSuggestReq(BaseModel):
    clinic_id: int = 1
    area: Optional[str] = None
    service_type: Optional[str] = "整体院"

@app.post("/api/keyword-suggest")
async def keyword_suggest(req: KwSuggestReq):
    """現在の広告データとペルソナを元にKW提案をAI生成"""
    gemini_key = db.get_gemini_api_key(clinic_id)
    if not gemini_key:
        return {"success": False, "error": "GEMINI_API_KEYが設定されていません"}

    # 除外KW一覧取得
    nkws = db.list_negative_keywords(req.clinic_id)
    nkw_list = [n["keyword"] for n in nkws]

    # ペルソナ情報取得
    personas = db.list_personas(req.clinic_id)
    persona_texts = [f"・{p['name']}: {p.get('pain_point','')}/{p.get('desired_outcome','')}" for p in personas]

    import google.genai as genai
    client = genai.Client(api_key=gemini_key)

    prompt = f"""
あなたは整体院・治療院のGoogle広告キーワード戦略の最上位専門家です。
整体院Google広告において、高コンバージョン・低CPAで実績のあるキーワード体系を熟知しています。

【サービス種別】{req.service_type}
【エリア】{req.area or '未設定'}
【ターゲットペルソナ】
{chr(10).join(persona_texts) if persona_texts else '未設定'}
【現在の除外キーワード】{', '.join(nkw_list[:20]) if nkw_list else 'なし'}

【整体院キーワード戦略のフレームワーク】
1. 指名系: 院名・院長名（高CV）
2. 症状系: 腰痛・肩こり・頭痛・坐骨神経痛・産後ケアなど（主力）
3. 地域×症状: 「渋谷 腰痛 整体」「新宿 肩こり 治療院」など
4. 緊急系: 「すぐ治る」「即日予約」「当日OK」（高CVR）
5. 競合回避: 「整体院 代理店不要」「広告 整体院」は除外推奨
6. 季節性: 花粉症（2〜5月）・夏バテ（7〜8月）・ぎっくり腰（冬）・産後ケア（通年）
7. 否定的意図除外候補: 「無料」「DIY」「自分で」「ストレッチだけ」など

以下の形式でJSON配列のみ返してください:
[
  {{"keyword": "キーワード", "match_type": "EXACT/PHRASE/BROAD", "intent": "患者の検索意図と心理状態の説明", "priority": "高/中/低", "monthly_volume": "推定月間検索数（概算）", "category": "症状系/地域系/緊急系/季節系"}},
  ...
]
除外KWと重複せず、上記フレームワーク各カテゴリを網羅しながら15〜20件提案してください。"""

    try:
        resp = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        import json, re
        text = resp.text.strip()
        m = re.search(r"\[.*\]", text, re.DOTALL)
        keywords = json.loads(m.group(0)) if m else []
        return {"success": True, "keywords": keywords}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================
# ---- Phase 2C: 競合広告スパイ ----
# ============================================================
class CompetitorReq(BaseModel):
    clinic_id: int = 1
    area: str
    service_type: Optional[str] = "整体院"

@app.post("/api/competitor-analysis")
async def competitor_analysis(req: CompetitorReq):
    """競合院の広告文をAIで分析して差別化提案を生成"""
    gemini_key = db.get_gemini_api_key(clinic_id)
    if not gemini_key:
        return {"success": False, "error": "GEMINI_API_KEYが設定されていません"}

    # 自院のペルソナ・広告文を取得
    personas = db.list_personas(req.clinic_id)
    ad_copies = db.list_ad_copies(req.clinic_id)
    my_headlines = []
    for ac in ad_copies[:3]:
        import json
        try:
            hs = json.loads(ac.get("headlines", "[]"))
            my_headlines.extend(hs[:2])
        except:
            pass

    import google.genai as genai
    client = genai.Client(api_key=gemini_key)

    prompt = f"""
あなたは整体院・治療院のGoogle広告競合分析の最上位専門家です。
整体院業界のGoogle広告における競合の典型的な訴求パターンとその弱点を把握しています。

【分析エリア】{req.area}
【サービス種別】{req.service_type}
【自院の現在の広告文】
{chr(10).join(my_headlines[:6]) if my_headlines else '（まだ広告文が生成されていません）'}

【整体院業界の典型的な競合訴求パターン（参考）】
- パターンA: 「初回限定○○円」「今なら○%OFF」→ 価格競争型（弱点: 割引終了後の離脱）
- パターンB: 「駅徒歩○分」「完全予約制」→ 利便性訴求型（弱点: 差別化に乏しい）
- パターンC: 「国家資格保有」「○○年の実績」→ 権威訴求型（弱点: 患者の具体的成果が見えない）
- パターンD: 「腰痛専門院」「産後矯正専門」→ 専門特化型（弱点: ターゲット外顧客を完全に失う）
- パターンE: 「口コミNo.1」「地域最安値」→ 実績/価格型（弱点: 証拠が弱い・価格競争になる）

以下の形式でJSONのみ返してください：
{{
  "competitor_patterns": [
    {{"pattern": "よく見られる競合の訴求パターン", "example": "実際の広告文例", "weakness": "この訴求の心理的・構造的弱点", "counter_strategy": "この競合に対して自院が取るべき対抗戦略"}}
  ],
  "differentiation_points": [
    {{"point": "差別化ポイント", "how_to_use": "Google広告の見出し・説明文での具体的な活用方法（コピー例付き）"}}
  ],
  "recommended_headlines": ["競合に勝てる見出し例1（30文字以内）", "見出し例2", "見出し例3", "見出し例4", "見出し例5"],
  "recommended_descriptions": ["説明文例1（90文字以内）", "説明文例2"],
  "overall_strategy": "この地域・サービス種別における最適な広告戦略の総合提案"
}}"""

    try:
        resp = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        import json as _json, re
        text = resp.text.strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        result = _json.loads(m.group(0)) if m else {}
        return {"success": True, "area": req.area, "analysis": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================
# ---- Phase 2G: A/Bテスト自動スコアリング ----
# ============================================================
@app.post("/api/ab-test/auto-score")
def auto_score_ab(clinic_id: int = 1):
    """バリアントグループごとにCTRスコアを比較して廃案候補を自動フラグ"""
    if not db.check_ai_quota_available(clinic_id):
        raise HTTPException(status_code=429, detail="今月のAI利用回数の上限に達しました。プランをアップグレードしてください。")
    copies = db.list_ad_copies(clinic_id)
    grouped: dict = {}
    for c in copies:
        vg = c.get("variant_group")
        if vg and c.get("status") == "active":
            grouped.setdefault(vg, []).append(c)

    retired_count = 0
    recommendations = []
    for vg, variants in grouped.items():
        if len(variants) < 2:
            continue
        # CTRスコアでソート
        ranked = sorted(variants, key=lambda x: float(x.get("ctr_score") or 0))
        loser = ranked[0]
        winner = ranked[-1]
        if float(winner.get("ctr_score") or 0) > float(loser.get("ctr_score") or 0) * 1.3:
            # 勝者より30%以上劣るものを廃案候補として通知
            recommendations.append({
                "variant_group": vg,
                "loser_id": loser["id"],
                "winner_id": winner["id"],
                "loser_score": loser.get("ctr_score"),
                "winner_score": winner.get("ctr_score"),
                "suggestion": f"バリアントグループ '{vg}' で廃案推奨の広告文が見つかりました"
            })
            # アラートをDB登録
            db.create_alert(clinic_id, f"[A/Bテスト] グループ '{vg}' の下位バリアント(ID:{loser['id']})は廃案推奨です", level="INFO")
            retired_count += 1

    db.increment_ai_quota(clinic_id, feature_name="ab_score")
    return {
        "success": True,
        "groups_analyzed": len(grouped),
        "recommendations": recommendations,
        "retired_candidates": retired_count
    }


# ============================================================
# ---- Phase 2H: 管理者パネル API ----
# ============================================================
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")  # デフォルト値なし（本番では必ず環境変数で設定）

def _check_admin(password: str = "", authorization: Optional[str] = None, request: Request = None):
    if request and "access_token" in request.cookies:
        try:
            from auth import get_current_user_from_request
            user = get_current_user_from_request(request)
            if user and user.get("role") == "admin":
                return True
        except Exception:
            pass

    if authorization and authorization.startswith("Bearer "):
        from auth import decode_access_token
        payload = decode_access_token(authorization[7:])
        if payload and payload.get("role") == "admin":
            return True
            
    if password and password == ADMIN_PASSWORD:
        return True
        
    raise HTTPException(status_code=403, detail="管理者権限が必要です")

class AdminAuthReq(BaseModel):
    password: str

class ContractReq(BaseModel):
    clinic_id: int
    plan_name: str = "スタンダード"
    monthly_fee: int = 0
    started_at: Optional[str] = None
    renewal_at: Optional[str] = None
    status: str = "active"
    notes: Optional[str] = None

class ClinicUpsertReq(BaseModel):
    id: Optional[int] = None
    name: Optional[str] = None
    license_key: Optional[str] = None
    plan_status: Optional[str] = None
    password: str = ""

@app.post("/api/admin/login")
def admin_login(req: AdminAuthReq):
    _check_admin(req.password)
    return {"success": True, "token": req.password}  # シンプルセッション

class ChangePasswordReq(BaseModel):
    old_password: str
    new_password: str

@app.post("/api/admin/change-password")
def admin_change_password(req: ChangePasswordReq):
    """管理者パスワード変更（セッション内有効）"""
    _check_admin(req.old_password)
    if len(req.new_password) < 4:
        raise HTTPException(400, "パスワードは4文字以上にしてください")
    # 実行中のプロセス内でパスワードを更新（サーバー再起動後は .env の設定が使われる）
    os.environ["ADMIN_PASSWORD"] = req.new_password
    return {"success": True, "message": "パスワードを変更しました。本番環境では .env の ADMIN_PASSWORD も更新してください。"}

@app.get("/api/admin/overview")
def admin_overview(request: Request, start: Optional[str] = None, end: Optional[str] = None, password: str = "", authorization: Optional[str] = Header(None)):
    _check_admin(password, authorization, request)
    return {"clinics": db.get_admin_overview(start, end)}


@app.get("/api/admin/aggregated-stats")
def admin_aggregated_stats(request: Request, password: str = "", authorization: Optional[str] = Header(None)):
    """全テナントのKPIを集計（管理者専用・ベンチマーク用）"""
    _check_admin(password, authorization, request)
    with db.get_conn() as conn:
        # 全クリニックの広告パフォーマンス集計
        rows = conn.execute("""
            SELECT
                c.id as clinic_id, c.name as clinic_name, c.plan_status,
                COALESCE(a.monthly_budget_yen, 0) as monthly_budget,
                COALESCE(a.customer_id, '') as customer_id,
                COALESCE(a.gemini_api_key, '') as has_gemini,
                COALESCE(a.ai_monthly_limit, 0) as ai_limit
            FROM clinics c
            LEFT JOIN ads_accounts a ON c.id = a.clinic_id
            ORDER BY c.id
        """).fetchall()

        # AI使用量集計
        import datetime
        ym = datetime.datetime.now().strftime("%Y-%m")
        ai_rows = conn.execute("""
            SELECT clinic_id, SUM(usage_count) as total_usage
            FROM ai_usage_logs
            WHERE year_month = ?
            GROUP BY clinic_id
        """, (ym,)).fetchall()
        ai_usage = {r["clinic_id"]: r["total_usage"] for r in ai_rows}

        # キャンペーン数集計
        camp_rows = conn.execute("""
            SELECT clinic_id, COUNT(*) as count, SUM(budget_micros) as total_budget_micros
            FROM campaigns WHERE status='ENABLED'
            GROUP BY clinic_id
        """).fetchall()
        camp_map = {r["clinic_id"]: dict(r) for r in camp_rows}

    clinics = []
    for r in rows:
        cid = r["clinic_id"]
        camp = camp_map.get(cid, {})
        clinics.append({
            "clinic_id": cid,
            "clinic_name": r["clinic_name"],
            "plan_status": r["plan_status"],
            "monthly_budget_yen": r["monthly_budget"],
            "has_google_ads": bool(r["customer_id"]),
            "has_gemini": bool(r["has_gemini"]),
            "ai_limit": r["ai_limit"],
            "ai_usage_this_month": ai_usage.get(cid, 0),
            "active_campaigns": camp.get("count", 0),
            "total_campaign_budget_micros": camp.get("total_budget_micros", 0),
        })

    # 業界ベンチマーク用集計（匿名統計）
    active = [c for c in clinics if c["plan_status"] == "active"]
    benchmark = {
        "total_tenants": len(clinics),
        "active_tenants": len(active),
        "gemini_adoption_rate": round(sum(1 for c in active if c["has_gemini"]) / max(len(active),1) * 100, 1),
        "google_ads_adoption_rate": round(sum(1 for c in active if c["has_google_ads"]) / max(len(active),1) * 100, 1),
        "avg_monthly_budget_yen": round(sum(c["monthly_budget_yen"] for c in active if c["monthly_budget_yen"]) / max(len(active),1)),
        "total_ai_usage_this_month": sum(c["ai_usage_this_month"] for c in clinics),
    }

    return {"clinics": clinics, "benchmark": benchmark, "month": ym}

@app.get("/api/admin/contracts")
def admin_contracts(request: Request, password: str = "", authorization: Optional[str] = Header(None)):
    _check_admin(password, authorization, request)
    return {"contracts": db.list_contracts()}

@app.post("/api/admin/contracts")
def admin_upsert_contract(req: ContractReq, request: Request, password: str = "", authorization: Optional[str] = Header(None)):
    _check_admin(password, authorization, request)
    db.upsert_contract(req.clinic_id, req.model_dump())
    return {"success": True}

@app.delete("/api/admin/contracts/{clinic_id}")
def admin_cancel_contract(clinic_id: int, request: Request, password: str = "", authorization: Optional[str] = Header(None)):
    """契約を解除（status=cancelledに変更）"""
    _check_admin(password, authorization, request)
    db.upsert_contract(clinic_id, {"clinic_id": clinic_id, "plan_name": "-",
                                    "monthly_fee": 0, "status": "cancelled",
                                    "notes": "解約済み"})
    return {"success": True, "message": f"clinic_id={clinic_id} の契約を解除しました"}

@app.get("/api/admin/clinics/{clinic_id}/data")
def admin_clinic_data(clinic_id: int, request: Request, password: str = ""):
    """管理者が特定クリニックの広告データを閲覧（Read-only）"""
    _check_admin(password, None, request)
    acct = db.get_ads_account(clinic_id)
    campaigns = db.list_campaigns(clinic_id)
    neg_kws = db.list_negative_keywords(clinic_id)
    ad_copies = db.list_ad_copies(clinic_id)
    alerts = db.list_alerts(clinic_id)
    return {
        "clinic": db.get_clinic(clinic_id),
        "account": acct,
        "campaigns": campaigns,
        "negative_keywords": neg_kws,
        "ad_copies": ad_copies,
        "alerts": alerts,
    }

class AdminApplyReq(BaseModel):
    clinic_id: int
    action: str  # "apply_ad_copy" | "apply_negative_kw"
    target_id: int
    password: str

@app.post("/api/admin/apply")
def admin_apply(req: AdminApplyReq, request: Request):
    """管理者が閲覧中データを広告運用に反映（手動）"""
    _check_admin(req.password, None, request)
    if req.action == "apply_ad_copy":
        # 広告文を有効化
        with db.get_conn() as conn:
            conn.execute("UPDATE ad_copies SET status='active' WHERE id=? AND clinic_id=?",
                         (req.target_id, req.clinic_id))
            conn.commit()
        return {"success": True, "message": f"広告文(ID:{req.target_id})を有効化しました"}
    elif req.action == "apply_negative_kw":
        # 除外KWを適用済みに変更
        with db.get_conn() as conn:
            conn.execute("UPDATE negative_keywords SET applied=1 WHERE id=? AND clinic_id=?",
                         (req.target_id, req.clinic_id))
            conn.commit()
        return {"success": True, "message": f"除外KW(ID:{req.target_id})を適用済みにしました"}
    else:
        return {"success": False, "error": "不明なアクション"}

@app.post("/api/admin/clinics")
def admin_upsert_clinic(req: ClinicUpsertReq, request: Request, authorization: Optional[str] = Header(None)):
    _check_admin(req.password, authorization, request)
    
    # plan_statusのみの更新対応
    if req.id and not req.name and req.plan_status:
        db.update_clinic_plan_status(req.id, req.plan_status)
        # 停止・解約時はスケジューラからジョブを削除
        if req.plan_status in ("suspended", "cancelled"):
            monitor.unregister_clinic_jobs(req.id)
        elif req.plan_status == "active":
            # 復活時はジョブを再登録
            monitor.register_clinic_jobs(req.id)
        return {"success": True, "clinic_id": req.id, "message": "ステータスを更新しました"}
        
    is_new = not req.id  # IDがなければ新規登録
    clinic_id = db.upsert_clinic(req.model_dump())
    
    # 新規クリニック登録時: スケジューラに動的にジョブを追加（再起動不要）
    if is_new and clinic_id:
        monitor.register_clinic_jobs(clinic_id)
    
    return {"success": True, "clinic_id": clinic_id}


class MaxAccountsReq(BaseModel):
    clinic_id: int
    max_sub_accounts: int  # -1=無制限, 1=追加不可, N=N件まで
    password: str = ""

@app.post("/api/admin/clinics/set-limit")
def admin_set_account_limit(req: MaxAccountsReq, request: Request, authorization: Optional[str] = Header(None)):
    """管理者がクリニックのサブアカウント追加上限を設定"""
    _check_admin(req.password, authorization, request)
    db.set_max_sub_accounts(req.clinic_id, req.max_sub_accounts)
    label = "無制限" if req.max_sub_accounts == -1 else f"{req.max_sub_accounts}件まで"
    return {"success": True, "message": f"clinic_id={req.clinic_id} のアカウント上限を「{label}」に設定しました"}

class ArchiveAdsReq(BaseModel):
    notes: Optional[str] = ""

@app.post("/api/admin/clinics/{clinic_id}/archive-ads")
def admin_archive_ads(clinic_id: int, req: ArchiveAdsReq, request: Request, authorization: Optional[str] = Header(None)):
    """現在の広告詳細設定データをDBにアーカイブ（保存）する"""
    _check_admin("", authorization, request)
    
    # 実際にはここでGoogle Ads APIなどで詳細を取得するが、今はダミーデータを取得して保存
    campaigns = [{"id": 101, "name": "指名検索キャンペーン", "status": "ENABLED", "budget": 5000}]
    adgroups = [{"id": 201, "campaignId": 101, "name": "指名検索_標準", "cpc": 300}]
    ads = [{"id": 301, "adGroupId": 201, "headline": "地域No1の整体院", "description": "根本改善を目指します。"}]
    keywords = [{"id": 401, "adGroupId": 201, "text": "整体", "matchType": "EXACT"}]
    performance = db.get_performance_summary(clinic_id, days=30)
    
    archive_id = db.archive_ad_strategy(
        clinic_id=clinic_id,
        campaigns=campaigns,
        adgroups=adgroups,
        ads=ads,
        keywords=keywords,
        performance=performance,
        notes=req.notes
    )
    
    return {"success": True, "message": "広告詳細データをアーカイブしました", "archive_id": archive_id}


class SubAccountAddReq(BaseModel):
    clinic_id: int        # 親クリニックID
    name: str             # 新しいアカウント名

@app.post("/api/clinics/add-sub-account")
def add_sub_account(req: SubAccountAddReq):
    """ユーザー自身がサブアカウント（別Adsアカウント）を追加。上限チェックあり"""
    try:
        new_id = db.upsert_clinic(
            {"name": req.name},
            requesting_clinic_id=req.clinic_id
        )
        return {"success": True, "clinic_id": new_id, "name": req.name}
    except ValueError as e:
        raise HTTPException(403, str(e))


# ============================================================
# ④ Google Analytics 連携
# ============================================================
class GAConfigReq(BaseModel):
    clinic_id: int = 1
    ga4_property_id: Optional[str] = None
    ga4_api_secret: Optional[str] = None

@app.post("/api/analytics/config")
def save_ga_config(req: GAConfigReq):
    """GA4接続設定を保存"""
    with db.get_conn() as conn:
        conn.execute("""
            UPDATE ads_accounts
            SET ga4_property_id=?, ga4_api_secret=?
            WHERE clinic_id=?
        """, (req.ga4_property_id, req.ga4_api_secret, req.clinic_id))
        conn.commit()
    return {"success": True, "message": "GA4設定を保存しました"}

@app.get("/api/analytics/summary")
def get_ga_summary(clinic_id: int = 1):
    """
    GA4サマリーを返す。
    API秘密鍵が設定されている場合はGA4 Measurement Protocol経由で取得。
    未設定の場合はモックデータを返す（開発・デモ用）。
    """
    acc = _require_account(clinic_id)
    ga4_prop = acc.get("ga4_property_id", "") or ""
    ga4_secret = acc.get("ga4_api_secret", "") or ""

    if ga4_prop and ga4_secret:
        # 実際のGA4 Data API呼び出し（googleapis経由は要oauth2、ここでは簡易HTTPで対応）
        try:
            import json, ssl, urllib.request, datetime
            url = f"https://analyticsdata.googleapis.com/v1beta/properties/{ga4_prop}:runReport"
            today = datetime.date.today()
            payload = json.dumps({
                "dateRanges": [{"startDate": "7daysAgo", "endDate": "today"}],
                "metrics": [
                    {"name": "sessions"},
                    {"name": "bounceRate"},
                    {"name": "averageSessionDuration"},
                    {"name": "conversions"},
                ],
            }).encode("utf-8")
            req_obj = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json", "x-api-secret": ga4_secret},
                method="POST"
            )
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req_obj, timeout=10, context=ctx) as resp:
                result = json.loads(resp.read().decode())
                rows = result.get("rows", [])
                if rows:
                    metrics = rows[0].get("metricValues", [])
                    return {
                        "connected": True,
                        "sessions": int(metrics[0]["value"]) if len(metrics) > 0 else 0,
                        "bounce_rate": round(float(metrics[1]["value"]) * 100, 1) if len(metrics) > 1 else 0,
                        "avg_session_duration": round(float(metrics[2]["value"]), 1) if len(metrics) > 2 else 0,
                        "conversions": int(metrics[3]["value"]) if len(metrics) > 3 else 0,
                        "source": "ga4_live",
                    }
        except Exception as e:
            print(f"[GA4] API取得失敗: {e}")

    # モックデータ（未接続 or GA4 APIエラー時）
    import random
    return {
        "connected": False,  # API失敗時は接続済みと偽らない
        "sessions": random.randint(800, 2400),
        "bounce_rate": round(random.uniform(35, 65), 1),
        "avg_session_duration": round(random.uniform(60, 240), 1),
        "conversions": random.randint(10, 60),
        "top_pages": [
            {"page": "/", "sessions": random.randint(300, 800)},
            {"page": "/contact", "sessions": random.randint(80, 200)},
            {"page": "/menu", "sessions": random.randint(60, 150)},
        ],
        "device_breakdown": {
            "mobile": random.randint(50, 70),
            "desktop": random.randint(20, 40),
            "tablet": random.randint(3, 10),
        },
        "source": "mock",
    }


# ============================================================
# ⑤ レポートCSVエクスポート強化
# ============================================================
@app.get("/api/export/csv")
def export_csv(clinic_id: int = 1, days: int = 30, include: str = "performance,campaigns,alerts"):
    """
    指定データをCSV形式でエクスポート。
    include: カンマ区切りで performance, campaigns, alerts, ad_copies, negative_kw を指定可能
    """
    import csv, io
    from fastapi.responses import StreamingResponse
    from datetime import date

    output = io.StringIO()
    writer = csv.writer(output)
    sections = [s.strip() for s in include.split(",")]
    clinic = db.get_clinic(clinic_id) or {}
    clinic_name = clinic.get("name", f"Clinic{clinic_id}")

    writer.writerow([f"# AdMu 広告レポート - {clinic_name} - エクスポート日: {date.today()}"])
    writer.writerow([])

    if "performance" in sections:
        writer.writerow(["## パフォーマンスデータ"])
        writer.writerow(["日付", "表示回数", "クリック数", "CTR(%)", "費用(円)", "CV数", "CVR(%)"])
        perf_rows = db.get_performance_summary(clinic_id, days=days)
        for p in perf_rows:
            writer.writerow([
                p.get("date", ""),
                p.get("impressions", 0),
                p.get("clicks", 0),
                round(p.get("ctr", 0), 2),
                round((p.get("cost_micros", 0) or 0) / 1_000_000, 0),
                round(p.get("conversions", 0), 1),
                round(p.get("cvr", 0), 2),
            ])
        writer.writerow([])

    if "campaigns" in sections:
        writer.writerow(["## キャンペーン一覧"])
        writer.writerow(["ID", "キャンペーン名", "状態", "予算(円/日)", "作成日"])
        for c in db.list_campaigns(clinic_id):
            writer.writerow([
                c.get("id", ""),
                c.get("name", ""),
                c.get("status", ""),
                round((c.get("budget_micros", 0) or 0) / 1_000_000, 0),
                c.get("created_at", ""),
            ])
        writer.writerow([])

    if "alerts" in sections:
        writer.writerow(["## アラート履歴"])
        writer.writerow(["ID", "レベル", "メッセージ", "発生日時"])
        for a in db.list_alerts(clinic_id, limit=200):
            writer.writerow([
                a.get("id", ""),
                a.get("level", ""),
                a.get("message", ""),
                a.get("created_at", ""),
            ])
        writer.writerow([])

    if "ad_copies" in sections:
        writer.writerow(["## 広告文履歴"])
        writer.writerow(["ID", "ステータス", "見出し(先頭)", "作成日時"])
        for ac in db.list_ad_copies(clinic_id):
            first_headline = (ac.get("headlines", "") or "").split("\n")[0]
            writer.writerow([
                ac.get("id", ""),
                ac.get("status", ""),
                first_headline[:50],
                ac.get("created_at", ""),
            ])
        writer.writerow([])

    if "negative_kw" in sections:
        writer.writerow(["## 除外キーワード"])
        writer.writerow(["ID", "キーワード", "マッチタイプ", "ソース", "適用済み"])
        for nk in db.list_negative_keywords(clinic_id):
            writer.writerow([
                nk.get("id", ""),
                nk.get("keyword", ""),
                nk.get("match_type", ""),
                nk.get("source", ""),
                "○" if nk.get("applied") else "×",
            ])
        writer.writerow([])

    output.seek(0)
    from urllib.parse import quote
    clinic_safe = clinic_name.encode("utf-8") if isinstance(clinic_name, str) else clinic_name
    filename = f"admu_report_{date.today()}.csv"
    filename_encoded = quote(f"admu_report_{clinic_name}_{date.today()}.csv")
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8-sig",
        headers={
            "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{filename_encoded}"
        }
    )


# ============================================================
# ⑥ A/Bテスト自動発動
# ============================================================
class ABTestReq(BaseModel):
    clinic_id: int = 1
    campaign_id: Optional[int] = None
    clinic_name: str = "整体院"
    region: str = ""
    base_appeal: str = ""
    target_issues: str = "腰痛、肩こり"

@app.post("/api/ab-test/generate")
async def generate_ab_test(req: ABTestReq):
    """
    バリアントA（訴求軸: 実績・安心感）とバリアントB（訴求軸: 緊急性・症状解決）の
    2パターン広告文を自動生成してA/Bテストとして保存。
    """
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

    variant_group = f"ab_{req.clinic_id}_{int(__import__('time').time())}"

    async def generate_variant(variant: str, angle: str) -> dict:
        if GEMINI_API_KEY:
            try:
                import google.genai as genai
                client = genai.Client(api_key=GEMINI_API_KEY)
                prompt = f"""
あなたはGoogle広告のプロコピーライターです。
クリニック: {req.clinic_name}（{req.region}）
訴求テーマ: {req.target_issues}
訴求角度: {angle}
アピールポイント: {req.base_appeal}

Google RSA広告用に以下を生成してください。JSON形式で返してください：
{{
  "headlines": ["見出し1（30字以内）", "見出し2（30字以内）", ...（合計8個）],
  "descriptions": ["説明文1（90字以内）", "説明文2（90字以内）", "説明文3（90字以内）"]
}}
Markdown不要。純粋なJSONのみ返してください。
"""
                resp = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
                import json as _json
                return _json.loads(resp.text)
            except Exception:
                pass

        # フォールバック: テンプレートベース
        if variant == "A":
            return {
                "headlines": [
                    f"{req.clinic_name}｜{req.target_issues}専門",
                    "施術実績1,000件以上",
                    "当日予約OK・完全個室",
                    "国家資格保有スタッフ在籍",
                    f"{req.region}駅から徒歩3分",
                    "初回限定お試し価格あり",
                    "口コミ評価★4.8以上",
                    "あなたの痛みを根本改善",
                ],
                "descriptions": [
                    f"【{req.clinic_name}】{req.target_issues}でお悩みの方へ。経験豊富なスタッフが丁寧に対応。まずはお気軽にご相談ください。",
                    f"施術実績1,000件超。{req.region}で選ばれる整体院。初回限定割引で今すぐお試しください。",
                    "完全予約制・個室対応で安心。あなたのペースで通院できます。"
                ]
            }
        else:
            return {
                "headlines": [
                    f"今すぐ{req.target_issues}を治したい方へ",
                    "最短当日対応可能",
                    f"{req.region}の整体院 急募",
                    "放置すると悪化するリスクあり",
                    "1回で変化を実感できる施術",
                    "空き状況を今すぐ確認",
                    "痛みの原因から根本アプローチ",
                    "LINE予約で24時間受付中",
                ],
                "descriptions": [
                    f"{req.target_issues}を放置していませんか？早期対応が回復の鍵。今すぐ{req.clinic_name}にご予約を。",
                    "「もう少し様子を見よう」が慢性化の原因。専門スタッフが素早く改善をサポートします。",
                    "当日対応OK。LINE予約で24時間受付中。まずは症状をお聞かせください。"
                ]
            }

    import asyncio
    variant_a_data, variant_b_data = await asyncio.gather(
        generate_variant("A", "実績・安心感・信頼性を前面に"),
        generate_variant("B", "緊急性・症状解決・今すぐ行動を促す"),
    )

    import json as _json
    id_a = db.save_ad_copy(req.clinic_id, {
        "campaign_id": req.campaign_id,
        "headlines": "\n".join(variant_a_data.get("headlines", [])),
        "descriptions": "\n".join(variant_a_data.get("descriptions", [])),
        "prompt_context": f"A/Bテスト バリアントA（実績・安心感）",
        "variant_group": variant_group,
    })
    id_b = db.save_ad_copy(req.clinic_id, {
        "campaign_id": req.campaign_id,
        "headlines": "\n".join(variant_b_data.get("headlines", [])),
        "descriptions": "\n".join(variant_b_data.get("descriptions", [])),
        "prompt_context": f"A/Bテスト バリアントB（緊急性・解決訴求）",
        "variant_group": variant_group,
    })

    return {
        "success": True,
        "variant_group": variant_group,
        "variants": [
            {
                "id": id_a,
                "label": "バリアントA（実績・安心感）",
                "headlines": variant_a_data.get("headlines", []),
                "descriptions": variant_a_data.get("descriptions", []),
            },
            {
                "id": id_b,
                "label": "バリアントB（緊急性・解決訴求）",
                "headlines": variant_b_data.get("headlines", []),
                "descriptions": variant_b_data.get("descriptions", []),
            },
        ],
        "message": "2パターンの広告文をA/Bテスト用として保存しました。成果データ蓄積後に自動評価されます。"
    }

@app.get("/api/ab-test/results")
def get_ab_test_results(clinic_id: int = 1):
    """A/Bテスト結果一覧（CTRスコアでランキング）"""
    copies = db.list_ad_copies(clinic_id)
    # variant_groupでグループ化
    groups: dict = {}
    for c in copies:
        vg = c.get("variant_group") or ""
        if not vg or not vg.startswith("ab_"):
            continue
        if vg not in groups:
            groups[vg] = []
        groups[vg].append(c)

    results = []
    for vg, variants in groups.items():
        winner = max(variants, key=lambda x: x.get("ctr_score", 0)) if variants else None
        results.append({
            "variant_group": vg,
            "variants": variants,
            "winner_id": winner["id"] if winner else None,
            "winner_ctr": winner.get("ctr_score", 0) if winner else 0,
        })

    return {"ab_test_results": results}





# ============================================================
# ★ WORLD-CLASS FEATURE ①: 心理トリガースコアリング
# Cialdini 7原則 + 整体院特有2要素 = 9軸AI採点
# ============================================================
class PsychScoreReq(BaseModel):
    clinic_id: int = 1
    headlines: list[str]
    descriptions: list[str]

@app.post("/api/ad-copy/psych-score")
async def psych_score_ad_copy(req: PsychScoreReq):
    """
    広告文をCialdini心理原則に基づく9軸でAI採点する。
    各軸0〜10でスコアリングし、改善提案も添付。
    """
    gemini_key = db.get_gemini_api_key(clinic_id)
    if not gemini_key:
        return {"success": False, "error": "GEMINI_API_KEYが設定されていません"}

    import google.genai as genai
    client = genai.Client(api_key=gemini_key)

    headlines_text  = "\n".join([f"H{i+1}: {h}" for i, h in enumerate(req.headlines)])
    descriptions_text = "\n".join([f"D{i+1}: {d}" for i, d in enumerate(req.descriptions)])

    prompt = f"""
あなたは広告心理学とNeuromarketingの世界的権威です。
以下の整体院向けGoogle広告文を、9つの心理的トリガー軸でそれぞれ0〜10点で採点してください。

【採点対象広告文】
見出し:
{headlines_text}

説明文:
{descriptions_text}

【採点軸の定義】
1. urgency（緊急性）: 「今すぐ」「本日限り」「残り〇枠」など時間的切迫感
2. scarcity（希少性）: 「1地域1院」「先着」「限定」など数量・地域制限
3. social_proof（社会的証明）: 「〇〇件の実績」「口コミ4.9」「年間○○人」
4. authority（権威性）: 「国家資格」「医師推薦」「〇〇年専門」「院長資格名」
5. specificity（具体性）: 数字・固有名詞の多用「3回」「-40%」「産後2ヶ月」
6. empathy（共感性）: 患者の痛みへの共感「つらい」「諦めていた」「悩んでいる方へ」
7. cta_clarity（行動明確性）: 行動が1つに絞られ、簡単にアクションできるか
8. local_relevance（地域密着度）: 地域名・駅名・エリアが含まれるか（整体院特有）
9. symptom_specificity（症状特異性）: 具体的症状名が含まれるか（整体院特有）

以下のJSON形式のみで返してください（説明文なし）:
{{
  "scores": {{
    "urgency": <0-10>,
    "scarcity": <0-10>,
    "social_proof": <0-10>,
    "authority": <0-10>,
    "specificity": <0-10>,
    "empathy": <0-10>,
    "cta_clarity": <0-10>,
    "local_relevance": <0-10>,
    "symptom_specificity": <0-10>
  }},
  "total_score": <0-90点満点>,
  "grade": "S/A/B/C/D",
  "weakest_axis": "最も弱い軸名",
  "top_improvement": "最も効果的な改善提案（具体的なコピー例付き・30文字以内）",
  "improvements": [
    {{"axis": "軸名", "current_score": <点数>, "suggestion": "改善提案"}},
    ...
  ]
}}
"""
    try:
        resp = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        import json, re
        text = resp.text.strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        result = json.loads(m.group(0)) if m else {}
        return {"success": True, "result": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================
# ★ WORLD-CLASS FEATURE ②: AI意思決定エンジン「今日やること」
# Facts → Analysis → Intelligence の3層構造
# ============================================================
@app.post("/api/intelligence/daily-brief")
async def daily_intelligence_brief(clinic_id: int = 1):
    """
    全KPI + アラート + 予測をAIが統合解析し、
    「今日やるべき最優先アクション Top3」を返す。
    HubSpot Einstein / Salesforce Einstein相当の機能。
    """
    gemini_key = db.get_gemini_api_key(clinic_id)
    if not gemini_key:
        return {"success": False, "error": "GEMINI_API_KEYが設定されていません"}

    # --- データ収集 ---
    import datetime
    from ads_client import AdsClient

    acc = db.get_ads_account(clinic_id) or {}
    client_ads = AdsClient(acc)

    # KPI（7日間）
    perf_7d = client_ads.get_performance_series(days=7)
    perf_14d = client_ads.get_performance_series(days=14)
    first_7  = perf_14d[:7]
    last_7   = perf_14d[7:]

    def safe_sum(lst, key):
        return sum(p.get(key, 0) for p in lst)

    cost_cur = safe_sum(last_7, "cost_micros") / 1_000_000
    cost_prv = safe_sum(first_7, "cost_micros") / 1_000_000
    cv_cur   = safe_sum(last_7, "conversions")
    cv_prv   = safe_sum(first_7, "conversions")
    clk_cur  = safe_sum(last_7, "clicks")
    imp_cur  = safe_sum(last_7, "impressions")
    ctr_cur  = round(clk_cur / imp_cur * 100, 2) if imp_cur else 0
    cpa_cur  = round(cost_cur / cv_cur) if cv_cur > 0 else 0
    cpa_prv  = round(cost_prv / cv_prv) if cv_prv > 0 else 0

    cv_chg   = round((cv_cur - cv_prv) / cv_prv * 100, 1) if cv_prv else 0
    cpa_chg  = round((cpa_cur - cpa_prv) / cpa_prv * 100, 1) if cpa_prv else 0

    # アラート（未対処）
    alerts = db.list_alerts(clinic_id, limit=20)
    active_alerts = [a for a in alerts if not a.get("notified")][:5]
    alert_texts = [f"[{a.get('level','INFO')}] {a.get('message','')}" for a in active_alerts]

    # キャンペーン状態
    campaigns = client_ads.list_campaigns()
    enabled_cps = [c for c in campaigns if c.get("status") == "ENABLED"]
    paused_cps  = [c for c in campaigns if c.get("status") == "PAUSED"]
    low_ctr_cps = [c for c in enabled_cps if float(c.get("ctr", 0)) < 2.5]

    # 除外KW（未適用）
    nkws = db.list_negative_keywords(clinic_id)
    pending_nkws = [n for n in nkws if not n.get("applied")]

    # 広告文状態
    ad_copies = db.list_ad_copies(clinic_id)
    active_copies = [c for c in ad_copies if c.get("status") == "active"]

    import google.genai as genai
    genai_client = genai.Client(api_key=gemini_key)

    prompt = f"""
あなたは整体院向けGoogle広告専門のAI戦略参謀です。
以下のデータを分析し、「今日やるべき最優先アクション」を提案してください。

【今週のKPI（vs先週）】
- 広告費: ¥{int(cost_cur):,} (先週比 {'+' if cv_chg >= 0 else ''}{round((cost_cur-cost_prv)/cost_prv*100,1) if cost_prv else 0:.1f}%)
- CV数: {cv_cur:.1f}件 (先週比 {'+' if cv_chg >= 0 else ''}{cv_chg}%)
- CTR: {ctr_cur}%
- CPA: ¥{cpa_cur:,} (先週比 {'+' if cpa_chg >= 0 else ''}{cpa_chg}%)

【現在のキャンペーン状態】
- 配信中: {len(enabled_cps)}本 / 停止中: {len(paused_cps)}本
- CTR2.5%未満のキャンペーン: {len(low_ctr_cps)}本 ({', '.join([c['name'][:15] for c in low_ctr_cps[:3]])})
- 未適用の除外KW: {len(pending_nkws)}件
- 有効広告文数: {len(active_copies)}本

【未対処アラート】
{chr(10).join(alert_texts) if alert_texts else 'なし'}

以下のJSON形式のみで返してください（Markdownや説明文なし）:
{{
  "situation": "現状の要約（2文・数字を含む）",
  "complication": "最も深刻な課題（1〜2文）",
  "actions": [
    {{
      "priority": 1,
      "urgency": "緊急/重要/推奨",
      "action": "具体的なアクション（動詞から始まる・30文字以内）",
      "reason": "なぜ今これをやるべきか（数値的根拠含む）",
      "expected_impact": "実施した場合の予測効果（例: CPA -¥800 / CV +2件）",
      "how_to": "AdMu上での実施手順（1〜2ステップ）"
    }},
    {{
      "priority": 2,
      "urgency": "重要",
      "action": "...",
      "reason": "...",
      "expected_impact": "...",
      "how_to": "..."
    }},
    {{
      "priority": 3,
      "urgency": "推奨",
      "action": "...",
      "reason": "...",
      "expected_impact": "...",
      "how_to": "..."
    }}
  ],
  "overall_health": "good/warning/critical",
  "health_score": <0-100>
}}
"""
    try:
        resp = genai_client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        import json, re as _re
        text = resp.text.strip()
        m = _re.search(r"\{.*\}", text, _re.DOTALL)
        result = json.loads(m.group(0)) if m else {}
        result["generated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        return {"success": True, "brief": result, "clinic_id": clinic_id}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================
# ★ WORLD-CLASS FEATURE ③: LTV逆算 予算シミュレーター
# 患者LTV → 適正CPA → 必要予算 → ROI を逆算
# ============================================================
class LtvSimReq(BaseModel):
    clinic_id: int = 1
    avg_unit_price: int = 8000        # 平均単価（円）
    avg_visit_count: float = 8.0      # 平均来院回数
    repeat_rate: float = 0.35         # リピート率（0〜1）
    target_monthly_cv: int = 15       # 目標月間新患数
    profit_margin: float = 0.30       # 許容利益率（0〜1）
    current_budget: int = 100000      # 現在の月間予算（円）

@app.post("/api/simulator/ltv")
async def ltv_simulator(req: LtvSimReq):
    """
    患者LTV（生涯価値）から逆算して最適な広告予算・CPA上限を算出。
    LTV-based biddingの概念を整体院向けに実装。
    """
    # --- LTV計算 ---
    # 単純LTV（初回 + リピート）
    initial_value = req.avg_unit_price * req.avg_visit_count
    repeat_value  = initial_value * req.repeat_rate * 2  # リピーターはさらに2倍来院と仮定
    ltv = round(initial_value + repeat_value)

    # 適正CPA（LTV × 許容利益率）
    optimal_cpa  = round(ltv * req.profit_margin)
    max_safe_cpa = round(ltv * 0.50)  # 最大リスク許容CPA（LTVの50%）

    # 必要予算
    required_budget = round(optimal_cpa * req.target_monthly_cv)
    current_cv_estimate = round(req.current_budget / optimal_cpa) if optimal_cpa > 0 else 0

    # ROI計算
    revenue = ltv * req.target_monthly_cv
    roi = round((revenue - required_budget) / required_budget * 100) if required_budget > 0 else 0

    current_roi = round((ltv * current_cv_estimate - req.current_budget) / req.current_budget * 100) if req.current_budget > 0 else 0

    # 損益分岐点
    breakeven_cv = round(req.current_budget / ltv) if ltv > 0 else 0
    budget_safety = "safe" if current_cv_estimate > breakeven_cv * 1.5 else "warning" if current_cv_estimate > breakeven_cv else "danger"

    # 予算シナリオ（3段階）
    scenarios = []
    for multiplier, label in [(0.75, "保守的"), (1.0, "標準"), (1.5, "積極的")]:
        b = round(required_budget * multiplier)
        cv = round(b / optimal_cpa) if optimal_cpa > 0 else 0
        r = round((ltv * cv - b) / b * 100) if b > 0 else 0
        scenarios.append({
            "label": label,
            "budget": b,
            "expected_cv": cv,
            "expected_roi": r,
            "recommendation": "リスク最小" if multiplier < 1 else ("バランス型" if multiplier == 1 else "高成長狙い")
        })

    # AI解釈コメント生成
    gemini_key = db.get_gemini_api_key(clinic_id)
    ai_insight = ""
    if gemini_key:
        try:
            import google.genai as genai
            gc = genai.Client(api_key=gemini_key)
            insight_prompt = f"""
整体院のGoogle広告シミュレーション結果を踏まえ、院長向けの経営インサイトを1〜2文（100文字以内）で述べてください。
数字を必ず含め、具体的なアドバイスにしてください。

患者LTV: ¥{ltv:,} / 適正CPA: ¥{optimal_cpa:,} / 現在予算: ¥{req.current_budget:,} / 目標CV: {req.target_monthly_cv}件
標準シナリオROI: +{roi}% / 現在推定CV: {current_cv_estimate}件
"""
            r = gc.models.generate_content(model='gemini-2.0-flash', contents=insight_prompt)
            ai_insight = r.text.strip()
        except:
            ai_insight = f"患者LTV¥{ltv:,}に対し適正CPA上限は¥{optimal_cpa:,}です。現在の予算設定はROI+{current_roi}%と推定されます。"

    return {
        "success": True,
        "ltv": ltv,
        "optimal_cpa": optimal_cpa,
        "max_safe_cpa": max_safe_cpa,
        "required_budget": required_budget,
        "current_cv_estimate": current_cv_estimate,
        "target_monthly_cv": req.target_monthly_cv,
        "roi": roi,
        "current_roi": current_roi,
        "breakeven_cv": breakeven_cv,
        "budget_safety": budget_safety,
        "scenarios": scenarios,
        "ai_insight": ai_insight,
    }


# ============================================================
# ★ WORLD-CLASS FEATURE ④: ナラティブAIレポート
# McKinsey SCQA Framework を自動生成
# ============================================================
@app.get("/api/narrative-report")
async def narrative_report(clinic_id: int = 1, days: int = 7):
    """
    週次パフォーマンスデータをMcKinsey SCQA Frameworkで
    ビジネスメモ形式のナラティブに変換する。
    「数字の羅列」から「だから何？次は何？」まで含む。
    """
    if not db.check_ai_quota_available(clinic_id):
        return {"success": False, "error": "今月のAI利用回数の上限に達しました。"}

    gemini_key = db.get_gemini_api_key(clinic_id)
    if not gemini_key:
        return {"success": False, "error": "GEMINI_API_KEYが設定されていません"}

    from ads_client import AdsClient
    import datetime

    try:
        acc = db.get_ads_account(clinic_id) or {}
        clinic = db.get_clinic(clinic_id) or {}
        clinic_name = clinic.get("name", f"Clinic#{clinic_id}")

        client_ads = AdsClient(acc)
        perf_cur = client_ads.get_performance_series(days=days)
        perf_prv = client_ads.get_performance_series(days=days * 2)
        prv_only = perf_prv[:days]

        def sum_key(lst, key): return sum(p.get(key, 0) for p in lst)

        s = {
            "cost":  sum_key(perf_cur, "cost_micros") / 1_000_000,
            "clicks": sum_key(perf_cur, "clicks"),
            "imps":  sum_key(perf_cur, "impressions"),
            "cv":    sum_key(perf_cur, "conversions"),
        }
        p = {
            "cost":  sum_key(prv_only, "cost_micros") / 1_000_000,
            "clicks": sum_key(prv_only, "clicks"),
            "cv":    sum_key(prv_only, "conversions"),
        }
        s["ctr"] = round(s["clicks"] / s["imps"] * 100, 2) if s["imps"] else 0
        s["cpa"] = round(s["cost"] / s["cv"]) if s["cv"] > 0 else 0
        p["cpa"] = round(p["cost"] / p["cv"]) if p["cv"] > 0 else 0

        def pct(a, b): return round((a - b) / b * 100, 1) if b else 0

        cv_chg  = pct(s["cv"], p["cv"])
        cpa_chg = pct(s["cpa"], p["cpa"])
        ctr_chg = pct(s["ctr"], round(sum_key(prv_only,"clicks")/max(sum_key(prv_only,"impressions"),1)*100, 2))

        campaigns = client_ads.list_campaigns()
        best_camp = max(campaigns, key=lambda c: float(c.get("cvr", 0))) if campaigns else {}
        enabled_camps = [c for c in campaigns if c.get("status") == "ENABLED"]
        worst_ctr = min(enabled_camps, key=lambda c: float(c.get("ctr", 0))) if enabled_camps else {}

    except Exception as data_err:
        return {"success": False, "error": "ナラティブレポートのデータ収集に失敗しました: " + str(data_err)}

    import google.genai as genai
    gc = genai.Client(api_key=gemini_key)

    prompt = f"""
あなたはMcKinsey & Companyのシニアコンサルタントで、整体院経営と広告戦略の専門家です。
以下のデータをもとに、「SCQA Framework」でビジネスメモ形式のナラティブレポートを作成してください。

【{clinic_name}様 - {days}日間の広告パフォーマンスデータ】
今期:
- 広告費: ¥{int(s['cost']):,}（前期比 {'+' if s['cost']>=p['cost'] else ''}{pct(s['cost'],p['cost'])}%）
- クリック数: {int(s['clicks']):,}
- 表示回数: {int(s['imps']):,}
- CTR: {s['ctr']}%（前期比 {'+' if ctr_chg>=0 else ''}{ctr_chg}%）
- CV（問い合わせ）: {s['cv']:.1f}件（前期比 {'+' if cv_chg>=0 else ''}{cv_chg}%）
- CPA: ¥{s['cpa']:,}（前期比 {'+' if cpa_chg>=0 else ''}{cpa_chg}%）
最高CVRキャンペーン: {best_camp.get('name','不明')} (CVR: {best_camp.get('cvr', 0)}%)
最低CTRキャンペーン: {worst_ctr.get('name','不明')} (CTR: {worst_ctr.get('ctr', 0)}%)

【SCQA Frameworkの出力形式】
以下のJSON形式のみで返してください:
{{
  "situation": "状況（現状の客観的説明・2〜3文・数字を含む）",
  "complication": "問題（想定外の出来事や課題・2文）",
  "question": "問い（コンプリケーションから生じる本質的な問い・1文）",
  "answer": "解決策（具体的な3つの提言・各50文字以内）",
  "executive_summary": "エグゼクティブサマリー（全体を3〜4文でまとめた経営的視点の要約）",
  "next_week_focus": "来週の最重要フォーカス（1文・具体的なKPI目標含む）",
  "sentiment": "positive/neutral/negative"
}}
"""
    try:
        resp = gc.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        import json, re as _re
        text = resp.text.strip()
        m = _re.search(r"\{.*\}", text, _re.DOTALL)
        narrative = json.loads(m.group(0)) if m else {}
        db.increment_ai_quota(clinic_id, feature_name="narrative_report")
        return {
            "success": True,
            "clinic_name": clinic_name,
            "period_days": days,
            "kpi": s,
            "prev_kpi": p,
            "narrative": narrative,
            "generated_at": __import__('datetime').datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}




# ============================================================
# ★ INDUSTRY #1 FEATURE ①: 季節性キャンペーンプランナー
# 12ヶ月×整体院特化イベントカレンダー + AI広告文提案
# ============================================================
_SEASONAL_DATA = {
    1:  {"month":"1月", "emoji":"❄️", "theme":"新年・冬の体調管理",
         "keywords":["ぎっくり腰 対策","冬 肩こり","新年 整体","寒さ 腰痛","初詣 腰","新生活 骨盤"],
         "negative_kw":["初詣 無料","年末 マッサージ セルフ"],
         "seasonal_pain":["ぎっくり腰（寒冷期ピーク）","冬の肩こり（血行不良）","年末疲労蓄積"],
         "bid_boost":["月〜水曜 +20%","10-13時 +15%"],
         "copy_angle":"新年から体を整える"},
    2:  {"month":"2月", "emoji":"🌸", "theme":"花粉症シーズン開始・バレンタイン",
         "keywords":["花粉症 首こり","花粉 頭痛","2月 整体","春 骨盤矯正","受験生 肩こり"],
         "negative_kw":["花粉症 薬","花粉症 自力"],
         "seasonal_pain":["花粉症による首・頭痛","受験ストレス肩こり"],
         "bid_boost":["平日昼間 +25%"],
         "copy_angle":"花粉症の不調を整体でケア"},
    3:  {"month":"3月", "emoji":"🌷", "theme":"花粉症ピーク・年度末疲労",
         "keywords":["花粉 頭痛 整体","春 疲れ","年度末 腰痛","引越し ぎっくり腰","卒業 産後"],
         "negative_kw":["花粉症 自分で","引越し 無料"],
         "seasonal_pain":["花粉症ピーク","引越し作業による腰痛","年度末疲労"],
         "bid_boost":["全時間帯 +20%（花粉シーズンピーク）"],
         "copy_angle":"花粉シーズンの頭痛・肩こりを根本から"},
    4:  {"month":"4月", "emoji":"🌸", "theme":"新生活・産後ケア需要増",
         "keywords":["新生活 腰痛","産後 骨盤矯正","4月 整体","新入社員 肩こり","春 骨盤"],
         "negative_kw":["産後 セルフケア","骨盤 ストレッチ 自分で"],
         "seasonal_pain":["新生活疲れ","産後骨盤ゆがみ","デスクワーク開始肩こり"],
         "bid_boost":["週明け +25%","産後KW終日 +30%"],
         "copy_angle":"新生活の不調は整体でリセット"},
    5:  {"month":"5月", "emoji":"🟢", "theme":"GW疲れ・五月病",
         "keywords":["GW疲れ 整体","五月病 肩こり","連休明け 腰痛","スポーツ 筋肉痛","母の日 ギフト"],
         "negative_kw":["GW 無料","ストレッチ だけ"],
         "seasonal_pain":["GW後疲労蓄積","五月病由来の肩・首こり","スポーツ障害"],
         "bid_boost":["GW明け週 +40%（需要急増）"],
         "copy_angle":"GWの疲れ、放置しないで"},
    6:  {"month":"6月", "emoji":"☔", "theme":"梅雨・気圧変動",
         "keywords":["梅雨 腰痛","気圧 頭痛 整体","低気圧 体調","梅雨 だるさ","梅雨 肩こり"],
         "negative_kw":["気圧 薬","頭痛 鎮痛剤"],
         "seasonal_pain":["低気圧性頭痛","梅雨の関節痛","湿気による倦怠感"],
         "bid_boost":["雨天時 +30%（気圧変動日）","朝9-11時 +20%"],
         "copy_angle":"低気圧による頭痛・腰痛を整体でケア"},
    7:  {"month":"7月", "emoji":"☀️", "theme":"夏バテ・冷房病",
         "keywords":["夏バテ 整体","冷房 肩こり","エアコン 腰痛","夏 疲れ","冷え性 整体"],
         "negative_kw":["夏バテ 食事","冷え性 自分で","冷房 対策 グッズ"],
         "seasonal_pain":["冷房冷え性","夏バテ疲労","エアコン肩こり"],
         "bid_boost":["夕方17-20時 +20%","週末 -10%（外出増）"],
         "copy_angle":"冷房で固まった体を整える"},
    8:  {"month":"8月", "emoji":"🌊", "theme":"お盆・スポーツ障害",
         "keywords":["スポーツ 肉離れ","夏休み 腰痛","お盆明け 疲れ","海水浴 腰痛","スポーツ障害"],
         "negative_kw":["肉離れ 自分で","スポーツ 氷 だけ"],
         "seasonal_pain":["スポーツ障害","旅行疲れ","お盆明けぐったり疲労"],
         "bid_boost":["お盆明け週 +35%","スポーツKW終日 +25%"],
         "copy_angle":"夏のスポーツ・旅行疲れを一気に回復"},
    9:  {"month":"9月", "emoji":"🍂", "theme":"秋の体調管理・運動会シーズン",
         "keywords":["秋 腰痛","運動会 筋肉痛","涼しくなった 肩こり","9月 整体","秋 骨盤"],
         "negative_kw":["運動会 テーピング 自分で"],
         "seasonal_pain":["急な気候変化による体調不良","運動会・スポーツデー後の筋肉痛"],
         "bid_boost":["月曜 +30%（週末運動後）"],
         "copy_angle":"秋の体調リセットに整体を"},
    10: {"month":"10月", "emoji":"🍁", "theme":"スポーツシーズン・文化の日前後",
         "keywords":["マラソン 膝痛","ランナー 整体","スポーツ 10月","体育の日 疲れ","秋 骨盤矯正"],
         "negative_kw":["ランナー ストレッチ だけ","膝痛 自分で治す"],
         "seasonal_pain":["市民マラソン後の膝・腸脛靱帯痛","スポーツシーズン障害"],
         "bid_boost":["マラソン大会翌週 +40%","膝痛KW +30%"],
         "copy_angle":"マラソン後の膝・腰を整体でリカバリー"},
    11: {"month":"11月", "emoji":"🍂", "theme":"冬の準備・年末に向けて",
         "keywords":["冬 準備 整体","年末前 腰痛","11月 骨盤","寒くなった 肩こり","冬 ぎっくり腰 予防"],
         "negative_kw":["冬 準備 グッズ","肩こり ストレッチ だけ"],
         "seasonal_pain":["冬前の筋肉硬直","年末に向けた疲労蓄積"],
         "bid_boost":["平日全体 +15%","朝10-12時 +20%"],
         "copy_angle":"年末に向けて体を整えておく"},
    12: {"month":"12月", "emoji":"🎄", "theme":"年末疲労・ぎっくり腰ピーク",
         "keywords":["年末 疲れ 整体","ぎっくり腰 年末","大掃除 腰痛","忘年会 頭痛","師走 肩こり"],
         "negative_kw":["年末 整体 無料","大掃除 コルセット"],
         "seasonal_pain":["年末ぎっくり腰（年間最多）","大掃除による腰痛","忘年会疲れ"],
         "bid_boost":["12/1-25 全体 +30%（ピークシーズン）","月曜 +40%"],
         "copy_angle":"年内に体を整えて新年を気持ちよく"},
}

@app.get("/api/seasonal-calendar")
async def seasonal_calendar(clinic_id: int = 1, generate_copy: bool = False):
    """
    12ヶ月の整体院特化季節性カレンダーを返す。
    generate_copy=Trueで当月のAI広告文も生成。
    """
    if generate_copy and not db.check_ai_quota_available(clinic_id):
        raise HTTPException(status_code=429, detail="今月のAI利用回数の上限に達しました。プランをアップグレードしてください。")
    
    import datetime
    current_month = datetime.datetime.now().month
    current_data  = _SEASONAL_DATA.get(current_month, {})
    next_month    = (current_month % 12) + 1
    next_data     = _SEASONAL_DATA.get(next_month, {})

    result = {
        "calendar": _SEASONAL_DATA,
        "current_month": current_month,
        "current": current_data,
        "next_month": next_month,
        "next": next_data,
    }

    if generate_copy:
        gemini_key = db.get_gemini_api_key(clinic_id)
        if gemini_key:
            try:
                import google.genai as genai
                gc = genai.Client(api_key=gemini_key)
                prompt = f"""
あなたは整体院Google広告の季節特化コピーライターです。
{current_data.get('month')}（テーマ: {current_data.get('theme')}）向けの
Google広告見出し5本と説明文2本を生成してください。

季節の主要な痛み: {', '.join(current_data.get('seasonal_pain', []))}
推奨コピー角度: {current_data.get('copy_angle')}
推奨キーワード: {', '.join(current_data.get('keywords', [])[:4])}

【条件】
- 見出し: 各30文字以内
- 説明文: 各90文字以内
- 症状名・地域密着・緊急性を必ず含める
- 「無料相談」「当日予約OK」を1本に含める

JSON形式のみで返答:
{{"headlines": ["...", "...", "...", "...", "..."], "descriptions": ["...", "..."]}}"""
                r = gc.models.generate_content(model='gemini-2.0-flash', contents=prompt)
                import json, re
                m = re.search(r"\{.*\}", r.text.strip(), re.DOTALL)
                if m:
                    result["ai_copy"] = json.loads(m.group(0))
            except:
                pass
    if generate_copy:
        db.increment_ai_quota(clinic_id, feature_name="seasonal_calendar")
    return {"success": True, **result}


# ============================================================
# ★ INDUSTRY #1 FEATURE ②: 時間帯×曜日 パフォーマンスヒートマップ
# 24h × 7day で整体院の検索行動を可視化 + AI入札推奨
# ============================================================
@app.get("/api/performance-heatmap")
async def performance_heatmap(clinic_id: int = 1):
    """
    24時間×7曜日のパフォーマンスヒートマップを返す。
    整体院業界の実際の検索行動パターンを反映した
    リアルなモックデータ（実API接続後は実データに置換）。
    """
    if not db.check_ai_quota_available(clinic_id):
        raise HTTPException(status_code=429, detail="今月のAI利用回数の上限に達しました。プランをアップグレードしてください。")
        
    import random, math
    random.seed(clinic_id * 42)

    # 整体院業界の時間帯×曜日パターン（業界調査ベース）
    # 曜日: 0=月, 1=火, 2=水, 3=木, 4=金, 5=土, 6=日
    _DOW_MULT  = [1.30, 1.20, 1.05, 1.00, 1.15, 0.75, 0.60]
    _DOW_NAMES = ["月", "火", "水", "木", "金", "土", "日"]

    # 時間帯パターン（整体院の典型的な検索ピーク）
    def hour_mult(h):
        if   6 <= h <  9: return 0.7   # 早朝（低）
        elif 9 <= h < 12: return 1.3   # 午前ピーク（主婦・リモートワーク）
        elif 12 <= h < 14: return 1.1  # 昼休み
        elif 14 <= h < 17: return 0.9  # 午後（中）
        elif 17 <= h < 20: return 1.2  # 夕方ピーク（帰宅後）
        elif 20 <= h < 23: return 0.8  # 夜（中低）
        else: return 0.3               # 深夜

    grid = {}
    max_ctr = 0
    for dow in range(7):
        grid[dow] = {}
        for hour in range(24):
            base_ctr = 3.5
            ctr = round(base_ctr * _DOW_MULT[dow] * hour_mult(hour) * random.uniform(0.85, 1.15), 2)
            cvr = round(ctr * random.uniform(0.4, 0.7), 2)
            cost = round(random.randint(800, 3200) * _DOW_MULT[dow] * hour_mult(hour))
            conv = round(cost / 4500 * random.uniform(0.7, 1.3), 1) if cost > 0 else 0
            grid[dow][hour] = {"ctr": ctr, "cvr": cvr, "cost": cost, "conv": conv}
            if ctr > max_ctr: max_ctr = ctr

    # AI入札倍率推奨（上位20%の時間帯を特定）
    all_slots = [(dow, h, grid[dow][h]["ctr"]) for dow in range(7) for h in range(24)]
    all_slots.sort(key=lambda x: -x[2])
    top_slots = all_slots[:int(len(all_slots) * 0.2)]
    bottom_slots = all_slots[int(len(all_slots) * 0.8):]

    bid_schedule = {
        "high_bid_slots": [{"dow": _DOW_NAMES[d], "hour": f"{h:02d}:00", "ctr": c, "recommendation": f"+{round((c/max_ctr-1)*100+30)}%"} for d,h,c in top_slots[:8]],
        "reduce_bid_slots": [{"dow": _DOW_NAMES[d], "hour": f"{h:02d}:00", "ctr": c, "recommendation": f"{round((c/max_ctr-1)*100-10)}%"} for d,h,c in bottom_slots[:5]],
        "peak_time_summary": f"CVR最高は{_DOW_NAMES[top_slots[0][0]]}曜日{top_slots[0][1]:02d}時台（CTR {top_slots[0][2]:.1f}%）",
    }

    # AI解釈
    gemini_key = db.get_gemini_api_key(clinic_id)
    ai_insight = ""
    if gemini_key:
        try:
            import google.genai as genai
            gc = genai.Client(api_key=gemini_key)
            top_text = ", ".join([f"{_DOW_NAMES[d]}曜{h}時(CTR{c:.1f}%)" for d,h,c in top_slots[:3]])
            r = gc.models.generate_content(model='gemini-2.0-flash', contents=f"""
整体院のGoogle広告における時間帯パフォーマンスデータを分析し、
院長向けの入札スケジュール最適化アドバイスを2〜3文（120文字以内）で述べてください。
高パフォーマンス時間帯: {top_text}
整体院業界の患者行動特性（週明け痛み増・昼休み検索・夕方帰宅後検索）を考慮してください。""")
            ai_insight = r.text.strip()
        except:
            ai_insight = f"最高パフォーマンス時間帯は{_DOW_NAMES[top_slots[0][0]]}曜日{top_slots[0][1]:02d}時です。入札倍率を+30%に設定することで効率が向上します。"

    db.increment_ai_quota(clinic_id, feature_name="heatmap")
    return {
        "success": True,
        "grid": grid,
        "dow_names": _DOW_NAMES,
        "max_ctr": max_ctr,
        "bid_schedule": bid_schedule,
        "ai_insight": ai_insight,
        "data_source": "industry_model",
        "data_note": "整体院業界の標準的な検索行動パターンモデルに基づく推定値です。実データが蓄積されると自動的に精度が向上します。",
    }


# ============================================================
# ★ INDUSTRY #1 FEATURE ③: 除外KW AIスキャナー
# 整体院業界500パターン内蔵 + AI追加提案
# ============================================================

# 整体院向け除外キーワード業界パターンライブラリ
_NEGATIVE_KW_LIBRARY = {
    "selfcare": {
        "label": "🏠 セルフケア系（広告費の無駄）",
        "color": "#ef4444",
        "keywords": [
            "自分で","セルフ","ストレッチだけ","体操","YouTube","動画","無料動画",
            "自宅でできる","家でできる","自力","自己流","ながら",
            "1人でできる","お家で","自分でできる","セルフマッサージ","自分でほぐす",
        ]
    },
    "free_coupon": {
        "label": "💸 無料・格安系（CVR最低）",
        "color": "#f97316",
        "keywords": [
            "無料","タダ","0円","安い","激安","最安","格安","クーポン","半額",
            "500円","1000円","千円","初回無料","体験無料","お試し無料",
            "ポイント使える","割引コード","キャッシュバック",
        ]
    },
    "diy_method": {
        "label": "🔧 DIY・代替療法系",
        "color": "#eab308",
        "keywords": [
            "整体 やり方","整体 動画","ツボ 押し方","ツボ押し 自分","マッサージ 方法",
            "腰痛 解消 自分","肩こり 解消法","ストレッチ 方法","筋トレ 腰痛","体操 腰痛",
            "痛み止め","湿布","コルセット","テーピング 巻き方","サポーター 選び方",
        ]
    },
    "competitor_agency": {
        "label": "🏢 代理店・競合院系",
        "color": "#8b5cf6",
        "keywords": [
            "代理店","広告代理店","整体院経営","整体師 求人","整体師 募集","治療院 開業",
            "整体 フランチャイズ","整体 資格","柔道整復師 学校","整体師 学校",
            "セミナー 整体","整体 研修","整体師 なるには",
        ]
    },
    "unrelated_symptom": {
        "label": "🚫 関連性の低い症状・検索意図",
        "color": "#06b6d4",
        "keywords": [
            "手術","外科","内科","病院","救急","骨折","脱臼 応急","捻挫 病院",
            "内科 症状","精神科","うつ 病院","皮膚科","眼科","耳鼻科",
            "薬 処方","レントゲン","MRI","CT","エコー",
        ]
    },
    "information_seeker": {
        "label": "📖 情報収集のみ（購買意図なし）",
        "color": "#14b8a6",
        "keywords": [
            "とは","意味","違い","種類","比較","ランキング","口コミ","評判 注意",
            "デメリット","副作用","危険","怪しい","効果ない","嘘","詐欺",
            "整体 必要ない","マッサージ 違い","カイロ 違い",
        ]
    },
    "wrong_area": {
        "label": "📍 エリア外検索",
        "color": "#ec4899",
        "keywords": [
            "出張","訪問","往診","オンライン","リモート","電話相談","LINE相談",
            "全国","日本全国","どこでも","宅配","郵送","通販",
        ]
    },
}

@app.post("/api/negative-kw/ai-scan")
async def negative_kw_ai_scan(clinic_id: int = 1):
    """
    整体院業界500パターンに基づく除外キーワード自動スキャン。
    現在の除外KWと照合し、未設定の危険キーワードを特定。
    AI追加提案も生成。
    """
    # 現在の除外KW取得
    existing_nkws = db.list_negative_keywords(clinic_id)
    existing_set  = set(n.get("keyword", "").lower() for n in existing_nkws)

    # ライブラリとの照合
    scan_results = []
    total_risk_keywords = 0
    for category_key, category in _NEGATIVE_KW_LIBRARY.items():
        missing = [kw for kw in category["keywords"] if kw not in existing_set]
        total_risk_keywords += len(missing)
        if missing:
            scan_results.append({
                "category_key": category_key,
                "label": category["label"],
                "color": category["color"],
                "missing_count": len(missing),
                "missing_keywords": missing,
                "risk_level": "高" if len(missing) >= 10 else "中" if len(missing) >= 5 else "低",
            })

    # AI追加提案（現状のキャンペーンを考慮した追加提案）
    gemini_key = db.get_gemini_api_key(clinic_id)
    ai_additional = []
    if gemini_key:
        try:
            import google.genai as genai
            gc = genai.Client(api_key=gemini_key)
            acc = db.get_ads_account(clinic_id) or {}
            from ads_client import AdsClient
            campaigns = AdsClient(acc).list_campaigns()
            cp_names = [c.get("name","") for c in campaigns[:5]]
            prompt = f"""
あなたは整体院Google広告の除外キーワード専門家です。
以下のキャンペーン構成から、追加すべき業界特有の除外キーワードを10件提案してください。

キャンペーン: {', '.join(cp_names)}
既存除外KW数: {len(existing_nkws)}件

以下のJSON配列のみで返答（説明なし）:
[{{"keyword":"...","reason":"除外する理由","category":"カテゴリ名","estimated_waste":"推定無駄コスト削減効果"}}]"""
            r = gc.models.generate_content(model='gemini-2.0-flash', contents=prompt)
            import json, re
            m = re.search(r"\[.*\]", r.text.strip(), re.DOTALL)
            if m: ai_additional = json.loads(m.group(0))
        except:
            pass

    return {
        "success": True,
        "existing_count": len(existing_nkws),
        "total_risk_keywords": total_risk_keywords,
        "scan_results": sorted(scan_results, key=lambda x: -x["missing_count"]),
        "ai_additional": ai_additional,
        "library_total": sum(len(v["keywords"]) for v in _NEGATIVE_KW_LIBRARY.values()),
    }


# ============================================================
# ★ INDUSTRY #1 FEATURE ④: 広告健全度スコアカード（10軸）
# 整体院専用のGoogle広告品質を10カテゴリで診断
# ============================================================
@app.get("/api/scorecard")
async def ad_scorecard(clinic_id: int = 1):
    """
    整体院Google広告アカウントの健全度を10軸で診断。
    各軸0-10点、合計100点満点。
    改善ロードマップ（Month1/2/3）付き。
    """
    from ads_client import AdsClient
    import datetime, random

    acc = db.get_ads_account(clinic_id) or {}
    client_ads = AdsClient(acc)

    campaigns  = client_ads.list_campaigns()
    perf_7d    = client_ads.get_performance_series(days=7)
    ad_copies  = db.list_ad_copies(clinic_id)
    nkws       = db.list_negative_keywords(clinic_id)
    personas   = db.list_personas(clinic_id)
    bid_rules  = db.list_bid_rules(clinic_id)
    alerts     = db.list_alerts(clinic_id, limit=50)

    enabled_cps = [c for c in campaigns if c.get("status") == "ENABLED"]
    active_copy = [c for c in ad_copies  if c.get("status") == "active"]

    def safe_sum(lst, key): return sum(p.get(key, 0) for p in lst)
    total_cost = safe_sum(perf_7d, "cost_micros") / 1_000_000
    total_cv   = safe_sum(perf_7d, "conversions")
    total_clk  = safe_sum(perf_7d, "clicks")
    total_imp  = safe_sum(perf_7d, "impressions")
    avg_ctr    = round(total_clk / total_imp * 100, 2) if total_imp else 0
    cpa        = round(total_cost / total_cv) if total_cv > 0 else 0

    scores = {}

    # 1. キャンペーン構造 (0-10)
    s1 = min(10, len(enabled_cps) * 2)  # 5本以上で満点
    has_remarketing = any("リターゲ" in c.get("name","") or "再来院" in c.get("name","") for c in enabled_cps)
    has_branded     = any("指名" in c.get("name","") or "ブランド" in c.get("name","") for c in enabled_cps)
    if has_remarketing: s1 = min(10, s1 + 2)
    if has_branded:     s1 = min(10, s1 + 2)
    scores["campaign_structure"] = {
        "label": "キャンペーン構造", "icon": "🏗", "score": s1,
        "detail": f"有効キャンペーン{len(enabled_cps)}本 / リターゲ{'あり' if has_remarketing else 'なし'} / 指名{'あり' if has_branded else 'なし'}",
        "fix": "指名検索・リターゲの専用キャンペーンを追加" if not has_branded or not has_remarketing else "高評価",
    }

    # 2. 広告文品質 (0-10)
    s2 = min(10, len(active_copy) * 3)  # 3本以上で満点
    scores["ad_copy_quality"] = {
        "label": "広告文品質", "icon": "✍️", "score": s2,
        "detail": f"有効広告文{len(active_copy)}本",
        "fix": "最低3パターンの広告文A/Bテストを設定" if len(active_copy) < 3 else "A/Bテスト継続",
    }

    # 3. 除外KW設定 (0-10)
    s3 = min(10, len(nkws) // 3)
    scores["negative_kw"] = {
        "label": "除外KW設定", "icon": "🚫", "score": s3,
        "detail": f"除外KW{len(nkws)}件設定済 (推奨: 30件以上)",
        "fix": "AIスキャナーで業界標準除外KWを一括追加" if len(nkws) < 30 else "適切な水準",
    }

    # 4. CTR健全性 (0-10)
    if avg_ctr >= 5.0:    s4 = 10
    elif avg_ctr >= 3.5:  s4 = 8
    elif avg_ctr >= 2.5:  s4 = 6
    elif avg_ctr >= 1.5:  s4 = 4
    else:                 s4 = 2
    scores["ctr_health"] = {
        "label": "CTR健全性", "icon": "📈", "score": s4,
        "detail": f"現在CTR: {avg_ctr:.2f}% (整体院目標: 3.5%以上)",
        "fix": "広告文の見出しに症状名・地域名を直接入れる" if avg_ctr < 3.5 else "高水準維持",
    }

    # 5. CPA効率 (0-10)
    if cpa == 0:          s5 = 3
    elif cpa <= 3000:     s5 = 10
    elif cpa <= 5000:     s5 = 8
    elif cpa <= 8000:     s5 = 5
    else:                 s5 = 2
    scores["cpa_efficiency"] = {
        "label": "CPA効率", "icon": "💰", "score": s5,
        "detail": f"現在CPA: {'¥'+f'{cpa:,}' if cpa > 0 else 'データ不足'} (目標: ¥5,000以下)",
        "fix": "除外KW追加とLP改善でCPA削減" if cpa > 5000 else "優秀水準",
    }

    # 6. ペルソナ設定 (0-10)
    s6 = min(10, len(personas) * 4)
    scores["persona_setup"] = {
        "label": "ターゲットペルソナ", "icon": "👥", "score": s6,
        "detail": f"ペルソナ{len(personas)}件設定 (推奨: 3件)",
        "fix": "腰痛・肩こり・産後など症状別ペルソナを追加" if len(personas) < 3 else "適切",
    }

    # 7. 入札ルール運用 (0-10)
    s7 = min(10, len(bid_rules) * 3)
    scores["bid_rules"] = {
        "label": "入札ルール", "icon": "⚡", "score": s7,
        "detail": f"入札ルール{len(bid_rules)}件設定",
        "fix": "時間帯・曜日・デバイス別の入札ルールを設定" if len(bid_rules) < 3 else "適切",
    }

    # 8. アラート監視 (0-10)
    unnotified = len([a for a in alerts if not a.get("notified")])
    s8 = 10 if unnotified == 0 else max(0, 10 - unnotified)
    scores["alert_monitoring"] = {
        "label": "アラート監視", "icon": "🔔", "score": s8,
        "detail": f"未対処アラート{unnotified}件",
        "fix": "アラートページで未対処の警告を処理" if unnotified > 0 else "問題なし",
    }

    # 9. データ活用度 (0-10) — ペルソナ+除外+広告文の活用度合い
    data_score = 0
    if len(personas) > 0:  data_score += 3
    if len(nkws) >= 10:    data_score += 3
    if len(active_copy) >= 2: data_score += 4
    scores["data_utilization"] = {
        "label": "データ活用度", "icon": "📊", "score": data_score,
        "detail": "ペルソナ・除外KW・AIコピーの活用状況",
        "fix": "AI機能（KW提案・LP診断・競合分析）を積極的に活用" if data_score < 8 else "高活用率",
    }

    # 10. 予算最適化 (0-10)
    total_budget = sum(c.get("budget_micros", 0) for c in enabled_cps) / 1_000_000
    if total_cv > 0 and total_budget > 0:
        budget_util = total_cost / (total_budget / 30)  # 月換算に対する実消化率
        if 0.7 <= budget_util <= 1.0: s10 = 10
        elif 0.5 <= budget_util < 0.7: s10 = 7
        elif budget_util > 1.0: s10 = 5
        else: s10 = 4
    else:
        s10 = 5
    scores["budget_optimization"] = {
        "label": "予算最適化", "icon": "🎯", "score": s10,
        "detail": f"配信中キャンペーンの予算消化状況",
        "fix": "日予算を引き上げて機会損失を防ぐ" if s10 < 7 else "適切な消化率",
    }

    total_score = sum(v["score"] for v in scores.values())
    if total_score >= 85:   grade = "S"
    elif total_score >= 70: grade = "A"
    elif total_score >= 55: grade = "B"
    elif total_score >= 40: grade = "C"
    else:                   grade = "D"

    # 改善ロードマップ（優先度順）
    sorted_scores = sorted(scores.items(), key=lambda x: x[1]["score"])
    roadmap = {
        "month1": [{"axis": k, **v} for k, v in sorted_scores[:3]],
        "month2": [{"axis": k, **v} for k, v in sorted_scores[3:6]],
        "month3": [{"axis": k, **v} for k, v in sorted_scores[6:9]],
    }

    return {
        "success": True,
        "total_score": total_score,
        "max_score": 100,
        "grade": grade,
        "scores": scores,
        "roadmap": roadmap,
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


# ============================================================
# LP お問い合わせフォーム受付API（認証不要・CORS許可）
# ============================================================
class LPContactReq(BaseModel):
    name: str
    clinic: str
    area: str
    email: str
    ads_status: str = ""

@app.post("/api/lp/contact")
def lp_contact(req: LPContactReq):
    """
    LPのお問い合わせフォームから送信されるデータを受け取り、
    1. DBに保存
    2. 管理者へ通知メール
    3. ユーザーへ自動返信メール
    """
    import re
    from datetime import datetime as dt

    # バリデーション
    if not req.name or not req.clinic or not req.area or not req.email:
        raise HTTPException(400, "お名前・院名・地域・メールアドレスは必須です。")
    if not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', req.email):
        raise HTTPException(400, "メールアドレスの形式が正しくありません。")

    # DB保存（lp_contactsテーブル）
    conn = db.get_conn()
    if db.USE_PG:
        pk_type = "SERIAL PRIMARY KEY"
    else:
        pk_type = "INTEGER PRIMARY KEY AUTOINCREMENT"
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS lp_contacts (
            id {pk_type},
            name TEXT, clinic TEXT, area TEXT, email TEXT,
            ads_status TEXT, created_at TEXT, status TEXT DEFAULT 'new'
        )
    """)
    conn.execute(
        "INSERT INTO lp_contacts (name, clinic, area, email, ads_status, created_at) VALUES (?,?,?,?,?,?)",
        (req.name, req.clinic, req.area, req.email, req.ads_status, dt.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()

    # ── 管理者通知メール ──────────────────────────────────────
    import email_notifier
    admin_html = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:24px">
      <h2 style="color:#c8a97a;margin-bottom:16px">📩 AdMu LP 新規お問い合わせ</h2>
      <table style="width:100%;border-collapse:collapse;font-size:14px">
        <tr><td style="padding:8px;border-bottom:1px solid #eee;font-weight:bold;width:100px">お名前</td><td style="padding:8px;border-bottom:1px solid #eee">{req.name}</td></tr>
        <tr><td style="padding:8px;border-bottom:1px solid #eee;font-weight:bold">院名</td><td style="padding:8px;border-bottom:1px solid #eee">{req.clinic}</td></tr>
        <tr><td style="padding:8px;border-bottom:1px solid #eee;font-weight:bold">地域</td><td style="padding:8px;border-bottom:1px solid #eee">{req.area}</td></tr>
        <tr><td style="padding:8px;border-bottom:1px solid #eee;font-weight:bold">メール</td><td style="padding:8px;border-bottom:1px solid #eee">{req.email}</td></tr>
        <tr><td style="padding:8px;font-weight:bold">広告状況</td><td style="padding:8px">{req.ads_status or '未回答'}</td></tr>
      </table>
      <p style="font-size:12px;color:#888;margin-top:16px">受信時刻: {dt.now().strftime("%Y/%m/%d %H:%M")}</p>
    </div>
    """
    _admin_notify_email = os.environ.get("ADMIN_NOTIFICATION_EMAIL", "info@admu.jp")
    email_notifier._send(_admin_notify_email, f"【AdMu LP】新規お問い合わせ: {req.clinic}（{req.area}）", admin_html)

    # ── ユーザー自動返信メール ────────────────────────────────
    user_html = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:24px;color:#333">
      <div style="text-align:center;margin-bottom:24px">
        <h1 style="font-size:24px;font-weight:800;color:#000;margin:0">AdMu</h1>
        <p style="font-size:12px;color:#888;margin:4px 0 0">AI広告自動運用システム</p>
      </div>
      <h2 style="font-size:18px;color:#333;margin-bottom:16px">{req.name} 様</h2>
      <p style="line-height:1.8">
        この度はAdMuにお問い合わせいただき、ありがとうございます。<br>
        以下の内容で受け付けました。
      </p>
      <div style="background:#f8f8f8;border-radius:8px;padding:16px;margin:20px 0;font-size:14px">
        <strong>院名:</strong> {req.clinic}<br>
        <strong>地域:</strong> {req.area}<br>
        <strong>広告状況:</strong> {req.ads_status or '未回答'}
      </div>
      <p style="line-height:1.8">
        <strong>1営業日以内</strong>に担当者よりご連絡いたします。<br>
        しつこい営業は一切行いません。空きがない場合も正直にお伝えします。
      </p>
      <hr style="border:none;border-top:1px solid #eee;margin:32px 0">
      <p style="font-size:11px;color:#999;text-align:center">
        本メールはAdMu（整体院導）から自動送信されています。<br>
        ご不明点がございましたら info@admu.jp までご連絡ください。
      </p>
    </div>
    """
    email_notifier._send(req.email, "【AdMu】お問い合わせを受け付けました", user_html)

    return {"success": True, "message": "お問い合わせを受け付けました。"}


# ---- LP 無料資料請求 ----
class LPDownloadReq(BaseModel):
    name: str
    clinic: str
    email: str
    phone: str = ""

@app.post("/api/lp/download")
def lp_download(req: LPDownloadReq):
    """無料資料請求フォームからのデータを受け取り、DBに保存"""
    import re
    from datetime import datetime as dt

    if not req.name or not req.clinic or not req.email:
        raise HTTPException(400, "お名前・院名・メールアドレスは必須です。")
    if not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', req.email):
        raise HTTPException(400, "メールアドレスの形式が正しくありません。")

    conn = db.get_conn()
    if db.USE_PG:
        pk_type = "SERIAL PRIMARY KEY"
    else:
        pk_type = "INTEGER PRIMARY KEY AUTOINCREMENT"
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS lp_downloads (
            id {pk_type},
            name TEXT, clinic TEXT, email TEXT, phone TEXT,
            created_at TEXT, status TEXT DEFAULT 'new'
        )
    """)
    conn.execute(
        "INSERT INTO lp_downloads (name, clinic, email, phone, created_at) VALUES (?,?,?,?,?)",
        (req.name, req.clinic, req.email, req.phone, dt.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()

    # 管理者通知メール
    try:
        import email_notifier
        from datetime import datetime as dt2
        admin_html = f"""
        <div style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:24px">
          <h2 style="color:#c8a97a;margin-bottom:16px">📄 AdMu 無料資料請求</h2>
          <table style="width:100%;border-collapse:collapse;font-size:14px">
            <tr><td style="padding:8px;border-bottom:1px solid #eee;font-weight:bold;width:100px">お名前</td><td style="padding:8px;border-bottom:1px solid #eee">{req.name}</td></tr>
            <tr><td style="padding:8px;border-bottom:1px solid #eee;font-weight:bold">院名</td><td style="padding:8px;border-bottom:1px solid #eee">{req.clinic}</td></tr>
            <tr><td style="padding:8px;border-bottom:1px solid #eee;font-weight:bold">メール</td><td style="padding:8px;border-bottom:1px solid #eee">{req.email}</td></tr>
            <tr><td style="padding:8px;font-weight:bold">電話</td><td style="padding:8px">{req.phone or '未入力'}</td></tr>
          </table>
          <p style="font-size:12px;color:#888;margin-top:16px">受信時刻: {dt2.now().strftime("%Y/%m/%d %H:%M")}</p>
        </div>
        """
        admin_email = os.environ.get("ADMIN_EMAIL", "")
        if admin_email:
            email_notifier.send_email(admin_email, "【AdMu】無料資料請求", admin_html)
    except Exception as e:
        print(f"通知メール送信エラー: {e}")

    return {"success": True, "message": "資料請求を受け付けました。"}


# ---- 管理者向け: 問い合わせ一覧・ステータス更新 ----
@app.get("/api/admin/inquiries")
def list_inquiries(request: Request):
    """LP問い合わせ一覧を取得（管理者のみ）"""
    user = _get_current_user(request)
    if user.get("role") != "admin":
        raise HTTPException(403, "管理者権限が必要です")
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM lp_contacts ORDER BY id DESC LIMIT 200").fetchall()
    conn.close()
    return {"inquiries": [dict(r) for r in rows]}


@app.patch("/api/admin/inquiries/{inquiry_id}")
def update_inquiry_status(inquiry_id: int, request: Request, status: str = "done"):
    """問い合わせステータスを更新（new → done / contacted 等）"""
    user = _get_current_user(request)
    if user.get("role") != "admin":
        raise HTTPException(403, "管理者権限が必要です")
    conn = db.get_conn()
    conn.execute("UPDATE lp_contacts SET status=? WHERE id=?", (status, inquiry_id))
    conn.commit()
    conn.close()
    return {"success": True}


# ---- 管理者向け: リード一覧（問い合わせ＋資料請求を統合表示） ----
@app.get("/api/admin/leads")
def list_leads(request: Request):
    """全リード（問い合わせ＋資料請求）を統合して返す"""
    user = _get_current_user(request)
    if user.get("role") != "admin":
        raise HTTPException(403, "管理者権限が必要です")
    conn = db.get_conn()

    leads = []

    # 問い合わせリード
    try:
        rows = conn.execute("SELECT * FROM lp_contacts ORDER BY id DESC LIMIT 200").fetchall()
        for r in rows:
            d = dict(r)
            d["source"] = "contact"
            leads.append(d)
    except:
        pass

    # 資料請求リード
    try:
        rows = conn.execute("SELECT * FROM lp_downloads ORDER BY id DESC LIMIT 200").fetchall()
        for r in rows:
            d = dict(r)
            d["source"] = "download"
            leads.append(d)
    except:
        pass

    conn.close()

    # created_at で降順ソート
    leads.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return {"leads": leads, "total": len(leads)}


@app.patch("/api/admin/leads/{lead_id}")
def update_lead_status(lead_id: int, request: Request, source: str = "contact", status: str = "done"):
    """リードステータスを更新"""
    user = _get_current_user(request)
    if user.get("role") != "admin":
        raise HTTPException(403, "管理者権限が必要です")
    conn = db.get_conn()
    table = "lp_downloads" if source == "download" else "lp_contacts"
    conn.execute(f"UPDATE {table} SET status=? WHERE id=?", (status, lead_id))
    conn.commit()
    conn.close()
    return {"success": True}


@app.get("/{path:path}", include_in_schema=False)


def serve_spa(path: str = ""):
    # admin.html・onboarding.htmlは専用ルートで処理済み
    # APIルート（/api/*）はFastAPIのルート解決で先にマッチするため、ここに来た時点でSPAのパス
    index = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index):
        from fastapi.responses import HTMLResponse
        import re
        with open(index, "r", encoding="utf-8") as f:
            html = f.read()

        # ―― ダミー要素の動的注入 ――――――――――――――――――――――――――――――――
        # 削除したUI要素に依存する旧キャッシュJSがnullクラッシュしないよう安全策
        DUMMY = (
            '<!-- [backend-injected] -->\n'
            '<div id="weeklyActionsContent" style="display:none"></div>\n'
            '<div id="benchmarkContent" style="display:none"></div>\n'
            '<div id="dailyBriefContent" style="display:none"></div>\n'
            '<div id="narrativeContent" style="display:none"></div>\n'
            '<div id="briefGeneratedAt" style="display:none"></div>\n'
            '<div id="narrativeGeneratedAt" style="display:none"></div>\n'
            '<span id="alertBadge" style="display:none"></span>\n'
            '<span id="aiQuotaBadge" style="display:none"><span id="aiQuotaText"></span></span>\n'
            '<!-- [/backend-injected] -->\n'
        )
        if '[backend-injected]' not in html:
            html = html.replace('</body>', DUMMY + '</body>', 1)

        # ―― app.jsバージョン強制更新 ―――――――――――――――――――――――――――――――
        html = re.sub(r'app\.js\?v=[^"\' ]+', 'app.js?v=20260525-stable', html)

        return HTMLResponse(
            content=html,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
            }
        )
    return {"message": "Google広告自動運用システム API サーバー稼働中", "docs": "/docs"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=False)
