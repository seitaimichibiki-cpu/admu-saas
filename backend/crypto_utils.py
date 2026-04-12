"""
crypto_utils.py - 外部API認証情報の暗号化/復号化ユーティリティ
SaaSのデータベース保存時に、平文で認証情報が保存されるリスクを防ぐためのモジュール。
"""
import os
import warnings
from cryptography.fernet import Fernet, InvalidToken

_PREFIX = "enc:"

# 環境変数からキーを取得
_KEY_STR = os.environ.get("ENCRYPTION_KEY")

if not _KEY_STR:
    warnings.warn("⚠️ ENCRYPTION_KEY が未設定です。一時的なキーを生成して使用します（再起動後に以前のデータが復号不可になります）。本番環境では必ず環境変数を設定してください。")
    _KEY_STR = Fernet.generate_key().decode("utf-8")
    # .env ファイルに自動追記を試みる（ローカル開発用）
    try:
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        with open(env_path, "a") as f:
            f.write(f"\nENCRYPTION_KEY={_KEY_STR}\n")
        print(f"[Crypto] ローカルの .env に新しい ENCRYPTION_KEY を書き込みました。")
    except Exception:
        pass

# Fernetインスタンスの初期化
try:
    _fernet = Fernet(_KEY_STR.encode("utf-8"))
except ValueError as e:
    raise ValueError(f"ENCRYPTION_KEYの形式が不正です（Base64エンコードされた32バイト鍵である必要があります）: {e}")


def encrypt(value: str) -> str:
    """文字列を暗号化して返す（空文字や既に暗号化されている場合はそのまま返す）"""
    if not value:
        return value
    
    # 既に暗号化されている場合はスキップ
    if value.startswith(_PREFIX):
        return value
        
    encrypted = _fernet.encrypt(value.encode("utf-8")).decode("utf-8")
    return f"{_PREFIX}{encrypted}"


def decrypt(value: str) -> str:
    """暗号化された文字列を復号して返す（平文の場合はそのまま返す）"""
    if not value:
        return value
        
    # 指定のプレフィックスがない場合は平文とみなす
    if not value.startswith(_PREFIX):
        return value
        
    token = value[len(_PREFIX):]
    try:
        decrypted = _fernet.decrypt(token.encode("utf-8")).decode("utf-8")
        return decrypted
    except InvalidToken:
        warnings.warn("⚠️ トークンの復号に失敗しました。不正なキーが使用されたか、データが破損しています。")
        # 復号失敗時は元の文字列（"enc:..."）をそのまま返すか、フェイルセーフのために空にするか要検討。
        # ここではエラーを起こさずそのまま返す（最悪上書き等を防ぐため）
        return value
