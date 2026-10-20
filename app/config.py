"""Runtime configuration. Database owner credentials are never passed to web."""
import os
from dataclasses import dataclass
from urllib.parse import urlparse


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
        result = cls(
            database_url=os.environ["DATABASE_URL"],
            csrf_secret=os.environ["APP_CSRF_SECRET"],
            base_url=os.getenv("APP_BASE_URL", "http://127.0.0.1:8008").rstrip("/"),
            environment=os.getenv("APP_ENV", "local"),
            cookie_secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        )
        if len(result.csrf_secret) < 32:
            raise ValueError("APP_CSRF_SECRET must contain at least 32 characters")
        if result.environment not in {"local", "test"}:
            if not result.cookie_secure or urlparse(result.base_url).scheme != "https":
                raise ValueError("Non-local environments require HTTPS and secure cookies")
            if result.csrf_secret.startswith("local-"):
                raise ValueError("Replace the local demonstration CSRF secret")
        return result
