"""僅閱讀目前有效版本，ACL 綁定版本；RLS 留待後續 migration。"""

# 清單與詳細頁共用同一段授權條件，避免兩邊條件不一致而出現「清單看不到、猜 ID 卻打得開」
VISIBLE_DOCUMENTS = """
    FROM app_core.documents d
    JOIN app_core.document_versions v
      ON v.tenant_id = d.tenant_id AND v.document_id = d.id AND v.id = d.current_version_id
    WHERE d.tenant_id = %(tenant_id)s AND d.status = 'ready'
      AND v.status = 'ready'
      AND (v.owner_id = %(user_id)s OR v.visibility = 'company'
           OR EXISTS (
               SELECT 1 FROM app_core.version_readers r
               WHERE r.tenant_id = v.tenant_id AND r.version_id = v.id
                 AND r.user_id = %(user_id)s
           ))
"""

SUMMARY_COLUMNS = "SELECT d.id, d.title, d.category, v.visibility, v.owner_id, v.id AS version_id, v.version_number"


def list_documents(connection, identity):
    # tenant_id 與 user_id 一律取自 Session 對應的身分，不接受使用者傳入的值
    return connection.execute(
        SUMMARY_COLUMNS + VISIBLE_DOCUMENTS + " ORDER BY d.title, d.id",
        {"tenant_id": identity["tenant_id"], "user_id": identity["id"]},
    ).fetchall()


def get_document(connection, identity, document_id):
    return connection.execute(
        SUMMARY_COLUMNS + ", v.content" + VISIBLE_DOCUMENTS + " AND d.id = %(document_id)s",
        {"tenant_id": identity["tenant_id"], "user_id": identity["id"], "document_id": document_id},
    ).fetchone()
