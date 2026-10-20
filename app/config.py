"""執行期設定。資料庫 owner 憑證不會傳給 web，web 只拿得到受限的 runtime 連線。"""
import os
from dataclasses import dataclass
from urllib.parse import urlparse

# 只有這兩個環境允許 HTTP 與本機示範密鑰；其他任何名稱都套用正式環境的檢查
RELAXED_ENVIRONMENTS = {"local", "test"}


@dataclass(frozen=True)
class Settings:
    database_url: str
    csrf_secret: str
    base_url: str = "http://127.0.0.1:8008"
    environment: str = "local"
    cookie_secure: bool = False
    idle_minutes: int = 30
    session_hours: int = 8

    @classmethod
    def from_env(cls):
        # 不給 APP_ENV 預設值：忘記設定時若默默當成 local，正式環境就會跳過 HTTPS 檢查
        environment = os.getenv("APP_ENV", "").strip()
        if not environment:
            raise ValueError("APP_ENV must be set explicitly, e.g. local, test or production")
        result = cls(
            database_url=os.environ["DATABASE_URL"],
            csrf_secret=os.environ["APP_CSRF_SECRET"],
            base_url=os.getenv("APP_BASE_URL", "http://127.0.0.1:8008").rstrip("/"),
            environment=environment,
            cookie_secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        )
        if len(result.csrf_secret) < 32:
            raise ValueError("APP_CSRF_SECRET must contain at least 32 characters")
        if result.environment not in RELAXED_ENVIRONMENTS:
            if not result.cookie_secure or urlparse(result.base_url).scheme != "https":
                raise ValueError("Non-local environments require HTTPS and secure cookies")
            if result.csrf_secret.startswith(("local-", "change-me")):
                raise ValueError("Replace the local demonstration CSRF secret")
        return result
