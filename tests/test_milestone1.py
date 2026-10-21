import os
import re

import psycopg
import pytest

from app.bootstrap import DEMO_PASSWORD, initialize
from app.db import connect
from app.security import LOGIN_CSRF_COOKIE, SESSION_COOKIE, hash_token


def csrf_from(response):
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)


def login(client, username="a.reader", password=DEMO_PASSWORD, **kwargs):
    csrf = csrf_from(client.get("/login"))
    kwargs.setdefault("headers", {"Origin": "http://127.0.0.1:8008"})
    return client.post(
        "/login", data={"username": username, "password": password, "csrf_token": csrf},
        follow_redirects=False, **kwargs,
    )


@pytest.mark.parametrize("prefix,base", [("a", 1000), ("b", 2000)])
@pytest.mark.parametrize("suffix,offsets", [
    ("admin", [1]), ("supervisor", [1]), ("owner", [1, 2, 3]),
    ("reader", [1, 3]), ("outsider", [1]),
])
def test_all_accounts_authorization_matrix(client, prefix, base, suffix, offsets):
    assert login(client, f"{prefix}.{suffix}").status_code == 303
    me = client.get("/api/me").json()
    assert me["tenant_id"] == base // 1000
    assert "token_hash" not in me and "password_hash" not in me
    # Caller-controlled identity cannot override the server session.
    response = client.get(
        "/api/documents?tenant_id=999&user_id=103&role=admin",
        headers={"X-Tenant-ID": "999", "X-User-ID": "103"},
    )
    assert response.status_code == 200
    assert {d["id"] for d in response.json()} == {base + i for i in offsets}
    assert client.get("/documents").status_code == 200
    for document_id in (1001, 1002, 1003, 2001, 2002, 2003, 999999):
        allowed = document_id in {base + i for i in offsets}
        api = client.get(f"/api/documents/{document_id}?tenant_id=2&user_id=203")
        page = client.get(f"/documents/{document_id}")
        assert api.status_code == page.status_code == (200 if allowed else 404)
        if not allowed:
            assert api.json() == {"detail": "找不到文件"}
            assert "私人工作筆記" not in page.text
            assert "指定分享內容" not in page.text
    public = client.get(f"/api/documents/{base + 1}").json()["content"]
    assert ("1,500" if prefix == "a" else "3,000") in public
    assert ("3,000" if prefix == "a" else "1,500") not in public


def test_anonymous_and_forged_sessions(client):
    for path in ("/api/me", "/api/documents", "/api/documents/1001"):
        assert client.get(path).status_code == 401
    assert client.get("/documents", follow_redirects=False).headers["location"] == "/login"
    client.cookies.set(SESSION_COOKIE, "tenant_id=1;user_id=103;role=admin")
    assert client.get("/api/documents").status_code == 401


def test_login_errors_are_generic_and_normalization_works(client, admin):
    responses = [
        login(client, "a.reader", "wrong"),
        login(client, "nonexistent", "wrong"),
        login(client, "' OR 1=1 --", "wrong"),
    ]
    admin.execute("UPDATE app_core.users SET is_active=false WHERE id=104")
    admin.commit()
    responses.append(login(client))
    assert all(r.status_code == 401 for r in responses)
    assert len({r.text for r in responses}) == 1
    admin.execute("UPDATE app_core.users SET is_active=true WHERE id=104")
    admin.commit()
    assert login(client, " A.READER ").status_code == 303


def test_login_csrf_origin_and_expiry(client):
    token = csrf_from(client.get("/login"))
    data = {"username": "a.reader", "password": DEMO_PASSWORD, "csrf_token": token}
    assert client.post("/login", data={**data, "csrf_token": "forged"}).status_code == 403
    assert client.post("/login", data=data, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/login", data=data, headers={"Origin": "null"}).status_code == 403
    client.cookies.clear()
    assert client.post("/login", data=data).status_code == 403
    from app.security import signature
    payload = "1.old"
    expired = payload + "." + signature(os.environ["APP_CSRF_SECRET"], payload)
    client.cookies.set(LOGIN_CSRF_COOKIE, expired)
    assert client.post("/login", data={**data, "csrf_token": expired}).status_code == 403


def test_rate_limit_survives_failed_request_rollback(client, admin):
    for _ in range(10):
        assert login(client, password="wrong").status_code == 401
    response = login(client)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "600"
    assert admin.execute("SELECT attempts FROM app_core.login_limits").fetchone()["attempts"] == 11
    admin.execute("UPDATE app_core.login_limits SET started_at=now()-interval '11 minutes'")
    admin.commit()
    assert login(client).status_code == 303


def test_session_hash_cookie_rotation_and_logout(client, admin):
    response = login(client)
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    old = client.cookies[SESSION_COOKIE]
    stored = admin.execute("SELECT token_hash FROM app_core.sessions").fetchone()["token_hash"]
    assert stored == hash_token(old) and stored != old
    assert login(client).status_code == 303
    current = client.cookies[SESSION_COOKIE]
    assert current != old
    assert admin.execute("SELECT count(*) AS n FROM app_core.sessions").fetchone()["n"] == 1
    token = csrf_from(client.get("/documents"))
    assert client.post("/logout", data={"csrf_token": "bad"}).status_code == 403
    assert client.post("/logout", data={"csrf_token": "偽造驗證碼"}).status_code == 403
    assert client.post("/logout", data={"csrf_token": token},
                       headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/me").status_code == 200
    assert client.post("/logout", data={"csrf_token": token}, follow_redirects=False).status_code == 303
    assert client.get("/api/me").status_code == 401
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, current)
    assert client.get("/api/me").status_code == 401
    client.cookies.set(SESSION_COOKIE, old)
    assert client.get("/api/me").status_code == 401


@pytest.mark.parametrize("mutation", [
    "UPDATE app_core.sessions SET expires_at=now()-interval '1 second'",
    "UPDATE app_core.sessions SET last_seen_at=now()-interval '31 minutes'",
    "UPDATE app_core.users SET is_active=false WHERE id=104",
])
def test_expired_or_disabled_identity_is_rejected_next_request(client, admin, mutation):
    login(client)
    admin.execute(mutation)
    admin.commit()
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/documents/1003").status_code == 401


def test_revocation_and_document_state_checked_each_request(client, admin):
    login(client)
    assert client.get("/api/documents/1003").status_code == 200
    admin.execute("DELETE FROM app_core.version_readers WHERE user_id=104")
    admin.commit()
    assert client.get("/api/documents/1003").status_code == 404
    for state in ("processing", "failed", "archived"):
        admin.execute("UPDATE app_core.documents SET status=%s WHERE id=1001", (state,))
        admin.commit()
        assert client.get("/api/documents/1001").status_code == 404
        assert client.get("/api/documents").json() == []


def test_runtime_least_privilege_and_cross_company_constraints(admin):
    with connect(os.environ["DATABASE_URL"]) as runtime:
        role = runtime.execute(
            "SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        assert not any(role.values())
        for statement in (
            "UPDATE app_core.users SET role='admin' WHERE id=104",
            "UPDATE app_core.documents SET owner_id=104 WHERE id=1002",
            "CREATE TABLE app_core.forbidden(id integer)",
            "SET ROLE rag_owner",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                runtime.execute(statement)
            runtime.rollback()
    for statement in (
        "INSERT INTO app_core.version_readers VALUES (1,1002,204)",
        "UPDATE app_core.documents SET owner_id=203 WHERE id=1001",
        "UPDATE app_core.users SET manager_id=202 WHERE id=103",
    ):
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            admin.execute(statement)
        admin.rollback()


def test_initialization_is_repeatable_without_resetting_data(client, admin):
    admin.execute("UPDATE app_core.users SET is_active=false WHERE id=105")
    admin.execute("DELETE FROM app_core.version_readers WHERE user_id=104")
    original_hash = admin.execute("SELECT password_hash FROM app_core.users WHERE id=104").fetchone()["password_hash"]
    admin.commit()
    initialize(os.environ["DATABASE_ADMIN_URL"], os.environ["RUNTIME_DB_PASSWORD"])
    assert admin.execute("SELECT count(*) AS n FROM app_core.users").fetchone()["n"] == 10
    assert admin.execute("SELECT count(*) AS n FROM app_core.documents").fetchone()["n"] == 6
    assert not admin.execute("SELECT is_active FROM app_core.users WHERE id=105").fetchone()["is_active"]
    assert admin.execute("SELECT password_hash FROM app_core.users WHERE id=104").fetchone()["password_hash"] == original_hash
    assert admin.execute("SELECT count(*) AS n FROM app_core.version_readers WHERE user_id=104").fetchone()["n"] == 0


def test_health_html_escaping_and_no_cache(client, admin):
    assert client.get("/health").json() == {"status": "ok"}
    login(client, "a.owner")
    from app.worker import run_once
    token = client.get("/api/csrf").json()["csrf_token"]
    assert client.post("/api/documents/1001/versions", json={"content": "<script>alert(1)</script>"},
                       headers={"X-CSRF-Token": token}).status_code == 202
    run_once(os.environ["WORKER_DATABASE_URL"])
    response = client.get("/documents/1001")
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "same-origin"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_database_dependency_rolls_back_on_error(client, admin):
    from fastapi import Depends, HTTPException, Request
    from app.db import get_db

    @client.app.get("/test-rollback")
    def rollback_route(request: Request, connection=Depends(get_db)):
        connection.execute("SELECT app_core.auth_logout(%s)", (hash_token(request.cookies[SESSION_COOKIE]),))
        raise HTTPException(400, "test")

    login(client)
    assert client.get("/test-rollback").status_code == 400
    assert client.get("/api/me").status_code == 200


def test_huge_guessed_id_returns_generic_missing(client):
    login(client)
    assert client.get("/api/documents/" + "9" * 100).json() == {"detail": "找不到文件"}


def test_private_visibility_does_not_honor_stale_reader_grant(client, admin):
    admin.execute("INSERT INTO app_core.version_readers VALUES (1,1002,104)")
    admin.commit()
    try:
        login(client)
        assert client.get("/api/documents/1002").status_code == 404
    finally:
        admin.execute("DELETE FROM app_core.version_readers WHERE version_id=1002")
        admin.commit()


def test_logout_csrf_cannot_be_reused_after_account_switch(client):
    login(client, "a.reader")
    csrf = csrf_from(client.get("/documents"))
    login(client, "b.reader")
    assert client.post("/logout", data={"csrf_token": csrf}).status_code == 403
    assert client.get("/api/me").json()["tenant_id"] == 2
    assert {d["id"] for d in client.get("/api/documents").json()} == {2001, 2003}
