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


def owned_current_version(connection, identity, document_id, lock=False):
    # Version writes serialize on the version row; future lifecycle writers must lock this row too.
    return connection.execute(
        """SELECT d.id, d.title, v.id AS version_id, v.version_number, v.visibility
           FROM app_core.documents d JOIN app_core.document_versions v
             ON v.tenant_id = d.tenant_id AND v.document_id = d.id AND v.id = d.current_version_id
           WHERE d.id = %s AND d.tenant_id = %s AND d.status = 'ready'
             AND v.status = 'ready' AND v.owner_id = %s"""
        + (" FOR UPDATE OF v" if lock else ""),
        (document_id, identity['tenant_id'], identity['id']),
    ).fetchone()


def sharing_readers(connection, identity, version_id):
    return [row['user_id'] for row in connection.execute(
        'SELECT user_id FROM app_core.version_readers WHERE tenant_id = %s AND version_id = %s ORDER BY user_id',
        (identity['tenant_id'], version_id),
    ).fetchall()]


def active_sharing_candidates(connection, identity):
    return connection.execute(
        """SELECT id, username, display_name FROM app_core.users
           WHERE tenant_id = %s AND is_active = true AND id <> %s ORDER BY username""",
        (identity['tenant_id'], identity['id']),
    ).fetchall()


def replace_sharing(connection, identity, document_id, version_id, mode, reader_ids):
    from fastapi import HTTPException

    lockedVersion = owned_current_version(connection, identity, document_id, lock=True)
    # A query that waited for v can still hold the old d snapshot. Recheck in a
    # new READ COMMITTED statement while the outgoing version lock remains held.
    current = owned_current_version(connection, identity, document_id) if lockedVersion is not None else None
    if current is None:
        raise HTTPException(404, '找不到文件')
    if current['version_id'] != lockedVersion['version_id'] or current['version_id'] != version_id:
        raise HTTPException(409, '有效版本已變更，請重新載入分享設定')
    readers = sorted(set(reader_ids))
    if (mode == 'specified' and not readers) or (mode != 'specified' and readers):
        raise HTTPException(422, '指定人員模式須選取閱讀者；其他模式不可包含閱讀者')
    if readers:
        # Account activity is validated on grant and again on every reader request.
        valid = connection.execute(
            """SELECT id FROM app_core.users WHERE tenant_id = %s AND is_active = true
               AND id <> %s AND id = ANY(%s) ORDER BY id""",
            (identity['tenant_id'], identity['id'], readers),
        ).fetchall()
        if len(valid) != len(readers):
            raise HTTPException(422, '閱讀者必須為本公司其他有效帳號')
    connection.execute(
        'DELETE FROM app_core.version_readers WHERE tenant_id = %s AND version_id = %s',
        (identity['tenant_id'], version_id),
    )
    for user_id in readers:
        connection.execute(
            'INSERT INTO app_core.version_readers (tenant_id, version_id, user_id) VALUES (%s, %s, %s)',
            (identity['tenant_id'], version_id, user_id),
        )
    visibility = 'company' if mode == 'company' else 'private'
    connection.execute(
        'UPDATE app_core.document_versions SET visibility = %s WHERE tenant_id = %s AND id = %s',
        (visibility, identity['tenant_id'], version_id),
    )
    return {'document_id': document_id, 'version_id': version_id, 'mode': mode, 'reader_ids': readers}
