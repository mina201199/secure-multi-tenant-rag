-- M1 核心資料表：只滿足登入與文件閱讀；版本、交接與 RLS 留給 M2 的後續 migration
-- 由 app.bootstrap 以資料庫 owner 身分執行一次，runtime 角色只取得下方明確授予的權限

CREATE TABLE app_core.tenants (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL UNIQUE
);

CREATE TABLE app_core.users (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id bigint NOT NULL REFERENCES app_core.tenants (id),
    -- 登入時只輸入帳號、不選公司，所以帳號必須全平台唯一
    -- 程式以 casefold 後的值查詢，這裡強制存小寫，避免大小寫不同而永遠登不進去
    username text NOT NULL UNIQUE
        CHECK (username = lower(username) AND length(username) BETWEEN 1 AND 64),
    display_name text NOT NULL,
    password_hash text NOT NULL,
    role text NOT NULL CHECK (role IN ('admin', 'employee')),
    is_active boolean NOT NULL DEFAULT true,
    manager_id bigint,
    -- 供複合外鍵使用，讓主管、文件擁有者、ACL 都無法指向別家公司的帳號
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, manager_id) REFERENCES app_core.users (tenant_id, id),
    CHECK (manager_id IS NULL OR manager_id <> id)
);

CREATE TABLE app_core.sessions (
    -- 只存 token 的 SHA-256；資料庫外洩時無法直接拿來登入
    token_hash text PRIMARY KEY,
    user_id bigint NOT NULL REFERENCES app_core.users (id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL
);

CREATE INDEX sessions_user_id_idx ON app_core.sessions (user_id);

CREATE TABLE app_core.login_limits (
    key_hash text PRIMARY KEY,
    started_at timestamptz NOT NULL,
    attempts integer NOT NULL CHECK (attempts > 0)
);

CREATE TABLE app_core.documents (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id bigint NOT NULL REFERENCES app_core.tenants (id),
    title text NOT NULL,
    category text NOT NULL,
    visibility text NOT NULL DEFAULT 'private' CHECK (visibility IN ('private', 'company')),
    owner_id bigint NOT NULL,
    -- 先列出 README 定義的處理狀態；M1 只有 ready 會出現在前台
    status text NOT NULL DEFAULT 'ready'
        CHECK (status IN ('queued', 'processing', 'ready', 'failed', 'archived')),
    content text NOT NULL,
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, owner_id) REFERENCES app_core.users (tenant_id, id)
);

CREATE TABLE app_core.document_readers (
    tenant_id bigint NOT NULL,
    document_id bigint NOT NULL,
    user_id bigint NOT NULL,
    PRIMARY KEY (document_id, user_id),
    -- 兩個複合外鍵都帶 tenant_id，資料庫層直接拒絕跨公司授權
    FOREIGN KEY (tenant_id, document_id) REFERENCES app_core.documents (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES app_core.users (tenant_id, id)
);

CREATE INDEX document_readers_user_idx ON app_core.document_readers (tenant_id, user_id);

-- runtime 角色只拿到 M1 必要權限：文件與帳號唯讀，只有 Session 與登入限流可寫
GRANT USAGE ON SCHEMA app_core TO rag_app;
GRANT SELECT ON app_core.tenants, app_core.users, app_core.documents, app_core.document_readers TO rag_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON app_core.sessions, app_core.login_limits TO rag_app;
