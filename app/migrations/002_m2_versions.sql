-- M2-01：保留 M1 文件 ID、內容與 ACL，改為明確版本。
CREATE TABLE app_core.document_versions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id bigint NOT NULL,
    document_id bigint NOT NULL,
    version_number integer NOT NULL CHECK (version_number > 0),
    owner_id bigint NOT NULL,
    visibility text NOT NULL DEFAULT 'private' CHECK (visibility IN ('private', 'company')),
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'processing', 'ready', 'failed', 'archived')),
    content text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, document_id, id),
    UNIQUE (document_id, version_number),
    FOREIGN KEY (tenant_id, document_id) REFERENCES app_core.documents (tenant_id, id),
    FOREIGN KEY (tenant_id, owner_id) REFERENCES app_core.users (tenant_id, id)
);

CREATE TABLE app_core.version_readers (
    tenant_id bigint NOT NULL,
    version_id bigint NOT NULL,
    user_id bigint NOT NULL,
    PRIMARY KEY (version_id, user_id),
    FOREIGN KEY (tenant_id, version_id) REFERENCES app_core.document_versions (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES app_core.users (tenant_id, id)
);
CREATE INDEX version_readers_user_idx ON app_core.version_readers (tenant_id, user_id);

ALTER TABLE app_core.documents ADD COLUMN current_version_id bigint;
-- 三欄外鍵同時限制公司、文件與版本，不能指到同公司的另一份文件。
ALTER TABLE app_core.documents ADD CONSTRAINT documents_current_version_fk
    FOREIGN KEY (tenant_id, id, current_version_id)
    REFERENCES app_core.document_versions (tenant_id, document_id, id);

INSERT INTO app_core.document_versions
    (tenant_id, document_id, version_number, owner_id, visibility, status, content)
SELECT tenant_id, id, 1, owner_id, visibility, status, content FROM app_core.documents;

UPDATE app_core.documents d SET current_version_id = v.id
FROM app_core.document_versions v WHERE v.document_id = d.id;

INSERT INTO app_core.version_readers (tenant_id, version_id, user_id)
SELECT r.tenant_id, v.id, r.user_id FROM app_core.document_readers r
JOIN app_core.document_versions v ON v.tenant_id = r.tenant_id AND v.document_id = r.document_id;

-- 移除舊資料來源，避免後續功能誤用文件層 ACL 或正文。
DROP TABLE app_core.document_readers;
ALTER TABLE app_core.documents DROP COLUMN visibility, DROP COLUMN content;

GRANT SELECT ON app_core.document_versions, app_core.version_readers TO rag_app;
-- M2 寫入 API 尚未開放，runtime 維持唯讀。
