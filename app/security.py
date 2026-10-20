"""Password verification, opaque sessions and CSRF protection are separate."""
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import HTTPException, Request

PASSWORD_HASHER = PasswordHasher()
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))
SESSION_COOKIE = "rag_session"
LOGIN_CSRF_COOKIE = "rag_login_csrf"


def utcnow():
    return datetime.now(timezone.utc)


def hash_token(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_matches(password, encoded):
    try:
        return PASSWORD_HASHER.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def signature(secret, value):
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def new_login_csrf(settings):
    payload = f"{int(time.time())}.{secrets.token_urlsafe(24)}"
    return f"{payload}.{signature(settings.csrf_secret, payload)}"


def session_csrf(settings, raw_session):
    return signature(settings.csrf_secret, "csrf:" + raw_session)


def check_origin(request):
    origin = request.headers.get("origin")
    if origin and origin != request.app.state.settings.base_url:
        raise HTTPException(403, "請從本網站提交操作")


def check_login_csrf(request, submitted):
    check_origin(request)
    cookie = request.cookies.get(LOGIN_CSRF_COOKIE, "")
    try:
        payload, mac = submitted.rsplit(".", 1)
        issued = int(payload.split(".", 1)[0])
        age = time.time() - issued
        valid = (
            0 <= age <= 600
            and hmac.compare_digest(cookie, submitted)
            and hmac.compare_digest(mac, signature(request.app.state.settings.csrf_secret, payload))
        )
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise HTTPException(403, "表單已過期，請重新載入登入頁面")


def check_session_csrf(request, submitted):
    check_origin(request)
    raw = request.cookies.get(SESSION_COOKIE, "")
    expected = session_csrf(request.app.state.settings, raw)
    if not raw or not submitted or not hmac.compare_digest(expected, submitted):
        raise HTTPException(403, "操作驗證失敗，請重新載入頁面")


def read_identity(connection, raw_session, settings):
    if not raw_session or len(raw_session) > 200:
        return None
    now = utcnow()
    identity = connection.execute(
        """SELECT u.id, u.tenant_id, u.username, u.display_name, u.role,
                  t.name AS tenant_name, s.token_hash
           FROM app_core.sessions s
           JOIN app_core.users u ON u.id = s.user_id
           JOIN app_core.tenants t ON t.id = u.tenant_id
           WHERE s.token_hash = %s AND u.is_active = true
             AND s.expires_at > %s AND s.last_seen_at > %s""",
        (hash_token(raw_session), now, now - timedelta(minutes=settings.idle_minutes)),
    ).fetchone()
    if identity:
        connection.execute(
            "UPDATE app_core.sessions SET last_seen_at = %s WHERE token_hash = %s",
            (now, identity["token_hash"]),
        )
    return identity


def attempt_login(connection, request, username, password):
    normalized = username.strip().casefold()
    remote = request.client.host if request.client else "unknown"
    attempt_key = hash_token(normalized + "|" + remote)
    now = utcnow()
    limit = connection.execute(
        """INSERT INTO app_core.login_limits (key_hash, started_at, attempts)
           VALUES (%s, %s, 1)
           ON CONFLICT (key_hash) DO UPDATE SET
             attempts = CASE WHEN app_core.login_limits.started_at < %s
                             THEN 1 ELSE app_core.login_limits.attempts + 1 END,
             started_at = CASE WHEN app_core.login_limits.started_at < %s
                               THEN EXCLUDED.started_at ELSE app_core.login_limits.started_at END
           RETURNING attempts""",
        (attempt_key, now, now - timedelta(minutes=10), now - timedelta(minutes=10)),
    ).fetchone()
    # Commit rejected attempts as well; raising an HTTP error must not erase them.
    connection.commit()
    if limit["attempts"] > 10:
        raise HTTPException(429, "嘗試次數過多，請稍後再試", headers={"Retry-After": "600"})
    user = connection.execute(
        "SELECT id, password_hash, is_active FROM app_core.users WHERE username = %s",
        (normalized,),
    ).fetchone()
    matches = password_matches(password, user["password_hash"] if user else DUMMY_PASSWORD_HASH)
    if not user or not matches or not user["is_active"]:
        raise HTTPException(401, "帳號或密碼不正確，或帳號無法使用")
    connection.execute("DELETE FROM app_core.login_limits WHERE key_hash = %s", (attempt_key,))
    raw = secrets.token_urlsafe(32)
    settings = request.app.state.settings
    previous = request.cookies.get(SESSION_COOKIE)
    if previous:
        connection.execute("DELETE FROM app_core.sessions WHERE token_hash = %s", (hash_token(previous),))
    connection.execute(
        """INSERT INTO app_core.sessions
           (token_hash, user_id, created_at, last_seen_at, expires_at)
           VALUES (%s, %s, %s, %s, %s)""",
        (hash_token(raw), user["id"], now, now, now + timedelta(hours=settings.session_hours)),
    )
    return raw


def set_session_cookie(response, raw, settings):
    response.set_cookie(
        SESSION_COOKIE, raw, httponly=True, secure=settings.cookie_secure,
        samesite="strict", max_age=settings.session_hours * 3600, path="/",
    )
    response.delete_cookie(LOGIN_CSRF_COOKIE, path="/")
