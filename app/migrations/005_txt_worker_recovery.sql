-- worker 僅能執行受限能力，不能任意讀取來源或指定公司／身分。
DO $roles$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'rag_worker') THEN
    CREATE ROLE rag_worker;
  END IF;
END $roles$;
ALTER ROLE rag_worker WITH NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT USAGE ON SCHEMA app_core TO rag_worker;

ALTER TABLE app_core.version_jobs
  ADD COLUMN attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 3),
  ADD COLUMN available_at timestamptz NOT NULL DEFAULT now();
-- 升級時已領取的工作也算一次，不讓重啟把次數歸零。
UPDATE app_core.version_jobs SET attempts = 1 WHERE status <> 'queued';
CREATE INDEX version_jobs_available ON app_core.version_jobs(available_at, created_at, id)
  WHERE status = 'queued';
CREATE INDEX version_jobs_expired ON app_core.version_jobs(lease_expires_at, id)
  WHERE status = 'processing';
GRANT UPDATE (attempts, available_at) ON app_core.version_jobs TO rag_version_writer;

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
    'source_bytes', encode(source.source_bytes, 'hex'), 'source_sha256', source.source_sha256);
END $$;

CREATE FUNCTION app_core.recover_version_job() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app_core, pg_temp AS $$
DECLARE job app_core.version_jobs%ROWTYPE; document app_core.documents%ROWTYPE;
  target app_core.document_versions%ROWTYPE; outgoing bigint; reason text; actor_active boolean; candidate record;
BEGIN
  -- 先找候選而不鎖 job，避免與發布的 outgoing → document → target → job 鎖序相反。
  FOR candidate IN SELECT id FROM app_core.version_jobs
    WHERE status = 'processing' AND lease_expires_at <= clock_timestamp()
    ORDER BY lease_expires_at, id
  LOOP
  BEGIN
  reason := NULL;
  SELECT * INTO job FROM app_core.version_jobs WHERE id = candidate.id;
  SELECT current_version_id INTO outgoing FROM app_core.documents
    WHERE tenant_id = job.tenant_id AND id = job.document_id;
  PERFORM 1 FROM app_core.document_versions WHERE tenant_id = job.tenant_id
    AND document_id = job.document_id AND id = outgoing FOR UPDATE NOWAIT;
  PERFORM 1 FROM app_core.documents WHERE tenant_id = job.tenant_id AND id = job.document_id FOR UPDATE NOWAIT;
  SELECT * INTO document FROM app_core.documents WHERE tenant_id = job.tenant_id AND id = job.document_id;
  IF document.current_version_id IS DISTINCT FROM outgoing THEN
    RAISE EXCEPTION 'pointer changed while waiting' USING ERRCODE = '40001';
  END IF;
  PERFORM 1 FROM app_core.document_versions WHERE tenant_id = job.tenant_id
    AND document_id = job.document_id AND id = job.version_id FOR UPDATE NOWAIT;
  PERFORM 1 FROM app_core.version_jobs WHERE id = job.id FOR UPDATE NOWAIT;
  -- 等鎖後重查，另一個 worker 可能已完成、失敗或回收這筆工作。
  SELECT * INTO job FROM app_core.version_jobs WHERE id = job.id;
  IF job.status <> 'processing' OR job.lease_expires_at > clock_timestamp() THEN CONTINUE; END IF;
  SELECT * INTO target FROM app_core.document_versions WHERE tenant_id = job.tenant_id
    AND document_id = job.document_id AND id = job.version_id;
  SELECT is_active INTO actor_active FROM app_core.users WHERE tenant_id = job.tenant_id AND id = job.actor_id;
  IF NOT coalesce(actor_active, false) THEN reason := 'actor_inactive';
  ELSIF document.owner_id <> job.actor_id OR target.owner_id <> job.actor_id THEN reason := 'ownership_changed';
  ELSIF document.status <> 'ready' THEN reason := 'archived';
  ELSIF document.current_version_id IS DISTINCT FROM job.expected_version_id THEN reason := 'base_conflict';
  ELSIF target.status NOT IN ('queued', 'processing') OR NOT EXISTS (SELECT FROM app_core.document_versions
    WHERE tenant_id = job.tenant_id AND document_id = job.document_id AND id = outgoing AND status = 'ready')
    THEN reason := 'version_unavailable';
  ELSIF job.attempts >= 3 THEN reason := 'lease_expired';
  END IF;
  IF reason IS NOT NULL THEN
    UPDATE app_core.document_versions SET status = 'failed'
      WHERE id = job.version_id AND status IN ('queued', 'processing');
    UPDATE app_core.version_jobs SET status = 'failed', error_code = reason,
      lease_token = NULL, lease_expires_at = NULL WHERE id = job.id;
    RETURN jsonb_build_object('job_id', job.id, 'status', 'failed', 'error_code', reason);
  END IF;
  UPDATE app_core.document_versions SET status = 'queued' WHERE id = job.version_id;
  UPDATE app_core.version_jobs SET status = 'queued', error_code = 'lease_expired',
    available_at = clock_timestamp() + CASE WHEN job.attempts <= 1 THEN interval '10 seconds' ELSE interval '20 seconds' END,
    lease_token = NULL, lease_expires_at = NULL WHERE id = job.id;
  RETURN jsonb_build_object('job_id', job.id, 'status', 'queued', 'error_code', 'lease_expired');
  EXCEPTION WHEN lock_not_available OR serialization_failure THEN
    -- 子交易回滾會釋放這筆候選已取得的鎖，再嘗試其他公司的工作。
    NULL;
  END;
  END LOOP;
  RETURN NULL;
END $$;

ALTER FUNCTION app_core.recover_version_job() OWNER TO rag_version_writer;
REVOKE ALL ON FUNCTION app_core.recover_version_job() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_core.recover_version_job() TO rag_worker, rag_version_processor;
GRANT EXECUTE ON FUNCTION app_core.claim_version_job(), app_core.complete_version_job(uuid, uuid),
  app_core.fail_version_job(uuid, uuid) TO rag_worker;
