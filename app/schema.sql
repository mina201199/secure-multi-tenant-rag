CREATE SCHEMA IF NOT EXISTS app_core;
REVOKE ALL ON SCHEMA app_core FROM PUBLIC;

CREATE TABLE IF NOT EXISTS app_core.tenants (
    id bigint PRIMARY KEY,
    name text NOT NULL
);
CREATE TABLE IF NOT EXISTS app_core.users (
    id bigint PRIMARY KEY,
    tenant_id bigint NOT NULL REFERENCES app_core.tenants(id),
    username text NOT NULL UNIQUE CHECK (username = lower(username)),
    display_name text NOT NULL,
    password_hash text NOT NULL,
    role text NOT NULL CHECK (role IN ('admin', 'employee')),
    is_active boolean NOT NULL DEFAULT true,
    manager_id bigint,
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, manager_id) REFERENCES app_core.users(tenant_id, id)
);
CREATE TABLE IF NOT EXISTS app_core.sessions (
    token_hash text PRIMARY KEY,
    user_id bigint NOT NULL REFERENCES app_core.users(id),
    created_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON app_core.sessions(user_id);
CREATE TABLE IF NOT EXISTS app_core.login_limits (
    key_hash text PRIMARY KEY,
    started_at timestamptz NOT NULL,
    attempts integer NOT NULL CHECK (attempts > 0)
);
CREATE TABLE IF NOT EXISTS app_core.documents (
    id bigint PRIMARY KEY,
    tenant_id bigint NOT NULL REFERENCES app_core.tenants(id),
    owner_id bigint NOT NULL,
    title text NOT NULL,
    category text NOT NULL,
    content text NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('private', 'company', 'selected')),
    status text NOT NULL CHECK (status IN ('ready', 'processing', 'failed', 'archived')),
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, owner_id) REFERENCES app_core.users(tenant_id, id)
);
CREATE TABLE IF NOT EXISTS app_core.document_readers (
    tenant_id bigint NOT NULL,
    document_id bigint NOT NULL,
    user_id bigint NOT NULL,
    PRIMARY KEY (tenant_id, document_id, user_id),
    FOREIGN KEY (tenant_id, document_id) REFERENCES app_core.documents(tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES app_core.users(tenant_id, id)
);
