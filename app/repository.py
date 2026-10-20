"""Milestone 1: authorization is explicit SQL at the API layer, not RLS yet."""

VISIBLE_DOCUMENTS = """
    SELECT d.id, d.title, d.category, d.visibility, d.owner_id
    FROM app_core.documents d
    WHERE d.tenant_id = %(tenant_id)s AND d.status = 'ready'
      AND (d.owner_id = %(user_id)s OR d.visibility = 'company'
           OR EXISTS (
               SELECT 1 FROM app_core.document_readers r
               WHERE r.tenant_id = d.tenant_id AND r.document_id = d.id
                 AND r.user_id = %(user_id)s
           ))
"""


def list_documents(connection, identity):
    return connection.execute(
        VISIBLE_DOCUMENTS + " ORDER BY d.title, d.id",
        {"tenant_id": identity["tenant_id"], "user_id": identity["id"]},
    ).fetchall()


def get_document(connection, identity, document_id):
    return connection.execute(
        "SELECT v.*, d.content FROM (" + VISIBLE_DOCUMENTS + " AND d.id = %(document_id)s) v "
        "JOIN app_core.documents d ON d.id = v.id AND d.tenant_id = %(tenant_id)s",
        {"tenant_id": identity["tenant_id"], "user_id": identity["id"], "document_id": document_id},
    ).fetchone()
