import os
from urllib.parse import urlparse

import pytest
from psycopg import sql
from fastapi.testclient import TestClient

from app.bootstrap import initialize
from app.config import Settings
from app.db import connect
from app.main import create_app


@pytest.fixture(scope="session", autouse=True)
def database():
    admin_url = os.environ["DATABASE_ADMIN_URL"]
    runtime_url = os.environ["DATABASE_URL"]
    # Destructive test cleanup is allowed only in the explicit Compose test DB.
    if os.getenv("APP_ENV") != "test" or any(
        urlparse(url).hostname != "test-db" or urlparse(url).path != "/rag_test"
        for url in (admin_url, runtime_url)
    ):
        raise RuntimeError("Tests require the isolated test-db/rag_test database")
    with connect(admin_url) as connection:
        connection.execute("DROP SCHEMA IF EXISTS app_core CASCADE")
        connection.execute("DROP SCHEMA IF EXISTS app_private CASCADE")
    initialize(admin_url, os.environ["RUNTIME_DB_PASSWORD"])
    with connect(admin_url) as connection:
        snapshot = {table: connection.execute(sql.SQL("SELECT * FROM app_core.{} ORDER BY id").format(sql.Identifier(table))).fetchall()
                    for table in ("users", "documents", "document_versions")}
    yield snapshot


@pytest.fixture
def admin(database):
    with connect(os.environ["DATABASE_ADMIN_URL"]) as connection:
        connection.execute(
            "TRUNCATE app_core.documents,app_core.document_versions,app_core.version_readers,"
            "app_core.history_permissions,app_core.jobs,app_core.transfer_requests,app_core.transfer_documents,"
            "app_core.transfer_versions,app_core.audit_events,app_core.sessions,app_core.login_limits RESTART IDENTITY CASCADE"
        )
        for user in database["users"]:
            connection.execute(
                "UPDATE app_core.users SET is_active=true,role=%s,manager_id=%s WHERE id=%s",
                (user["role"], user["manager_id"], user["id"]),
            )
        for table in ("documents", "document_versions"):
            for row in database[table]:
                connection.execute(sql.SQL("INSERT INTO app_core.{} ({}) VALUES ({})").format(
                    sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, row)),
                    sql.SQL(",").join(sql.Placeholder() for _ in row)), tuple(row.values()))
        connection.execute("UPDATE app_core.documents SET status='ready'")
        connection.execute(
            "INSERT INTO app_core.version_readers VALUES (1,1003,104), (2,2003,204)"
        )
        connection.commit()
        yield connection
        connection.rollback()


@pytest.fixture
def client(admin):
    with TestClient(create_app(Settings.from_env()), base_url="http://127.0.0.1:8008") as client:
        yield client
