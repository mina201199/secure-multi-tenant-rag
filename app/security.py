"""密碼驗證、不透明 Session token 與 CSRF 防護各自獨立，任何一項失效都不會讓其他防線一起失效。"""
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import HTTPException, Request

PASSWORD_HASHER = PasswordHasher()
# 帳號不存在時也拿這個假雜湊跑一次 Argon2，讓回應時間無法用來猜哪些帳號存在
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))
SESSION_COOKIE = "rag_session"
LOGIN_CSRF_COOKIE = "rag_login_csrf"
LOGIN_CSRF_SECONDS = 600


def utcnow():
    return datetime.now(timezone.utc)


def normalize_username(username):
    # 登入與 seed 共用同一個正規化規則，資料庫存的值才會與登入時查詢的值一致
    return username.strip().casefold()


def hash_token(value):
    # token 本身已是高熵亂數，不需要加鹽；存雜湊是為了資料庫外洩時無法直接冒用
    return hashlib.sha256(value.encode()).hexdigest()


def password_matches(password, encoded):
    try:
        return PASSWORD_HASHER.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def signature(secret, value):
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def new_login_csrf(settings):
    # 尚未登入時沒有 Session 可綁定，所以改用「時間戳 + 亂數 + 簽章」並同時放進 Cookie 與表單比對
    payload = f"{int(time.time())}.{secrets.token_urlsafe(24)}"
    return f"{payload}.{signature(settings.csrf_secret, payload)}"


def set_login_csrf_cookie(response, token, settings):
    response.set_cookie(
        LOGIN_CSRF_COOKIE, token, httponly=True, secure=settings.cookie_secure,
        samesite="strict", max_age=LOGIN_CSRF_SECONDS, path="/",
    )


def session_csrf(settings, raw_session):
    # 由 Session token 推導，不必另外存表；登出或換 Session 後舊的 CSRF token 自動失效
    return signature(settings.csrf_secret, "csrf:" + raw_session)


def request_origin(request):
    origin = request.headers.get("origin")
    if origin:
        return origin
    referer = request.headers.get("referer")
    if referer:
        parsed = urlparse(referer)
        return f"{parsed.scheme}://{parsed.netloc}"
    return None


def check_origin(request):
    # 瀏覽器送表單幾乎都會帶 Origin，少數隱私設定下只剩 Referer，所以兩者都檢查。
    # 兩者都沒有的通常是 curl 等非瀏覽器用戶端，沒有跨站偽造的風險，仍由 CSRF token 把關。
    origin = request_origin(request)
    if origin is not None and origin != request.app.state.settings.base_url:
        raise HTTPException(403, "請從本網站提交操作")


def check_login_csrf(request, submitted):
    check_origin(request)
    cookie = request.cookies.get(LOGIN_CSRF_COOKIE, "")
    try:
        payload, mac = submitted.rsplit(".", 1)
        issued = int(payload.split(".", 1)[0])
        age = time.time() - issued
        valid = (
            0 <= age <= LOGIN_CSRF_SECONDS
            and hmac.compare_digest(cookie, submitted)
            and hmac.compare_digest(mac, signature(request.app.state.settings.csrf_secret, payload))
        )
    except (ValueError, TypeError, AttributeError):
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
    # 過長的值不可能是我們發的 token，直接拒絕，避免拿大量資料去算雜湊
    if not raw_session or len(raw_session) > 200:
        return None
    now = utcnow()
    # 每次請求都重查帳號是否啟用與 Session 是否過期，停用帳號從下一次請求起立即失效
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
    normalized = normalize_username(username)
    remote = request.client.host if request.client else "unknown"
    # 以「帳號 + 來源位址」計數：同一來源猜同一帳號會被擋，但不會讓攻擊者一次鎖死所有人的帳號
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
    # 失敗時會丟 HTTPException 讓交易回滾；先 commit，計數才不會跟著被回滾而永遠擋不住
    connection.commit()
    if limit["attempts"] > 10:
        raise HTTPException(429, "嘗試次數過多，請稍後再試", headers={"Retry-After": "600"})
    user = connection.execute(
        "SELECT id, password_hash, is_active FROM app_core.users WHERE username = %s",
        (normalized,),
    ).fetchone()
    matches = password_matches(password, user["password_hash"] if user else DUMMY_PASSWORD_HASH)
    # 帳號不存在、密碼錯誤、帳號停用都回同一句話，不透露是哪一種
    if not user or not matches or not user["is_active"]:
        raise HTTPException(401, "帳號或密碼不正確，或帳號無法使用")
    connection.execute("DELETE FROM app_core.login_limits WHERE key_hash = %s", (attempt_key,))
    raw = secrets.token_urlsafe(32)
    settings = request.app.state.settings
    # 登入時作廢舊 Session，防止攻擊者預先塞入 Session 再等受害者登入（Session fixation）
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


def end_session(connection, raw_session):
    # 登出要刪掉資料庫的 Session，只清 Cookie 的話被偷走的 token 仍然能用
    if raw_session:
        connection.execute("DELETE FROM app_core.sessions WHERE token_hash = %s", (hash_token(raw_session),))


def set_session_cookie(response, raw, settings):
    response.set_cookie(
        SESSION_COOKIE, raw, httponly=True, secure=settings.cookie_secure,
        samesite="strict", max_age=settings.session_hours * 3600, path="/",
    )
    response.delete_cookie(LOGIN_CSRF_COOKIE, path="/")


def clear_session_cookie(response):
    response.delete_cookie(SESSION_COOKIE, path="/")
