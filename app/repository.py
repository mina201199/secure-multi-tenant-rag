"""Explicit API authorization plus database RLS; current-version queries never fall back."""

VISIBLE_DOCUMENTS = """
    SELECT d.id, d.title, d.category, v.visibility, d.owner_id, v.id AS version_id
    FROM app_core.documents d JOIN app_core.document_versions v
      ON v.id=d.current_version_id AND v.tenant_id=d.tenant_id AND v.document_id=d.id
    WHERE d.tenant_id = %(tenant_id)s AND d.status = 'ready' AND v.state='ready'
      AND (v.owner_id = %(user_id)s OR v.visibility = 'company'
           OR (v.visibility = 'selected' AND EXISTS (
               SELECT 1 FROM app_core.version_readers r
               WHERE r.tenant_id = d.tenant_id AND r.version_id = v.id
                 AND r.user_id = %(user_id)s
           )))
"""


def list_documents(connection, identity):
    return connection.execute(
        VISIBLE_DOCUMENTS + " ORDER BY d.title, d.id",
        {"tenant_id": identity["tenant_id"], "user_id": identity["id"]},
    ).fetchall()


def get_document(connection, identity, document_id):
    return connection.execute(
        "SELECT visible.*, coalesce(s.content,v.content) AS content FROM (" + VISIBLE_DOCUMENTS + " AND d.id = %(document_id)s) visible "
        "JOIN app_core.document_versions v ON v.id = visible.version_id "
        "LEFT JOIN app_core.parsed_sources s ON s.version_id=v.id",
        {"tenant_id": identity["tenant_id"], "user_id": identity["id"], "document_id": document_id},
    ).fetchone()
