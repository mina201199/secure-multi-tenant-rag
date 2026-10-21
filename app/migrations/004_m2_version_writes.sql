-- TXT-only durable queue. The web role never owns tables or publishes versions.
DO $roles$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'rag_version_writer') THEN
        CREATE ROLE rag_version_writer;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'rag_version_processor') THEN
        CREATE ROLE rag_version_processor;
    END IF;
END $roles$;
ALTER ROLE rag_version_writer WITH NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE rag_version_processor WITH NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

ALTER TABLE app_core.document_versions ALTER COLUMN content DROP NOT NULL;
ALTER TABLE app_core.document_versions ADD CONSTRAINT version_ready_has_content
    CHECK (status <> 'ready' OR (content IS NOT NULL AND length(btrim(content)) > 0));

CREATE TABLE app_core.version_sources (
    tenant_id bigint NOT NULL,
    document_id bigint NOT NULL,
    version_id bigint PRIMARY KEY,
    source_bytes bytea NOT NULL CHECK (octet_length(source_bytes) BETWEEN 1 AND 1048576),
    source_sha256 text GENERATED ALWAYS AS (encode(sha256(source_bytes), 'hex')) STORED,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, document_id, version_id),
    FOREIGN KEY (tenant_id, document_id, version_id)
        REFERENCES app_core.document_versions (tenant_id, document_id, id)
);
CREATE TABLE app_core.version_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id bigint NOT NULL,
    document_id bigint NOT NULL,
    version_id bigint NOT NULL UNIQUE,
    actor_id bigint NOT NULL,
    expected_version_id bigint NOT NULL,
    idempotency_key text NOT NULL CHECK (idempotency_key ~ '^[A-Za-z0-9_-]{16,128}$'),
    request_fingerprint text NOT NULL,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'processing', 'ready', 'failed')),
    lease_token uuid,
    lease_expires_at timestamptz,
    error_code text CHECK (error_code IN ('processing_failed', 'lease_expired', 'archived',
        'actor_inactive', 'ownership_changed', 'base_conflict', 'invalid_source', 'version_unavailable')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, actor_id, document_id, idempotency_key),
    FOREIGN KEY (tenant_id, document_id, version_id)
        REFERENCES app_core.version_sources (tenant_id, document_id, version_id),
    FOREIGN KEY (tenant_id, document_id, expected_version_id)
        REFERENCES app_core.document_versions (tenant_id, document_id, id),
    FOREIGN KEY (tenant_id, actor_id) REFERENCES app_core.users (tenant_id, id),
    CHECK (status <> 'processing' OR (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL))
);
CREATE UNIQUE INDEX version_jobs_one_pending ON app_core.version_jobs (tenant_id, document_id)
    WHERE status IN ('queued', 'processing');

GRANT USAGE ON SCHEMA app_core TO rag_version_writer, rag_version_processor;
GRANT SELECT (id, tenant_id, is_active) ON app_core.users TO rag_version_writer;
GRANT SELECT ON app_core.documents, app_core.document_versions,
    app_core.version_sources, app_core.version_jobs TO rag_version_writer;
-- PostgreSQL row locks require UPDATE privilege on at least one column.
GRANT UPDATE (current_version_id) ON app_core.documents TO rag_version_writer;
GRANT INSERT (tenant_id, document_id, version_number, owner_id, visibility, status, content),
    UPDATE (status, content) ON app_core.document_versions TO rag_version_writer;
GRANT USAGE ON SEQUENCE app_core.document_versions_id_seq TO rag_version_writer;
GRANT INSERT (tenant_id, document_id, version_id, source_bytes) ON app_core.version_sources TO rag_version_writer;
GRANT INSERT (tenant_id, document_id, version_id, actor_id, expected_version_id,
    idempotency_key, request_fingerprint), UPDATE (status, lease_token, lease_expires_at, error_code)
    ON app_core.version_jobs TO rag_version_writer;

CREATE FUNCTION app_core.immutable_version_source() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, app_core, pg_temp AS $$
BEGIN
    RAISE EXCEPTION 'immutable source' USING ERRCODE = '22023';
END $$;
CREATE TRIGGER version_source_immutable BEFORE UPDATE ON app_core.version_sources
    FOR EACH ROW EXECUTE FUNCTION app_core.immutable_version_source();

CREATE FUNCTION app_core.validate_version_txt(p_bytes bytea) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app_core, pg_temp AS $$
DECLARE body text;
BEGIN
    IF p_bytes IS NULL OR octet_length(p_bytes) = 0 THEN
        RAISE EXCEPTION 'invalid TXT' USING ERRCODE = '22023';
    END IF;
    IF octet_length(p_bytes) > 1048576 THEN
        RAISE EXCEPTION 'TXT too large' USING ERRCODE = '54000';
    END IF;
    BEGIN
        body := convert_from(p_bytes, 'UTF8');
    EXCEPTION WHEN character_not_in_repertoire OR untranslatable_character THEN
        RAISE EXCEPTION 'invalid TXT' USING ERRCODE = '22023';
    END;
    IF left(body, 1) = chr(65279) THEN body := substr(body, 2); END IF;
    IF length(body) > 200000 THEN
        RAISE EXCEPTION 'TXT too many characters' USING ERRCODE = '54000';
    END IF;
    -- A single regex scan avoids quadratic UTF-8 substr(..., position, 1) work.
    -- Explicit Unicode whitespace also avoids depending on the DB locale's [:space:].
    IF btrim(body, E' \t\r\n' || U&'\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000') = ''
       OR left(body, 5) = '%PDF-'
       OR body ~ ('[' || chr(1) || '-' || chr(8) || chr(11) || chr(12)
           || chr(14) || '-' || chr(31) || chr(127) || '-' || chr(159) || ']') THEN
        RAISE EXCEPTION 'invalid TXT' USING ERRCODE = '22023';
    END IF;
    RETURN body;
END $$;

CREATE FUNCTION app_core.get_version_management(p_tenant bigint, p_actor bigint, p_document bigint)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, app_core, pg_temp AS $$
    SELECT jsonb_build_object('id', d.id, 'title', d.title, 'current_version_id', v.id,
                              'current_version_number', v.version_number)
    FROM app_core.documents d JOIN app_core.document_versions v
      ON (v.tenant_id, v.document_id, v.id) = (d.tenant_id, d.id, d.current_version_id)
    JOIN app_core.users u ON (u.tenant_id, u.id) = (d.tenant_id, d.owner_id)
    WHERE d.tenant_id = p_tenant AND d.id = p_document AND d.owner_id = p_actor
      AND u.is_active AND d.status = 'ready' AND v.status = 'ready'
$$;

CREATE FUNCTION app_core.get_job(p_tenant bigint, p_actor bigint, p_job uuid) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, app_core, pg_temp AS $$
    SELECT jsonb_build_object('job_id', j.id, 'version_id', j.version_id, 'status', j.status,
                             'error_code', j.error_code, 'retryable', false)
    FROM app_core.version_jobs j JOIN app_core.documents d
      ON (d.tenant_id, d.id) = (j.tenant_id, j.document_id)
    JOIN app_core.users u ON (u.tenant_id, u.id) = (d.tenant_id, d.owner_id)
    WHERE j.id = p_job AND j.tenant_id = p_tenant AND d.owner_id = p_actor
      AND u.is_active AND d.status = 'ready'
$$;

CREATE FUNCTION app_core.create_version(p_tenant bigint, p_actor bigint, p_document bigint,
    p_expected bigint, p_source bytea, p_key text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app_core, pg_temp AS $$
DECLARE outgoing bigint; current_data jsonb; fingerprint text; prior app_core.version_jobs%ROWTYPE;
    new_version bigint; number integer; job_id uuid;
BEGIN
    current_data := app_core.get_version_management(p_tenant, p_actor, p_document);
    IF current_data IS NULL THEN RAISE EXCEPTION 'not available' USING ERRCODE = 'P0002'; END IF;
    IF p_expected IS NULL OR p_expected <= 0 OR p_key IS NULL OR p_key !~ '^[A-Za-z0-9_-]{16,128}$' THEN
        RAISE EXCEPTION 'invalid request' USING ERRCODE = '22023';
    END IF;
    PERFORM app_core.validate_version_txt(p_source);
    outgoing := (current_data ->> 'current_version_id')::bigint;
    PERFORM 1 FROM app_core.document_versions
        WHERE tenant_id = p_tenant AND document_id = p_document AND id = outgoing FOR UPDATE;
    PERFORM 1 FROM app_core.documents WHERE tenant_id = p_tenant AND id = p_document FOR UPDATE;
    -- A separate statement after waiting sees the latest READ COMMITTED snapshot.
    current_data := app_core.get_version_management(p_tenant, p_actor, p_document);
    IF current_data IS NULL THEN RAISE EXCEPTION 'not available' USING ERRCODE = 'P0002'; END IF;
    IF (current_data ->> 'current_version_id')::bigint <> outgoing THEN
        RAISE EXCEPTION 'pointer changed while waiting' USING ERRCODE = '40001';
    END IF;
    fingerprint := encode(sha256(convert_to(encode(sha256(p_source), 'hex') || ':TXT:' || p_expected::text, 'UTF8')), 'hex');
    SELECT * INTO prior FROM app_core.version_jobs
        WHERE tenant_id = p_tenant AND actor_id = p_actor AND document_id = p_document AND idempotency_key = p_key;
    IF FOUND THEN
        IF prior.request_fingerprint <> fingerprint THEN
            RAISE EXCEPTION 'idempotency conflict' USING ERRCODE = '40001';
        END IF;
        SELECT version_number INTO number FROM app_core.document_versions WHERE id = prior.version_id;
        RETURN jsonb_build_object('document_id', p_document, 'version_id', prior.version_id,
            'version_number', number, 'processing_status', 'queued', 'is_current', false, 'job_id', prior.id);
    END IF;
    IF outgoing <> p_expected OR EXISTS (SELECT FROM app_core.version_jobs
        WHERE tenant_id = p_tenant AND document_id = p_document AND status IN ('queued', 'processing')) THEN
        RAISE EXCEPTION 'base or pending conflict' USING ERRCODE = '40001';
    END IF;
    SELECT coalesce(max(version_number), 0) + 1 INTO number FROM app_core.document_versions
        WHERE tenant_id = p_tenant AND document_id = p_document;
    INSERT INTO app_core.document_versions (tenant_id, document_id, version_number, owner_id, visibility, status, content)
        VALUES (p_tenant, p_document, number, p_actor, 'private', 'queued', NULL) RETURNING id INTO new_version;
    INSERT INTO app_core.version_sources (tenant_id, document_id, version_id, source_bytes)
        VALUES (p_tenant, p_document, new_version, p_source);
    INSERT INTO app_core.version_jobs (tenant_id, document_id, version_id, actor_id, expected_version_id,
        idempotency_key, request_fingerprint)
        VALUES (p_tenant, p_document, new_version, p_actor, p_expected, p_key, fingerprint) RETURNING id INTO job_id;
    RETURN jsonb_build_object('document_id', p_document, 'version_id', new_version, 'version_number', number,
        'processing_status', 'queued', 'is_current', false, 'job_id', job_id);
END $$;

CREATE FUNCTION app_core.claim_version_job() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app_core, pg_temp AS $$
DECLARE claimed app_core.version_jobs%ROWTYPE; source app_core.version_sources%ROWTYPE;
BEGIN
    SELECT * INTO claimed FROM app_core.version_jobs WHERE status = 'queued'
        ORDER BY created_at, id FOR UPDATE SKIP LOCKED LIMIT 1;
    IF NOT FOUND THEN RETURN NULL; END IF;
    UPDATE app_core.version_jobs SET status = 'processing', lease_token = gen_random_uuid(),
        lease_expires_at = clock_timestamp() + interval '5 minutes'
        WHERE id = claimed.id RETURNING * INTO claimed;
    SELECT * INTO source FROM app_core.version_sources
        WHERE (tenant_id, document_id, version_id) = (claimed.tenant_id, claimed.document_id, claimed.version_id);
    RETURN jsonb_build_object('job_id', claimed.id, 'version_id', claimed.version_id,
        'document_id', claimed.document_id, 'tenant_id', claimed.tenant_id, 'actor_id', claimed.actor_id,
        'expected_version_id', claimed.expected_version_id, 'lease_token', claimed.lease_token,
        'lease_expires_at', claimed.lease_expires_at, 'status', claimed.status,
        'source_bytes', encode(source.source_bytes, 'hex'), 'source_sha256', source.source_sha256);
END $$;

-- One internal capability implements both terminal paths with one lock protocol.
CREATE FUNCTION app_core.finish_version_job(p_job uuid, p_lease uuid, p_success boolean) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app_core, pg_temp AS $$
DECLARE job app_core.version_jobs%ROWTYPE; document app_core.documents%ROWTYPE;
    target app_core.document_versions%ROWTYPE; outgoing bigint; body text; reason text; actor_active boolean;
BEGIN
    SELECT * INTO job FROM app_core.version_jobs WHERE id = p_job;
    IF NOT FOUND THEN RAISE EXCEPTION 'invalid lease' USING ERRCODE = '40001'; END IF;
    SELECT current_version_id INTO outgoing FROM app_core.documents
        WHERE tenant_id = job.tenant_id AND id = job.document_id;
    PERFORM 1 FROM app_core.document_versions WHERE tenant_id = job.tenant_id
        AND document_id = job.document_id AND id = outgoing FOR UPDATE;
    PERFORM 1 FROM app_core.documents WHERE tenant_id = job.tenant_id AND id = job.document_id FOR UPDATE;
    -- Re-read in a new statement, then acquire target -> job, never job -> document.
    SELECT * INTO document FROM app_core.documents WHERE tenant_id = job.tenant_id AND id = job.document_id;
    IF document.current_version_id IS DISTINCT FROM outgoing THEN
        RAISE EXCEPTION 'pointer changed while waiting' USING ERRCODE = '40001';
    END IF;
    PERFORM 1 FROM app_core.document_versions WHERE tenant_id = job.tenant_id
        AND document_id = job.document_id AND id = job.version_id FOR UPDATE;
    PERFORM 1 FROM app_core.version_jobs WHERE id = p_job FOR UPDATE;
    SELECT * INTO job FROM app_core.version_jobs WHERE id = p_job;
    IF job.status <> 'processing' OR p_lease IS NULL OR job.lease_token IS DISTINCT FROM p_lease THEN
        RAISE EXCEPTION 'invalid lease' USING ERRCODE = '40001';
    END IF;
    SELECT * INTO target FROM app_core.document_versions WHERE tenant_id = job.tenant_id
        AND document_id = job.document_id AND id = job.version_id;
    SELECT is_active INTO actor_active FROM app_core.users WHERE tenant_id = job.tenant_id AND id = job.actor_id;
    IF NOT p_success THEN reason := 'processing_failed';
    ELSIF job.lease_expires_at <= clock_timestamp() THEN reason := 'lease_expired';
    ELSIF NOT coalesce(actor_active, false) THEN reason := 'actor_inactive';
    ELSIF document.owner_id <> job.actor_id OR target.owner_id <> job.actor_id THEN reason := 'ownership_changed';
    ELSIF document.status <> 'ready' THEN reason := 'archived';
    ELSIF document.current_version_id IS DISTINCT FROM job.expected_version_id THEN reason := 'base_conflict';
    ELSIF target.status NOT IN ('queued', 'processing') OR NOT EXISTS (SELECT FROM app_core.document_versions
        WHERE tenant_id = job.tenant_id AND document_id = job.document_id AND id = outgoing AND status = 'ready')
        THEN reason := 'version_unavailable';
    ELSE
        BEGIN
            SELECT app_core.validate_version_txt(source_bytes) INTO body FROM app_core.version_sources
                WHERE (tenant_id, document_id, version_id) = (job.tenant_id, job.document_id, job.version_id)
                  AND encode(sha256(source_bytes), 'hex') = source_sha256;
            IF body IS NULL THEN reason := 'invalid_source'; END IF;
        EXCEPTION WHEN SQLSTATE '22023' OR SQLSTATE '54000' THEN reason := 'invalid_source';
        END;
    END IF;
    IF reason IS NOT NULL THEN
        UPDATE app_core.document_versions SET status = 'failed'
            WHERE id = job.version_id AND status IN ('queued', 'processing');
        UPDATE app_core.version_jobs SET status = 'failed', error_code = reason,
            lease_token = NULL, lease_expires_at = NULL WHERE id = p_job;
        RETURN jsonb_build_object('job_id', job.id, 'version_id', job.version_id,
            'status', 'failed', 'error_code', reason, 'retryable', false);
    END IF;
    UPDATE app_core.document_versions SET content = body, status = 'ready'
        WHERE tenant_id = job.tenant_id AND document_id = job.document_id AND id = job.version_id;
    UPDATE app_core.documents SET current_version_id = job.version_id
        WHERE tenant_id = job.tenant_id AND id = job.document_id AND current_version_id = job.expected_version_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'base conflict' USING ERRCODE = '40001'; END IF;
    UPDATE app_core.version_jobs SET status = 'ready', error_code = NULL,
        lease_token = NULL, lease_expires_at = NULL WHERE id = p_job;
    RETURN jsonb_build_object('job_id', job.id, 'version_id', job.version_id,
        'status', 'ready', 'error_code', NULL, 'retryable', false);
END $$;

CREATE FUNCTION app_core.complete_version_job(p_job uuid, p_lease uuid) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, app_core, pg_temp AS $$
    SELECT app_core.finish_version_job(p_job, p_lease, true)
$$;
CREATE FUNCTION app_core.fail_version_job(p_job uuid, p_lease uuid) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, app_core, pg_temp AS $$
    SELECT app_core.finish_version_job(p_job, p_lease, false)
$$;

-- Function ownership never confers table ownership or bypasses future RLS.
ALTER FUNCTION app_core.immutable_version_source() OWNER TO rag_version_writer;
ALTER FUNCTION app_core.validate_version_txt(bytea) OWNER TO rag_version_writer;
ALTER FUNCTION app_core.get_version_management(bigint, bigint, bigint) OWNER TO rag_version_writer;
ALTER FUNCTION app_core.get_job(bigint, bigint, uuid) OWNER TO rag_version_writer;
ALTER FUNCTION app_core.create_version(bigint, bigint, bigint, bigint, bytea, text) OWNER TO rag_version_writer;
ALTER FUNCTION app_core.claim_version_job() OWNER TO rag_version_writer;
ALTER FUNCTION app_core.finish_version_job(uuid, uuid, boolean) OWNER TO rag_version_writer;
ALTER FUNCTION app_core.complete_version_job(uuid, uuid) OWNER TO rag_version_writer;
ALTER FUNCTION app_core.fail_version_job(uuid, uuid) OWNER TO rag_version_writer;
REVOKE ALL ON FUNCTION app_core.immutable_version_source(), app_core.validate_version_txt(bytea),
    app_core.get_version_management(bigint, bigint, bigint), app_core.get_job(bigint, bigint, uuid),
    app_core.create_version(bigint, bigint, bigint, bigint, bytea, text), app_core.claim_version_job(),
    app_core.finish_version_job(uuid, uuid, boolean), app_core.complete_version_job(uuid, uuid),
    app_core.fail_version_job(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_core.get_version_management(bigint, bigint, bigint),
    app_core.get_job(bigint, bigint, uuid),
    app_core.create_version(bigint, bigint, bigint, bigint, bytea, text) TO rag_app;
GRANT EXECUTE ON FUNCTION app_core.claim_version_job(), app_core.complete_version_job(uuid, uuid),
    app_core.fail_version_job(uuid, uuid) TO rag_version_processor;
