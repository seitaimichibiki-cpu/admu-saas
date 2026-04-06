"""
auth.py - JWT認証・パスワードハッシュモジュール
"""
import os
import hashlib
import hmac
import time
import json
from datetime import datetime, timedelta
from typing import Optional

# ---- 設定 ----
JWT_SECRET = os.environ.get("JWT_SECRET", os.environ.get("SECRET_KEY", "dev-secret-change-in-production"))
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24 * 7  # 7日間


# ============================================================
# パスワードハッシュ（bcryptの代わりにpbkdf2_hmacを使用）
# ============================================================
def hash_password(password: str) -> str:
    """パスワードをPBKDF2-SHA256でハッシュ化"""
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 260000)
    return salt.hex() + ":" + key.hex()


def verify_password(password: str, hashed: str) -> bool:
    """パスワードを検証"""
    try:
        salt_hex, key_hex = hashed.split(":")
        salt = bytes.fromhex(salt_hex)
        key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 260000)
        return hmac.compare_digest(key.hex(), key_hex)
    except Exception:
        return False


# ============================================================
# JWT（python-joseの代わりにシンプルな実装）
# ============================================================
def _b64url_encode(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    import base64
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def create_access_token(user_id: int, email: str, clinic_id: int, role: str) -> str:
    """JWTアクセストークンを生成"""
    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    expire = datetime.utcnow() + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {
        "sub": str(user_id),
        "email": email,
        "clinic_id": clinic_id,
        "role": role,
        "exp": int(expire.timestamp()),
        "iat": int(time.time()),
    }
    header_enc = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_enc = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_enc}.{payload_enc}"
    signature = hmac.new(
        JWT_SECRET.encode(),
        signing_input.encode(),
        hashlib.sha256
    ).digest()
    sig_enc = _b64url_encode(signature)
    return f"{signing_input}.{sig_enc}"


def decode_access_token(token: str) -> Optional[dict]:
    """JWTを検証してペイロードを返す。無効な場合はNone"""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header_enc, payload_enc, sig_enc = parts
        signing_input = f"{header_enc}.{payload_enc}"
        expected_sig = hmac.new(
            JWT_SECRET.encode(),
            signing_input.encode(),
            hashlib.sha256
        ).digest()
        actual_sig = _b64url_decode(sig_enc)
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None
        payload = json.loads(_b64url_decode(payload_enc))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


# ============================================================
# パスワードリセットトークン
# ============================================================
def create_reset_token(email: str) -> str:
    """パスワードリセット用トークンを生成（1時間有効）"""
    expire = int(time.time()) + 3600
    data = f"{email}:{expire}"
    sig = hmac.new(JWT_SECRET.encode(), data.encode(), hashlib.sha256).hexdigest()
    import base64
    payload = base64.urlsafe_b64encode(f"{data}:{sig}".encode()).decode()
    return payload


def verify_reset_token(token: str) -> Optional[str]:
    """パスワードリセットトークンを検証してメールアドレスを返す"""
    try:
        import base64
        decoded = base64.urlsafe_b64decode(token + "==").decode()
        parts = decoded.rsplit(":", 1)
        if len(parts) != 2:
            return None
        data, sig = parts
        expected = hmac.new(JWT_SECRET.encode(), data.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        # data = "email:expire"
        data_parts = data.rsplit(":", 1)
        email, expire_str = data_parts[0], data_parts[1]
        if int(expire_str) < time.time():
            return None
        return email
    except Exception:
        return None


# ============================================================
# FastAPI依存関数
# ============================================================
def get_current_user_from_header(authorization: Optional[str]) -> Optional[dict]:
    """Authorizationヘッダーからユーザー情報を取得"""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    return decode_access_token(token)
