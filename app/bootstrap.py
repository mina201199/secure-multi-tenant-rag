"""Repeatable, non-destructive local demonstration database initialization."""
import os
from pathlib import Path

from psycopg import sql

from app.db import connect
from app.security import PASSWORD_HASHER
from app.migrations import migrate

DEMO_PASSWORD = "Demo-Rag-2026!"


def initialize(admin_url, runtime_password):
    if len(runtime_password) < 12:
        raise ValueError("RUNTIME_DB_PASSWORD must contain at least 12 characters")
    with connect(admin_url) as connection:
        # Serialize concurrent setup processes; existing rows are never reset.
        connection.execute("SELECT pg_advisory_xact_lock(726401)")
        role = connection.execute(
            "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb "
            "FROM pg_roles WHERE rolname = 'rag_app'"
        ).fetchone()
        if role and any(role.values()):
            raise ValueError("rag_app must be an unprivileged runtime role")
        if not role:
            connection.execute("CREATE ROLE rag_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS")
        connection.execute(
            sql.SQL("ALTER ROLE rag_app WITH LOGIN PASSWORD {}").format(sql.Literal(runtime_password))
        )
        upgraded = connection.execute("SELECT to_regclass('app_core.schema_migrations') AS name").fetchone()["name"]
        if not upgraded:
            connection.execute(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
        for tenant_id, prefix, name, allowance in (
            (1, "a", "甲公司", 1500), (2, "b", "乙公司", 3000)
        ):
            created = connection.execute(
                "INSERT INTO app_core.tenants VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING id",
                (tenant_id, name),
            ).fetchone()
            # Seed each company only once. Restarting must not restore revoked ACLs.
            if not created:
                continue
            for index, suffix, label in (
                (1, "admin", "管理員"), (2, "supervisor", "主管"),
                (3, "owner", "文件擁有者"), (4, "reader", "獲授權員工"),
                (5, "outsider", "未獲指定授權員工"),
            ):
                connection.execute(
                    """INSERT INTO app_core.users
                       (id, tenant_id, username, display_name, password_hash, role, manager_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (tenant_id * 100 + index, tenant_id, f"{prefix}.{suffix}",
                     name + label, PASSWORD_HASHER.hash(DEMO_PASSWORD),
                     "admin" if index == 1 else "employee",
                     tenant_id * 100 + 2 if index >= 3 else None),
                )
            for index, visibility, title, content in (
                (1, "company", "出差補助辦法", f"本公司每日出差補助為 NT${allowance:,}。"),
                (2, "private", "私人工作筆記", f"{name}擁有者私人筆記，僅本人可讀。"),
                (3, "selected", "指定分享作業規定", f"{name}指定分享內容：領料時須核對料號及單位。"),
            ):
                connection.execute(
                    """INSERT INTO app_core.documents
                       (id,tenant_id,owner_id,title,category,content,visibility,status)
                       VALUES (%s,%s,%s,%s,'教學範例',%s,%s,'ready')
                       ON CONFLICT DO NOTHING""",
                    (tenant_id * 1000 + index, tenant_id, tenant_id * 100 + 3,
                     title, content, visibility),
                )
            connection.execute(
                "INSERT INTO app_core.document_readers VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                (tenant_id, tenant_id * 1000 + 3, tenant_id * 100 + 4),
            )
        worker_role = connection.execute(
            "SELECT rolsuper,rolbypassrls,rolcreaterole,rolcreatedb FROM pg_roles WHERE rolname='rag_worker'"
        ).fetchone()
        if worker_role and any(worker_role.values()):
            raise ValueError("rag_worker must be an unprivileged role")
        if not worker_role:
            connection.execute("CREATE ROLE rag_worker LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS")
        for name in ("rag_app", "rag_worker"):
            if connection.execute(
                """SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname=%s
                   UNION ALL SELECT 1 FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner
                   JOIN pg_namespace n ON n.oid=c.relnamespace WHERE r.rolname=%s AND n.nspname IN ('app_core','app_private')""",
                (name, name),
            ).fetchone():
                raise ValueError("Runtime roles cannot own application tables or have role memberships")
        worker_password = os.environ["WORKER_DB_PASSWORD"]
        if len(worker_password) < 12:
            raise ValueError("WORKER_DB_PASSWORD must contain at least 12 characters")
        connection.execute(sql.SQL("ALTER ROLE rag_worker PASSWORD {}").format(sql.Literal(worker_password)))
        migrate(connection)
        secret = os.environ["APP_CSRF_SECRET"]
        if len(secret) < 32:
            raise ValueError("APP_CSRF_SECRET must contain at least 32 characters")
        connection.execute(
            "INSERT INTO app_private.runtime_secret(id,value) VALUES(true,%s) "
            "ON CONFLICT(id) DO UPDATE SET value=EXCLUDED.value", (secret,),
        )
        for filename in ("policies.sql", "lifecycle.sql", "retrieval.sql", "grants.sql"):
            connection.execute(Path(__file__).with_name(filename).read_text(encoding="utf-8"))
    print("Database initialized; existing users and documents preserved.")


if __name__ == "__main__":
    initialize(os.environ["DATABASE_ADMIN_URL"], os.environ["RUNTIME_DB_PASSWORD"])
