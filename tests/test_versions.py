"""M2-01：版本資料與授權切換，直接使用真實 PostgreSQL。"""
import os
import uuid

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from app import bootstrap
from tests.helpers import login


@pytest.fixture
def versioned_document(admin_db):
    tenant_id, owner_id = admin_db.execute(
        "SELECT tenant_id, id FROM app_core.users WHERE username = 'a.owner'"
    ).fetchone()
    reader_id = admin_db.execute(
        "SELECT id FROM app_core.users WHERE username = 'a.reader'"
    ).fetchone()[0]
    document_id = admin_db.execute(
        """INSERT INTO app_core.documents (tenant_id, title, category, owner_id)
           VALUES (%s, '版本測試', '測試', %s) RETURNING id""", (tenant_id, owner_id)
    ).fetchone()[0]
    version_id = admin_db.execute(
        """INSERT INTO app_core.document_versions
           (tenant_id, document_id, version_number, owner_id, content, visibility, status)
           VALUES (%s, %s, 1, %s, '舊版內容', 'private', 'ready') RETURNING id""",
        (tenant_id, document_id, owner_id),
    ).fetchone()[0]
    admin_db.execute(
        "UPDATE app_core.documents SET current_version_id = %s WHERE id = %s",
        (version_id, document_id),
    )
    admin_db.execute(
        "INSERT INTO app_core.version_readers (tenant_id, version_id, user_id) VALUES (%s, %s, %s)",
        (tenant_id, version_id, reader_id),
    )
    try:
        yield tenant_id, document_id, owner_id, reader_id, version_id
    finally:
        admin_db.execute("UPDATE app_core.documents SET current_version_id = NULL WHERE id = %s", (document_id,))
        admin_db.execute(
            "DELETE FROM app_core.version_readers WHERE version_id IN "
            "(SELECT id FROM app_core.document_versions WHERE document_id = %s)", (document_id,)
        )
        admin_db.execute("DELETE FROM app_core.document_versions WHERE document_id = %s", (document_id,))
        admin_db.execute("DELETE FROM app_core.documents WHERE id = %s", (document_id,))


def add_version(admin_db, document, status='ready'):
    tenant_id, document_id, owner_id, _, _ = document
    return admin_db.execute(
        """INSERT INTO app_core.document_versions
           (tenant_id, document_id, version_number, owner_id, content, status)
           VALUES (%s, %s, 2, %s, '新版內容', %s) RETURNING id""",
        (tenant_id, document_id, owner_id, status),
    ).fetchone()[0]


@pytest.mark.parametrize('status', ['queued', 'processing', 'failed'])
def test_unpublished_version_keeps_current_content(client, admin_db, versioned_document, status):
    add_version(admin_db, versioned_document, status)
    login(client, 'a.reader')
    response = client.get(f'/api/documents/{versioned_document[1]}')
    assert response.status_code == 200
    assert response.json()['content'] == '舊版內容'


def test_new_version_is_private_and_does_not_inherit_acl(client, admin_db, versioned_document):
    version_id = add_version(admin_db, versioned_document)
    row = admin_db.execute(
        "SELECT visibility FROM app_core.document_versions WHERE id = %s", (version_id,)
    ).fetchone()
    assert row == ('private',)
    assert admin_db.execute(
        "SELECT count(*) FROM app_core.version_readers WHERE version_id = %s", (version_id,)
    ).fetchone() == (0,)
    login(client, 'a.reader')
    document_id = versioned_document[1]
    assert client.get(f'/api/documents/{document_id}').status_code == 200
    admin_db.execute(
        "UPDATE app_core.documents SET current_version_id = %s WHERE id = %s", (version_id, document_id)
    )
    # 舊版 ACL 仍存在，也不能回退舊版；API 和 HTML 都必須立即拒絕。
    assert client.get(f'/api/documents/{document_id}').status_code == 404
    assert client.get(f'/documents/{document_id}').status_code == 404
    assert document_id not in [d['id'] for d in client.get('/api/documents').json()]
    client.cookies.clear()
    login(client, 'a.owner')
    assert client.get(f'/api/documents/{document_id}').json()['content'] == '新版內容'


def test_acl_revocation_applies_next_request(client, admin_db, versioned_document):
    login(client, 'a.reader')
    document_id = versioned_document[1]
    assert client.get(f'/api/documents/{document_id}').status_code == 200
    admin_db.execute("DELETE FROM app_core.version_readers WHERE version_id = %s", (versioned_document[4],))
    assert client.get(f'/api/documents/{document_id}').status_code == 404


@pytest.mark.parametrize('state', ['queued', 'processing', 'failed', 'archived', 'no_current'])
def test_unavailable_current_version_is_hidden_even_from_owner(client, admin_db, versioned_document, state):
    document_id, version_id = versioned_document[1], versioned_document[4]
    if state == 'no_current':
        admin_db.execute('UPDATE app_core.documents SET current_version_id = NULL WHERE id = %s', (document_id,))
    else:
        admin_db.execute('UPDATE app_core.document_versions SET status = %s WHERE id = %s', (state, version_id))
    login(client, 'a.owner')
    assert client.get(f'/api/documents/{document_id}').status_code == 404
    assert document_id not in [d['id'] for d in client.get('/api/documents').json()]


@pytest.mark.parametrize('other_company', [False, True])
def test_current_version_cannot_point_to_another_document(admin_db, versioned_document, other_company):
    operator = '<>' if other_company else '='
    target = admin_db.execute(
        f"""SELECT id FROM app_core.document_versions WHERE tenant_id {operator} %s
            AND document_id <> %s LIMIT 1""", versioned_document[:2]
    ).fetchone()[0]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        admin_db.execute(
            "UPDATE app_core.documents SET current_version_id = %s WHERE id = %s",
            (target, versioned_document[1]),
        )


def test_version_owner_cannot_be_from_other_company(admin_db, versioned_document):
    other_user = admin_db.execute("SELECT id FROM app_core.users WHERE username = 'b.owner'").fetchone()[0]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        admin_db.execute(
            "UPDATE app_core.document_versions SET owner_id = %s WHERE id = %s",
            (other_user, versioned_document[4]),
        )


def test_m1_database_upgrade_preserves_content_and_acl(admin_db):
    # 獨立測試庫重現「已部署 M1，再套 M2」，也驗證 fresh install 之外的升級路徑。
    database_name = 'test_upgrade_' + uuid.uuid4().hex
    admin_db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database_name)))
    try:
        with psycopg.connect(os.environ['DATABASE_ADMIN_URL'], dbname=database_name) as connection:
            connection.execute('CREATE SCHEMA app_core')
            connection.execute((bootstrap.MIGRATIONS_DIR / '001_m1_core.sql').read_text(encoding='utf-8'))
            connection.execute(
                """CREATE TABLE app_core.schema_migrations (
                    version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());
                    INSERT INTO app_core.schema_migrations (version) VALUES ('001_m1_core');
                    INSERT INTO app_core.tenants (name) VALUES ('升級公司');
                    INSERT INTO app_core.users (tenant_id, username, display_name, password_hash, role)
                    VALUES (1, 'upgrade.owner', 'owner', 'hash', 'employee'),
                           (1, 'upgrade.reader', 'reader', 'hash', 'employee');
                    INSERT INTO app_core.documents (tenant_id, title, category, owner_id, visibility, content)
                    VALUES (1, '原文件', '測試', 1, 'private', '不可遺失的原文');
                    INSERT INTO app_core.document_readers VALUES (1, 1, 2)"""
            )
            bootstrap.apply_migrations(connection)
            assert connection.execute(
                """SELECT v.content, v.visibility, v.owner_id, v.version_number, r.user_id
                   FROM app_core.documents d JOIN app_core.document_versions v ON v.id = d.current_version_id
                   JOIN app_core.version_readers r ON r.version_id = v.id"""
            ).fetchall() == [('不可遺失的原文', 'private', 1, 1, 2)]
            bootstrap.apply_migrations(connection)
            assert connection.execute('SELECT count(*) FROM app_core.document_versions').fetchone() == (1,)
    finally:
        admin_db.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(database_name)))


def test_fresh_install_seeds_versions_and_is_repeatable(admin_db, monkeypatch):
    database_name = 'test_fresh_' + uuid.uuid4().hex
    admin_db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database_name)))
    try:
        with psycopg.connect(os.environ['DATABASE_ADMIN_URL'], dbname=database_name) as connection:
            # Connection.info.dsn 會移除密碼；保留原環境憑證，只替換測試庫名稱。
            monkeypatch.setenv('DATABASE_ADMIN_URL', make_conninfo(
                os.environ['DATABASE_ADMIN_URL'], dbname=database_name
            ))
            bootstrap.main()
            bootstrap.main()
            assert connection.execute(
                "SELECT count(*), count(current_version_id) FROM app_core.documents"
            ).fetchone() == (8, 8)
            assert connection.execute("SELECT count(*) FROM app_core.document_versions").fetchone() == (8,)
            assert connection.execute("SELECT count(*) FROM app_core.version_readers").fetchone() == (2,)
    finally:
        admin_db.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(database_name)))
