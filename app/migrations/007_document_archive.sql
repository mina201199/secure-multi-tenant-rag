-- 封存是文件層級的終止狀態；保留 ready 版本及 PDF 頁次供未來獨立後台調閱。
ALTER TABLE app_core.documents
  ADD COLUMN archived_at timestamptz,
  ADD COLUMN archived_by bigint,
  ADD CONSTRAINT document_archive_actor_fk FOREIGN KEY (tenant_id, archived_by)
    REFERENCES app_core.users (tenant_id, id),
  ADD CONSTRAINT document_archive_metadata_pair CHECK
    ((archived_at IS NULL AND archived_by IS NULL) OR
     (archived_at IS NOT NULL AND archived_by IS NOT NULL AND status = 'archived'));
GRANT UPDATE (status, archived_at, archived_by) ON app_core.documents TO rag_version_writer;

CREATE FUNCTION app_core.archive_document(p_tenant bigint, p_actor bigint,
  p_document bigint, p_expected bigint) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app_core, pg_temp AS $$
DECLARE document app_core.documents%ROWTYPE; outgoing bigint;
BEGIN
  SELECT d.* INTO document FROM app_core.documents d
    JOIN app_core.users u ON u.tenant_id = d.tenant_id AND u.id = p_actor AND u.is_active
    WHERE d.tenant_id = p_tenant AND d.id = p_document AND d.owner_id = p_actor;
  IF NOT FOUND THEN RAISE EXCEPTION 'not available' USING ERRCODE = 'P0002'; END IF;
  IF p_expected IS NULL OR p_expected <= 0 THEN
    RAISE EXCEPTION 'invalid request' USING ERRCODE = '22023';
  END IF;
  outgoing := document.current_version_id;
  -- 與建立／發布／恢復相同的 outgoing → document → target → job 鎖序。
  PERFORM 1 FROM app_core.document_versions
    WHERE tenant_id = p_tenant AND document_id = p_document AND id = outgoing FOR UPDATE;
  PERFORM 1 FROM app_core.documents WHERE tenant_id = p_tenant AND id = p_document FOR UPDATE;
  SELECT d.* INTO document FROM app_core.documents d
    JOIN app_core.users u ON u.tenant_id = d.tenant_id AND u.id = p_actor AND u.is_active
    WHERE d.tenant_id = p_tenant AND d.id = p_document AND d.owner_id = p_actor;
  IF NOT FOUND THEN RAISE EXCEPTION 'not available' USING ERRCODE = 'P0002'; END IF;
  IF document.current_version_id IS DISTINCT FROM outgoing
      OR document.current_version_id IS DISTINCT FROM p_expected THEN
    RAISE EXCEPTION 'version changed' USING ERRCODE = '40001';
  END IF;
  IF document.status = 'archived' THEN
    RETURN jsonb_build_object('document_id', p_document, 'status', 'archived');
  END IF;
  IF document.status <> 'ready' THEN RAISE EXCEPTION 'not available' USING ERRCODE = 'P0002'; END IF;

  PERFORM 1 FROM app_core.document_versions
    WHERE tenant_id = p_tenant AND document_id = p_document ORDER BY id FOR UPDATE;
  PERFORM 1 FROM app_core.version_jobs
    WHERE tenant_id = p_tenant AND document_id = p_document ORDER BY id FOR UPDATE;
  UPDATE app_core.documents SET status = 'archived', archived_at = clock_timestamp(), archived_by = p_actor
    WHERE tenant_id = p_tenant AND id = p_document;
  UPDATE app_core.document_versions SET status = 'failed'
    WHERE tenant_id = p_tenant AND document_id = p_document AND status IN ('queued', 'processing');
  UPDATE app_core.version_jobs SET status = 'failed', error_code = 'archived',
    lease_token = NULL, lease_expires_at = NULL
    WHERE tenant_id = p_tenant AND document_id = p_document AND status IN ('queued', 'processing');
  RETURN jsonb_build_object('document_id', p_document, 'status', 'archived');
END $$;

ALTER FUNCTION app_core.archive_document(bigint,bigint,bigint,bigint) OWNER TO rag_version_writer;
REVOKE ALL ON FUNCTION app_core.archive_document(bigint,bigint,bigint,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_core.archive_document(bigint,bigint,bigint,bigint) TO rag_app;
