import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.security import PASSWORD_HASHER, attempt_login, hash_token
from tests.helpers import BASE_URL, CSRF_FIELD, login, login_csrf, session_cookie

GENERIC_LOGIN_ERROR = "帳號或密碼不正確，或帳號無法使用"


def test_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_login_success_sets_protected_cookie(client):
    response = login(client, "a.reader")
    assert response.status_code == 303
    cookie_header = response.headers["set-cookie"].lower()
    assert "httponly" in cookie_header and "samesite=strict" in cookie_header
    me = client.get("/api/me").json()
    assert me["username"] == "a.reader"
    assert me["tenant_name"] == "甲公司"
    assert "token_hash" not in me


def test_username_is_case_insensitive(client):
    assert login(client, "  A.Reader ").status_code == 303


def test_wrong_password_and_unknown_user_share_same_error(client):
    wrong = login(client, "a.reader", "not-the-password")
    unknown = login(client, f"nobody-{uuid.uuid4().hex[:8]}", "not-the-password")
    assert wrong.status_code == unknown.status_code == 401
    assert GENERIC_LOGIN_ERROR in wrong.text and GENERIC_LOGIN_ERROR in unknown.text
    assert session_cookie(client) is None


def test_inactive_seed_user_cannot_login(client):
    response = login(client, "a.inactive")
    assert response.status_code == 401
    assert GENERIC_LOGIN_ERROR in response.text


def test_login_requires_valid_csrf(client):
    client.get("/login")
    missing = client.post("/login", data={"username": "a.reader", "password": "x"}, headers={"Origin": BASE_URL})
    tampered = client.post(
        "/login",
        data={"username": "a.reader", "password": "x", "csrf_token": login_csrf(client) + "0"},
        headers={"Origin": BASE_URL},
    )
    assert missing.status_code == tampered.status_code == 403


def test_login_rejects_foreign_origin_and_referer(client):
    assert login(client, "a.reader", headers={"Origin": "https://evil.example"}).status_code == 403
    assert login(client, "a.reader", headers={"Referer": "https://evil.example/form"}).status_code == 403


def test_rate_limit_blocks_correct_password_after_ten_failures(client, admin_db):
    username = f"ratelimit-{uuid.uuid4().hex[:8]}"
    admin_db.execute(
        """INSERT INTO app_core.users (tenant_id, username, display_name, password_hash, role)
           SELECT id, %s, '限流測試', %s, 'employee' FROM app_core.tenants WHERE name = '甲公司'""",
        (username, PASSWORD_HASHER.hash("correct-password")),
    )
    try:
        for _ in range(10):
            assert login(client, username, "wrong-password").status_code == 401
        # 第 11 次即使密碼正確也要擋下
        blocked = login(client, username, "correct-password")
        assert blocked.status_code == 429
        assert blocked.headers["retry-after"] == "600"
        assert session_cookie(client) is None
    finally:
        admin_db.execute("DELETE FROM app_core.users WHERE username = %s", (username,))


def test_failed_attempt_count_survives_transaction_rollback(runtime_db, admin_db):
    username = f"rollback-{uuid.uuid4().hex[:8]}"
    fake_request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.9"))
    with pytest.raises(HTTPException):
        attempt_login(runtime_db, fake_request, username, "wrong-password")
    # 模擬 API 路由沒有攔下 401、整個 request 交易被回滾的情況
    runtime_db.rollback()
    row = admin_db.execute(
        "SELECT attempts FROM app_core.login_limits WHERE key_hash = %s",
        (hash_token(username + "|203.0.113.9"),),
    ).fetchone()
    assert row == (1,)


def test_logout_requires_csrf_and_revokes_session(client):
    login(client, "a.reader")
    raw = session_cookie(client)
    csrf = client.get("/api/me").json()["csrf_token"]
    assert client.post("/logout", data={}, headers={"Origin": BASE_URL}).status_code == 403
    assert client.post("/logout", data={"csrf_token": "wrong"}, headers={"Origin": BASE_URL}).status_code == 403
    response = client.post("/logout", data={"csrf_token": csrf}, headers={"Origin": BASE_URL}, follow_redirects=False)
    assert response.status_code == 303
    assert client.get("/api/me").status_code == 401
    # 即使攻擊者事先複製了舊 Cookie，登出後也不能再用
    client.cookies.set("rag_session", raw)
    assert client.get("/api/me").status_code == 401


def test_expired_session_is_rejected(client, admin_db):
    login(client, "a.staff")
    admin_db.execute(
        "UPDATE app_core.sessions SET expires_at = now() - interval '1 second' "
        "WHERE token_hash = %s",
        (hash_token(session_cookie(client)),),
    )
    assert client.get("/api/me").status_code == 401


def test_idle_session_is_rejected(client, admin_db):
    login(client, "a.staff")
    admin_db.execute(
        "UPDATE app_core.sessions SET last_seen_at = now() - interval '31 minutes' "
        "WHERE token_hash = %s",
        (hash_token(session_cookie(client)),),
    )
    assert client.get("/api/me").status_code == 401


def test_deactivation_applies_on_next_request(client, admin_db):
    login(client, "b.staff")
    assert client.get("/api/me").status_code == 200
    admin_db.execute("UPDATE app_core.users SET is_active = false WHERE username = 'b.staff'")
    try:
        assert client.get("/api/me").status_code == 401
        assert client.get("/api/documents").status_code == 401
    finally:
        admin_db.execute("UPDATE app_core.users SET is_active = true WHERE username = 'b.staff'")


def test_unauthenticated_requests(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/documents").status_code == 401
    assert client.get("/api/documents/1").status_code == 401
    page = client.get("/documents", follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"] == "/login"


def test_login_page_renders_csrf_field(client):
    page = client.get("/login")
    assert page.status_code == 200
    assert CSRF_FIELD.search(page.text)
    assert "rag_login_csrf" in page.headers["set-cookie"]
