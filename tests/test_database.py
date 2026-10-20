import psycopg
import pytest

from app import bootstrap


def test_runtime_role_has_no_bypass_privileges(runtime_db):
    row = runtime_db.execute(
        """SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb
           FROM pg_roles WHERE rolname = current_user"""
    ).fetchone()
    assert row == {"rolsuper": False, "rolbypassrls": False, "rolcreaterole": False, "rolcreatedb": False}


@pytest.mark.parametrize("statement", [
    "INSERT INTO app_core.documents (tenant_id, title, category, owner_id) VALUES (1, 'x', 'x', 1)",
    "UPDATE app_core.users SET role = 'admin'",
    "UPDATE app_core.version_readers SET user_id = user_id",
    "UPDATE app_core.document_versions SET content = 'tampered'",
    "CREATE TABLE app_core.runtime_should_not_create (id int)",
    "SELECT * FROM app_core.schema_migrations",
])
def test_runtime_role_cannot_change_business_data_or_schema(runtime_db, statement):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        runtime_db.execute(statement)


def test_cross_tenant_acl_is_rejected_by_foreign_key(admin_db):
    document_id, other_user_id, tenant_id = admin_db.execute(
        """SELECT d.id, u.id, d.tenant_id
           FROM app_core.document_versions d, app_core.users u
           WHERE d.tenant_id <> u.tenant_id LIMIT 1"""
    ).fetchone()
    # 即使把 tenant_id 寫成文件所屬公司，複合外鍵也會發現使用者不屬於這家公司
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        admin_db.execute(
            "INSERT INTO app_core.version_readers (tenant_id, version_id, user_id) VALUES (%s, %s, %s)",
            (tenant_id, document_id, other_user_id),
        )


def test_uppercase_username_is_rejected(admin_db):
    with pytest.raises(psycopg.errors.CheckViolation):
        admin_db.execute(
            """INSERT INTO app_core.users (tenant_id, username, display_name, password_hash, role)
               SELECT id, 'Mixed.Case', 'x', 'x', 'employee' FROM app_core.tenants LIMIT 1"""
        )


def test_bootstrap_is_repeatable(admin_db):
    count_query = """SELECT (SELECT count(*) FROM app_core.tenants), (SELECT count(*) FROM app_core.users),
                            (SELECT count(*) FROM app_core.documents), (SELECT count(*) FROM app_core.version_readers),
                            (SELECT count(*) FROM app_core.document_versions),
                            (SELECT count(*) FROM app_core.schema_migrations)"""
    before = admin_db.execute(count_query).fetchone()
    bootstrap.main()
    assert admin_db.execute(count_query).fetchone() == before
