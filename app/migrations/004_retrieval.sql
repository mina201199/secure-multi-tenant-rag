CREATE EXTENSION IF NOT EXISTS vector;
CREATE SEQUENCE app_core.upload_document_id START 2000000000;
ALTER TABLE app_core.jobs ADD COLUMN kind text NOT NULL DEFAULT 'version' CHECK(kind IN ('version','index'));
ALTER TABLE app_core.jobs ADD COLUMN stage text NOT NULL DEFAULT 'queued';

-- Original bytes never have a public path; downloads must pass current ACL checks.
CREATE TABLE app_core.source_files (
 tenant_id bigint NOT NULL,
 document_id bigint NOT NULL,
 version_id bigint PRIMARY KEY,
 filename text NOT NULL CHECK(length(filename) BETWEEN 1 AND 180),
 media_type text NOT NULL CHECK(media_type IN ('application/pdf','text/plain')),
 body bytea NOT NULL CHECK(octet_length(body) BETWEEN 1 AND 10485760),
 sha256 text NOT NULL CHECK(sha256=encode(public.digest(body,'sha256'),'hex')),
 FOREIGN KEY(tenant_id,document_id,version_id) REFERENCES app_core.document_versions(tenant_id,document_id,id)
);
CREATE TABLE app_core.parsed_sources (
 tenant_id bigint NOT NULL,
 document_id bigint NOT NULL,
 version_id bigint PRIMARY KEY,
 content text NOT NULL CHECK(octet_length(content) BETWEEN 1 AND 1048576),
 locations jsonb NOT NULL CHECK(jsonb_typeof(locations)='array'),
 parser text NOT NULL,
 FOREIGN KEY(tenant_id,document_id,version_id) REFERENCES app_core.document_versions(tenant_id,document_id,id)
);
CREATE TABLE app_core.chunks (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 tenant_id bigint NOT NULL,
 document_id bigint NOT NULL,
 version_id bigint NOT NULL,
 ordinal integer NOT NULL CHECK(ordinal BETWEEN 0 AND 63),
 location_kind text NOT NULL CHECK(location_kind IN ('page','paragraph')),
 location_no integer NOT NULL CHECK(location_no>0),
 start_offset integer NOT NULL CHECK(start_offset>=0),
 end_offset integer NOT NULL CHECK(end_offset>start_offset),
 content text NOT NULL,
 tokens integer NOT NULL CHECK(tokens BETWEEN 1 AND 600),
 profile text NOT NULL,
 embedding public.vector(1536) NOT NULL,
 UNIQUE(version_id,ordinal),
 FOREIGN KEY(tenant_id,document_id,version_id) REFERENCES app_core.document_versions(tenant_id,document_id,id)
);
CREATE INDEX ON app_core.chunks(tenant_id,version_id);
CREATE TABLE app_core.index_manifests (
 tenant_id bigint NOT NULL,
 document_id bigint NOT NULL,
 version_id bigint PRIMARY KEY,
 profile text NOT NULL,
 chunk_count integer NOT NULL CHECK(chunk_count BETWEEN 1 AND 64),
 indexed_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(tenant_id,document_id,version_id) REFERENCES app_core.document_versions(tenant_id,document_id,id)
);
CREATE TABLE app_core.model_usage (
 id uuid PRIMARY KEY,
 tenant_id bigint NOT NULL REFERENCES app_core.tenants(id),
 actor_id bigint NOT NULL,
 job_id bigint REFERENCES app_core.jobs(id),
 credential_hash text NOT NULL,
 operation text NOT NULL CHECK(operation IN ('embedding','answer')),
 model text NOT NULL,
 price_revision text NOT NULL DEFAULT '2026-10-21-usd40-buffer1.25',
 month date NOT NULL DEFAULT date_trunc('month',now() AT TIME ZONE 'Asia/Taipei')::date,
 reserved_microtwd bigint NOT NULL CHECK(reserved_microtwd>0),
 charged_microtwd bigint NOT NULL CHECK(charged_microtwd>=0 AND charged_microtwd<=reserved_microtwd),
 status text NOT NULL DEFAULT 'reserved' CHECK(status IN ('reserved','settled','uncertain')),
 input_tokens integer,
 output_tokens integer,
 elapsed_ms integer,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES app_core.users(tenant_id,id)
);
CREATE INDEX ON app_core.model_usage(month,status);
-- Existing readable content remains readable during backfill. Only a complete index is searchable.
INSERT INTO app_core.jobs(tenant_id,document_id,version_id,requested_by,base_version_id,kind)
 SELECT v.tenant_id,v.document_id,v.id,d.owner_id,v.id,'index'
 FROM app_core.document_versions v JOIN app_core.documents d ON d.current_version_id=v.id
 JOIN app_core.users u ON u.id=d.owner_id
 WHERE d.status='ready' AND v.state='ready' AND u.is_active
 ON CONFLICT(version_id) DO NOTHING;

CREATE FUNCTION app_private.immutable_source() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'immutable source' USING ERRCODE='23514'; END $$;
CREATE TRIGGER immutable_source BEFORE UPDATE OR DELETE ON app_core.source_files
 FOR EACH ROW EXECUTE FUNCTION app_private.immutable_source();
