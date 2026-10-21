import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from app.bootstrap import initialize
from app.config import Settings
from app.db import connect, bind_identity
from app.security import hash_token, read_identity, SESSION_COOKIE, PASSWORD_HASHER
from tests.test_milestone2 import account, call, new_version, grant, process, bound


@pytest.mark.parametrize("username,expected", [
    ("a.reader",{1001,1003}), ("b.reader",{2001,2003}),
    ("a.owner",{1001,1002,1003}), ("a.admin",{1001}), ("a.supervisor",{1001}),
])
def test_direct_sql_without_tenant_filter_is_isolated(account, username, expected):
    person=account(username)
    with bound(person) as runtime:
        assert {v["id"] for v in runtime.execute("SELECT id FROM app_core.document_versions").fetchall()}==expected
        assert {d["id"] for d in runtime.execute("SELECT id FROM app_core.documents").fetchall()}==expected
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            runtime.execute("SELECT password_hash FROM app_core.users")
        runtime.rollback()


def test_unsigned_spoofed_context_and_connection_reuse(account):
    person=account("a.reader")
    with connect(os.environ["DATABASE_URL"]) as runtime:
        assert runtime.execute("SELECT * FROM app_core.documents").fetchall()==[]
        for key,value in (("app.actor_id","203"),("app.tenant_id","2"),("app.context_signature","forged")):
            runtime.execute("SELECT set_config(%s,%s,true)",(key,value))
        assert runtime.execute("SELECT * FROM app_core.document_versions").fetchall()==[]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            runtime.execute("SELECT app_core.auth_issue()")
        runtime.rollback()
        assert read_identity(runtime,person.cookies[SESSION_COOKIE],Settings.from_env())
        assert runtime.execute("SELECT count(*) AS n FROM app_core.documents").fetchone()["n"]==2
        runtime.execute("SELECT set_config('app.tenant_id','2',true)")
        assert runtime.execute("SELECT * FROM app_core.documents").fetchall()==[]
        runtime.rollback()
        assert runtime.execute("SELECT * FROM app_core.documents").fetchall()==[]
        runtime.commit()
        assert read_identity(runtime,person.cookies[SESSION_COOKIE],Settings.from_env())
        runtime.commit()
        assert runtime.execute("SELECT * FROM app_core.documents").fetchall()==[]
        other=account("b.reader")
        assert read_identity(runtime,other.cookies[SESSION_COOKIE],Settings.from_env())
        assert {r["id"] for r in runtime.execute("SELECT id FROM app_core.documents").fetchall()}=={2001,2003}


def test_runtime_cannot_read_secret_create_sessions_or_bypass_rls(account):
    person=account("a.owner")
    for statement in (
        "SELECT value FROM app_private.runtime_secret",
        "SELECT token_hash FROM app_core.sessions",
        "INSERT INTO app_core.sessions VALUES ('forged',203,now(),now(),now()+interval '8 hours')",
        "UPDATE app_core.document_versions SET content='modified' WHERE id=1001",
        "UPDATE app_core.documents SET owner_id=105 WHERE id=1001",
        "SET ROLE rag_owner",
        "SET ROLE rag_worker",
        "ALTER TABLE app_core.documents DISABLE ROW LEVEL SECURITY",
        "SELECT * FROM app_core.claim_job()",
    ):
        with bound(person) as runtime:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                runtime.execute(statement)
            runtime.rollback()
    with bound(person) as runtime:
        runtime.execute("SET LOCAL row_security=off")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            runtime.execute("SELECT * FROM app_core.documents")
        runtime.rollback()


def test_rls_denies_raw_acl_insert_update_delete_even_for_owner(account):
    owner=account("a.owner")
    for table,values in (
        ("version_readers","(1,1002,104)"),("version_readers","(2,2002,204)"),
        ("history_permissions","(1,1002,104,103)"),("history_permissions","(2,2002,204,203)"),
    ):
        with bound(owner) as runtime:
            assert runtime.execute("SELECT has_table_privilege(current_user,%s,'INSERT') AS ok",
                                   ("app_core."+table,)).fetchone()["ok"]
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                runtime.execute(f"INSERT INTO app_core.{table} VALUES {values}")
            runtime.rollback()
    with bound(owner) as runtime:
        assert runtime.execute("UPDATE app_core.version_readers SET user_id=105 WHERE version_id=1003").rowcount==0
        assert runtime.execute("DELETE FROM app_core.version_readers").rowcount==0
        with pytest.raises(psycopg.errors.NoDataFound):
            runtime.execute("SELECT app_core.change('archive',%s)",(Jsonb({"document_id":2001,"tenant_id":2}),))
        runtime.rollback()


def test_rls_rechecks_revocation_disable_and_history_mode(account, admin):
    reader=account("a.reader")
    with bound(reader) as runtime:
        assert runtime.execute("SELECT id FROM app_core.document_versions WHERE id=1003").fetchone()
        admin.execute("DELETE FROM app_core.version_readers WHERE version_id=1003")
        admin.commit()
        assert runtime.execute("SELECT id FROM app_core.document_versions WHERE id=1003").fetchone() is None
        runtime.execute("SELECT set_config('app.mode','history',true)")
        assert runtime.execute("SELECT id FROM app_core.document_versions WHERE id=1002").fetchone() is None
        admin.execute("UPDATE app_core.users SET is_active=false WHERE id=104")
        admin.commit()
        assert runtime.execute("SELECT * FROM app_core.documents").fetchall()==[]


def test_auth_activity_lock_does_not_block_account_deactivation(account, admin):
    person=account("a.reader")
    with bound(person) as runtime:
        admin.execute("SET LOCAL statement_timeout='2s'")
        admin.execute("UPDATE app_core.users SET is_active=false WHERE id=104")
        admin.execute("DELETE FROM app_core.sessions WHERE user_id=104")
        admin.commit()
        assert runtime.execute("SELECT * FROM app_core.documents").fetchall()==[]


def test_worker_can_only_read_leased_version_and_cannot_mutate_application(account):
    owner=account("a.owner")
    version=new_version(owner)
    with connect(os.environ["WORKER_DATABASE_URL"]) as worker:
        assert worker.execute("SELECT * FROM app_core.document_versions").fetchall()==[]
        job=worker.execute("SELECT * FROM app_core.claim_job()").fetchone()
    with connect(os.environ["WORKER_DATABASE_URL"]) as worker:
        worker.execute("SELECT set_config('app.job_id',%s,true)",(str(job["job_id"]),))
        worker.execute("SELECT set_config('app.job_token',%s,true)",(job["lease_token"],))
        assert {v["id"] for v in worker.execute("SELECT id FROM app_core.document_versions").fetchall()}=={version["version_id"]}
        assert len(worker.execute("SELECT id FROM app_core.jobs").fetchall())==1
        worker.execute("SELECT set_config('app.job_token','forged',true)")
        assert worker.execute("SELECT * FROM app_core.document_versions").fetchall()==[]
        for statement in (
            "SELECT * FROM app_core.auth_candidate('a.owner')",
            "SELECT app_core.change('archive','{\"document_id\":1001}'::jsonb)",
            "UPDATE app_core.document_versions SET state='ready'",
            "SELECT * FROM app_private.runtime_secret",
            "SET ROLE rag_app",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                worker.execute(statement)
            worker.rollback()


@pytest.mark.parametrize("mutation", [
    "UPDATE app_core.users SET is_active=false WHERE id=103",
    "UPDATE app_core.documents SET owner_id=104 WHERE id=1001",
])
def test_worker_revalidates_requester_and_never_activates_stale_work(account, admin, mutation):
    new_version(account("a.owner"))
    with connect(os.environ["WORKER_DATABASE_URL"]) as worker:
        job=worker.execute("SELECT * FROM app_core.claim_job()").fetchone()
    admin.execute(mutation)
    admin.commit()
    with connect(os.environ["WORKER_DATABASE_URL"]) as worker:
        assert not worker.execute("SELECT app_core.finish_job(%s,%s,true) AS ok",
                                  (job["job_id"],job["lease_token"])).fetchone()["ok"]
    assert admin.execute("SELECT current_version_id FROM app_core.documents WHERE id=1001").fetchone()["current_version_id"]==1001
    assert admin.execute("SELECT status FROM app_core.jobs WHERE id=%s",(job["job_id"],)).fetchone()["status"]=="failed"


def test_worker_lease_recovery_rejects_old_token(account, admin):
    version=new_version(account("a.owner"))
    with connect(os.environ["WORKER_DATABASE_URL"]) as worker:
        original=worker.execute("SELECT * FROM app_core.claim_job()").fetchone()
    admin.execute("UPDATE app_core.jobs SET lease_until=now()-interval '1 second'")
    admin.commit()
    with connect(os.environ["WORKER_DATABASE_URL"]) as worker:
        recovered=worker.execute("SELECT * FROM app_core.claim_job()").fetchone()
    assert recovered["lease_token"] != original["lease_token"]
    with connect(os.environ["WORKER_DATABASE_URL"]) as worker:
        assert not worker.execute("SELECT app_core.finish_job(%s,%s,true) AS ok",
                                  (original["job_id"],original["lease_token"])).fetchone()["ok"]
    from app.worker import process_job
    assert process_job(os.environ["WORKER_DATABASE_URL"], recovered)
    with connect(os.environ["WORKER_DATABASE_URL"]) as worker:
        assert not worker.execute("SELECT app_core.finish_job(%s,%s,true) AS ok",
                                  (recovered["job_id"],recovered["lease_token"])).fetchone()["ok"]
    assert admin.execute("SELECT current_version_id FROM app_core.documents WHERE id=1001").fetchone()["current_version_id"]==version["version_id"]


def test_foreign_keys_and_immutable_source(admin):
    for statement in (
        "INSERT INTO app_core.version_readers VALUES(1,1002,204)",
        "INSERT INTO app_core.history_permissions VALUES(1,1001,204,103)",
        "INSERT INTO app_core.history_permissions VALUES(1,1001,104,203)",
        "INSERT INTO app_core.jobs(tenant_id,document_id,version_id,requested_by,base_version_id) VALUES(1,1001,1002,103,1001)",
        "UPDATE app_core.documents SET current_version_id=2001 WHERE id=1001",
        "UPDATE app_core.documents SET current_version_id=1002 WHERE id=1001",
    ):
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            admin.execute(statement)
            admin.execute("SET CONSTRAINTS ALL IMMEDIATE")
        admin.rollback()
    for statement in (
        "UPDATE app_core.document_versions SET content='rewrite' WHERE id=1001",
        "UPDATE app_core.document_versions SET source_hash='rewrite' WHERE id=1001",
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            admin.execute(statement)
        admin.rollback()


def test_all_sensitive_tables_force_rls_and_roles_are_unprivileged(admin):
    tables=admin.execute(
        "SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='app_core' AND c.relkind='r' AND c.relname NOT IN ('schema_migrations','login_limits')"
    ).fetchall()
    assert len(tables)==17
    assert all(r["relrowsecurity"] and r["relforcerowsecurity"] for r in tables)
    assert not any(any(r.values()) for r in admin.execute(
        "SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname IN ('rag_app','rag_worker')"
    ).fetchall())
    assert admin.execute(
        "SELECT count(*) AS n FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname IN ('rag_app','rag_worker')"
    ).fetchone()["n"]==0


def test_m1_upgrade_preserves_content_acl_and_repeatability(database):
    # This database is created exclusively on the guarded disposable test-db host.
    name="rag_upgrade_"+uuid4().hex[:12]
    admin_url=os.environ["DATABASE_ADMIN_URL"]
    upgraded_url=make_conninfo(admin_url,dbname=name)
    with psycopg.connect(admin_url,autocommit=True) as server:
        server.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            with connect(upgraded_url) as old:
                old.execute(Path("/workspace/app/schema.sql").read_text())
                old.execute("INSERT INTO app_core.tenants VALUES(1,'保留公司')")
                password_hash=PASSWORD_HASHER.hash("unchanged-password")
                for uid,username in ((901,"kept.owner"),(902,"kept.reader")):
                    old.execute("INSERT INTO app_core.users(id,tenant_id,username,display_name,password_hash,role) "
                                "VALUES(%s,1,%s,%s,%s,'employee')",(uid,username,username,password_hash))
                old.execute("INSERT INTO app_core.documents VALUES(9001,1,901,'保留文件','自訂分類','原始內容：甲乙丙','selected','ready')")
                old.execute("INSERT INTO app_core.document_readers VALUES(1,9001,902)")
            initialize(upgraded_url,os.environ["RUNTIME_DB_PASSWORD"])
            initialize(upgraded_url,os.environ["RUNTIME_DB_PASSWORD"])
            with connect(upgraded_url) as upgraded:
                assert upgraded.execute("SELECT content FROM app_core.document_versions WHERE id=9001").fetchone()["content"]=="原始內容：甲乙丙"
                assert upgraded.execute("SELECT password_hash FROM app_core.users WHERE id=901").fetchone()["password_hash"]==password_hash
                assert upgraded.execute("SELECT user_id FROM app_core.version_readers WHERE version_id=9001").fetchone()["user_id"]==902
                assert upgraded.execute("SELECT count(*) AS n FROM app_core.schema_migrations").fetchone()["n"]==4
                assert upgraded.execute("SELECT to_regclass('app_core.document_readers') AS name").fetchone()["name"] is None
        finally:
            server.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
