-- 保留 TXT 來源、指紋與排程；PDF 解析結果只能由持有有效租約的處理角色發布。
ALTER TABLE app_core.version_sources ADD COLUMN source_format text NOT NULL DEFAULT 'txt'
    CHECK (source_format IN ('txt', 'pdf'));
ALTER TABLE app_core.version_sources DROP CONSTRAINT version_sources_source_bytes_check;
ALTER TABLE app_core.version_sources ADD CONSTRAINT version_sources_source_bytes_check
    CHECK (octet_length(source_bytes) BETWEEN 1 AND CASE WHEN source_format = 'pdf' THEN 2097152 ELSE 1048576 END);
ALTER TABLE app_core.document_versions ADD COLUMN source_pages jsonb, ADD COLUMN parser_version text;
ALTER TABLE app_core.document_versions ADD CONSTRAINT version_pdf_metadata_pair CHECK
    ((source_pages IS NULL AND parser_version IS NULL) OR
     (source_pages IS NOT NULL AND parser_version IS NOT NULL AND parser_version = 'pypdf-6.21.1-v1' AND status = 'ready'));
GRANT INSERT (source_format) ON app_core.version_sources TO rag_version_writer;
GRANT UPDATE (source_pages, parser_version) ON app_core.document_versions TO rag_version_writer;
ALTER TABLE app_core.version_jobs DROP CONSTRAINT version_jobs_error_code_check;
ALTER TABLE app_core.version_jobs ADD CONSTRAINT version_jobs_error_code_check CHECK
    (error_code IN ('processing_failed', 'lease_expired', 'archived', 'actor_inactive', 'ownership_changed',
     'base_conflict', 'invalid_source', 'version_unavailable', 'invalid_pdf', 'encrypted_pdf', 'unreadable_page',
     'unsupported_pdf', 'pdf_limit_exceeded', 'pdf_timeout', 'pdf_resource_limit', 'pdf_parser_unavailable'));

CREATE FUNCTION app_core.validate_version_pdf_pages(p_pages jsonb) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app_core, pg_temp AS $$
DECLARE item jsonb; ordinal bigint; body text := ''; page_text text;
BEGIN
    IF p_pages IS NULL OR jsonb_typeof(p_pages) <> 'array' THEN
        RAISE EXCEPTION 'invalid pages' USING ERRCODE = '22023';
    END IF;
    IF jsonb_array_length(p_pages) NOT BETWEEN 1 AND 20 THEN
        RAISE EXCEPTION 'invalid pages' USING ERRCODE = '22023';
    END IF;
    FOR item, ordinal IN SELECT value, ordinality FROM jsonb_array_elements(p_pages) WITH ORDINALITY LOOP
        IF jsonb_typeof(item) <> 'object' THEN RAISE EXCEPTION 'invalid page' USING ERRCODE = '22023'; END IF;
        IF (SELECT count(*) FROM jsonb_object_keys(item)) <> 2
           OR jsonb_typeof(item->'page') IS DISTINCT FROM 'number'
           OR item->'page' IS DISTINCT FROM to_jsonb(ordinal)
           OR jsonb_typeof(item->'text') IS DISTINCT FROM 'string' THEN
            RAISE EXCEPTION 'invalid page' USING ERRCODE = '22023';
        END IF;
        page_text := item->>'text';
        -- 已解析文字不套用 TXT 原始 bytes 的 PDF signature 檢查。
        IF btrim(page_text, E' \t\r\n' || U&'\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000') = ''
           OR position(chr(65533) IN page_text) > 0
           OR page_text ~ ('[' || chr(1) || '-' || chr(8) || chr(11) || chr(12)
             || chr(14) || '-' || chr(31) || chr(127) || '-' || chr(159) || ']') THEN
            RAISE EXCEPTION 'invalid text' USING ERRCODE = '22023';
        END IF;
        body := body || CASE WHEN ordinal > 1 THEN E'\n\n' ELSE '' END || page_text;
        IF length(body) > 200000 THEN RAISE EXCEPTION 'text too large' USING ERRCODE = '54000'; END IF;
    END LOOP;
    RETURN body;
END $$;


CREATE FUNCTION app_core.create_version(p_tenant bigint, p_actor bigint, p_document bigint,
    p_expected bigint, p_source bytea, p_key text, p_format text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app_core, pg_temp AS $$
DECLARE outgoing bigint; current_data jsonb; fingerprint text; prior app_core.version_jobs%ROWTYPE;
    new_version bigint; number integer; job_id uuid;
BEGIN
    current_data := app_core.get_version_management(p_tenant, p_actor, p_document);
    IF current_data IS NULL THEN RAISE EXCEPTION 'not available' USING ERRCODE = 'P0002'; END IF;
    IF p_expected IS NULL OR p_expected <= 0 OR p_key IS NULL OR p_key !~ '^[A-Za-z0-9_-]{16,128}$' THEN
        RAISE EXCEPTION 'invalid request' USING ERRCODE = '22023';
    END IF;
    IF p_format IS NULL OR p_format NOT IN ('txt', 'pdf') THEN
        RAISE EXCEPTION 'invalid format' USING ERRCODE = '22023';
    END IF;
    IF p_format = 'txt' THEN
        PERFORM app_core.validate_version_txt(p_source);
    ELSE
        IF p_source IS NULL OR octet_length(p_source) < 5 OR substring(p_source FROM 1 FOR 5) <> convert_to('%PDF-', 'UTF8') THEN
            RAISE EXCEPTION 'invalid PDF' USING ERRCODE = '22023';
        END IF;
        IF octet_length(p_source) > 2097152 THEN
            RAISE EXCEPTION 'PDF too large' USING ERRCODE = '54000';
        END IF;
    END IF;
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
    fingerprint := encode(sha256(convert_to(encode(sha256(p_source), 'hex') || ':' || upper(p_format) || ':' || p_expected::text, 'UTF8')), 'hex');
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
    INSERT INTO app_core.version_sources (tenant_id, document_id, version_id, source_bytes, source_format)
        VALUES (p_tenant, p_document, new_version, p_source, p_format);
    INSERT INTO app_core.version_jobs (tenant_id, document_id, version_id, actor_id, expected_version_id,
        idempotency_key, request_fingerprint)
        VALUES (p_tenant, p_document, new_version, p_actor, p_expected, p_key, fingerprint) RETURNING id INTO job_id;
    RETURN jsonb_build_object('document_id', p_document, 'version_id', new_version, 'version_number', number,
        'processing_status', 'queued', 'is_current', false, 'job_id', job_id);
END $$;

CREATE OR REPLACE FUNCTION app_core.claim_version_job() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app_core, pg_temp AS $$
DECLARE claimed app_core.version_jobs%ROWTYPE; source app_core.version_sources%ROWTYPE;
BEGIN
  SELECT * INTO claimed FROM app_core.version_jobs
    WHERE status = 'queued' AND available_at <= clock_timestamp() AND attempts < 3
    ORDER BY available_at, created_at, id FOR UPDATE SKIP LOCKED LIMIT 1;
  IF NOT FOUND THEN RETURN NULL; END IF;
  UPDATE app_core.version_jobs SET status = 'processing', lease_token = gen_random_uuid(),
    lease_expires_at = clock_timestamp() + interval '5 minutes', attempts = attempts + 1,
    error_code = NULL WHERE id = claimed.id RETURNING * INTO claimed;
  SELECT * INTO source FROM app_core.version_sources
    WHERE (tenant_id, document_id, version_id) = (claimed.tenant_id, claimed.document_id, claimed.version_id);
  RETURN jsonb_build_object('job_id', claimed.id, 'version_id', claimed.version_id,
    'document_id', claimed.document_id, 'tenant_id', claimed.tenant_id, 'actor_id', claimed.actor_id,
    'expected_version_id', claimed.expected_version_id, 'lease_token', claimed.lease_token,
    'lease_expires_at', claimed.lease_expires_at, 'status', claimed.status,
    'source_format', source.source_format, 'source_bytes', encode(source.source_bytes, 'hex'), 'source_sha256', source.source_sha256);
END $$;

CREATE FUNCTION app_core.finish_version_job_result(p_job uuid, p_lease uuid, p_success boolean,
    p_format text, p_digest text, p_pages jsonb, p_error text) RETURNS jsonb
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
    IF NOT p_success THEN reason := coalesce(p_error, 'processing_failed');
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
            IF p_format = 'pdf' THEN
                IF NOT EXISTS (SELECT FROM app_core.version_sources
                    WHERE (tenant_id, document_id, version_id) = (job.tenant_id, job.document_id, job.version_id)
                      AND source_format = 'pdf' AND source_sha256 = p_digest
                      AND encode(sha256(source_bytes), 'hex') = p_digest) THEN
                    reason := 'invalid_source';
                ELSE
                    body := app_core.validate_version_pdf_pages(p_pages);
                END IF;
            ELSE
                SELECT app_core.validate_version_txt(source_bytes) INTO body FROM app_core.version_sources
                    WHERE (tenant_id, document_id, version_id) = (job.tenant_id, job.document_id, job.version_id)
                      AND source_format = 'txt' AND encode(sha256(source_bytes), 'hex') = source_sha256;
            END IF;
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
    UPDATE app_core.document_versions SET content = body, status = 'ready',
        source_pages = CASE WHEN p_format = 'pdf' THEN p_pages END,
        parser_version = CASE WHEN p_format = 'pdf' THEN 'pypdf-6.21.1-v1' END
        WHERE tenant_id = job.tenant_id AND document_id = job.document_id AND id = job.version_id;
    UPDATE app_core.documents SET current_version_id = job.version_id
        WHERE tenant_id = job.tenant_id AND id = job.document_id AND current_version_id = job.expected_version_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'base conflict' USING ERRCODE = '40001'; END IF;
    UPDATE app_core.version_jobs SET status = 'ready', error_code = NULL,
        lease_token = NULL, lease_expires_at = NULL WHERE id = p_job;
    RETURN jsonb_build_object('job_id', job.id, 'version_id', job.version_id,
        'status', 'ready', 'error_code', NULL, 'retryable', false);
END $$;


CREATE OR REPLACE FUNCTION app_core.create_version(p_tenant bigint, p_actor bigint, p_document bigint,
    p_expected bigint, p_source bytea, p_key text) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, app_core, pg_temp AS $$
    SELECT app_core.create_version(p_tenant, p_actor, p_document, p_expected, p_source, p_key, 'txt')
$$;
CREATE OR REPLACE FUNCTION app_core.finish_version_job(p_job uuid, p_lease uuid, p_success boolean) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, app_core, pg_temp AS $$
    SELECT app_core.finish_version_job_result(p_job, p_lease, p_success, 'txt', NULL, NULL, NULL)
$$;
CREATE FUNCTION app_core.finish_pdf_version_job(p_job uuid, p_lease uuid, p_digest text, p_pages jsonb, p_error text)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, app_core, pg_temp AS $$
    SELECT app_core.finish_version_job_result(p_job, p_lease, p_error IS NULL, 'pdf', p_digest, p_pages, p_error)
$$;
CREATE FUNCTION app_core.complete_pdf_version_job(p_job uuid, p_lease uuid, p_digest text, p_pages jsonb) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, app_core, pg_temp AS $$
    SELECT app_core.finish_pdf_version_job(p_job, p_lease, p_digest, p_pages, NULL)
$$;
CREATE FUNCTION app_core.fail_pdf_version_job(p_job uuid, p_lease uuid, p_error text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app_core, pg_temp AS $$
BEGIN
    IF p_error IS NULL OR p_error NOT IN ('invalid_pdf', 'encrypted_pdf', 'unreadable_page', 'unsupported_pdf',
        'pdf_limit_exceeded', 'pdf_timeout', 'pdf_resource_limit', 'pdf_parser_unavailable') THEN
        RAISE EXCEPTION 'invalid error' USING ERRCODE = '22023';
    END IF;
    RETURN app_core.finish_pdf_version_job(p_job, p_lease, NULL, NULL, p_error);
END $$;


ALTER FUNCTION app_core.validate_version_pdf_pages(jsonb) OWNER TO rag_version_writer;
REVOKE ALL ON FUNCTION app_core.validate_version_pdf_pages(jsonb) FROM PUBLIC;
ALTER FUNCTION app_core.create_version(bigint,bigint,bigint,bigint,bytea,text,text) OWNER TO rag_version_writer;
REVOKE ALL ON FUNCTION app_core.create_version(bigint,bigint,bigint,bigint,bytea,text,text) FROM PUBLIC;
ALTER FUNCTION app_core.finish_version_job_result(uuid,uuid,boolean,text,text,jsonb,text) OWNER TO rag_version_writer;
REVOKE ALL ON FUNCTION app_core.finish_version_job_result(uuid,uuid,boolean,text,text,jsonb,text) FROM PUBLIC;
ALTER FUNCTION app_core.finish_pdf_version_job(uuid,uuid,text,jsonb,text) OWNER TO rag_version_writer;
REVOKE ALL ON FUNCTION app_core.finish_pdf_version_job(uuid,uuid,text,jsonb,text) FROM PUBLIC;
ALTER FUNCTION app_core.complete_pdf_version_job(uuid,uuid,text,jsonb) OWNER TO rag_version_writer;
REVOKE ALL ON FUNCTION app_core.complete_pdf_version_job(uuid,uuid,text,jsonb) FROM PUBLIC;
ALTER FUNCTION app_core.fail_pdf_version_job(uuid,uuid,text) OWNER TO rag_version_writer;
REVOKE ALL ON FUNCTION app_core.fail_pdf_version_job(uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_core.create_version(bigint,bigint,bigint,bigint,bytea,text,text) TO rag_app;
GRANT EXECUTE ON FUNCTION app_core.complete_pdf_version_job(uuid,uuid,text,jsonb),
    app_core.fail_pdf_version_job(uuid,uuid,text) TO rag_worker, rag_version_processor;

