-- 歷史權限與一般閱讀分開；後台也不得開啟封存文件。
DO $roles$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'rag_history_access') THEN
    CREATE ROLE rag_history_access;
  END IF;
END $roles$;
ALTER ROLE rag_history_access WITH NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

CREATE TABLE app_core.history_readers (
  tenant_id bigint NOT NULL,
  version_id bigint NOT NULL,
  user_id bigint NOT NULL,
  granted_by bigint NOT NULL,
  granted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (version_id, user_id),
  FOREIGN KEY (tenant_id, version_id) REFERENCES app_core.document_versions (tenant_id, id),
  FOREIGN KEY (tenant_id, user_id) REFERENCES app_core.users (tenant_id, id),
  FOREIGN KEY (tenant_id, granted_by) REFERENCES app_core.users (tenant_id, id)
);
CREATE INDEX history_readers_user_idx ON app_core.history_readers (tenant_id, user_id, version_id);

CREATE TABLE app_core.history_audit_events (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id bigint NOT NULL,
  document_id bigint NOT NULL,
  version_id bigint NOT NULL,
  actor_id bigint NOT NULL,
  action text NOT NULL CHECK (action IN ('read', 'grant', 'revoke')),
  subject_id bigint,
  occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK ((action = 'read' AND subject_id IS NULL) OR (action <> 'read' AND subject_id IS NOT NULL)),
  FOREIGN KEY (tenant_id, document_id, version_id)
    REFERENCES app_core.document_versions (tenant_id, document_id, id),
  FOREIGN KEY (tenant_id, actor_id) REFERENCES app_core.users (tenant_id, id),
  FOREIGN KEY (tenant_id, subject_id) REFERENCES app_core.users (tenant_id, id)
);
CREATE INDEX history_audit_version_idx ON app_core.history_audit_events (tenant_id, version_id, id DESC);

GRANT USAGE ON SCHEMA app_core TO rag_history_access;
GRANT SELECT ON app_core.documents, app_core.document_versions TO rag_history_access;
GRANT SELECT (id, tenant_id, is_active, username, display_name) ON app_core.users TO rag_history_access;
-- PostgreSQL 的列鎖需要至少一欄 UPDATE 權；此 NOLOGIN 角色只透過下面的固定能力使用。
GRANT UPDATE (current_version_id) ON app_core.documents TO rag_history_access;
GRANT UPDATE (status) ON app_core.document_versions TO rag_history_access;
GRANT UPDATE (is_active) ON app_core.users TO rag_history_access;
GRANT SELECT, INSERT, DELETE ON app_core.history_readers TO rag_history_access;
GRANT SELECT, INSERT ON app_core.history_audit_events TO rag_history_access;
GRANT USAGE ON SEQUENCE app_core.history_audit_events_id_seq TO rag_history_access;

CREATE FUNCTION app_core.lock_history_version(p_tenant bigint, p_actor bigint, p_version bigint,
  p_manage boolean) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app_core, pg_temp AS $$
DECLARE doc_id bigint; outgoing bigint; result jsonb;
BEGIN
  SELECT d.id, d.current_version_id INTO doc_id, outgoing
    FROM app_core.documents d JOIN app_core.document_versions v
      ON (v.tenant_id, v.document_id) = (d.tenant_id, d.id)
    JOIN app_core.users u ON u.tenant_id = d.tenant_id AND u.id = p_actor AND u.is_active
    WHERE d.tenant_id = p_tenant AND v.id = p_version AND d.status = 'ready'
      AND v.status = 'ready' AND v.id <> d.current_version_id
      AND ((p_manage AND v.owner_id = p_actor) OR (NOT p_manage AND EXISTS (
        SELECT FROM app_core.history_readers h
        WHERE h.tenant_id = p_tenant AND h.version_id = v.id AND h.user_id = p_actor)));
  IF NOT FOUND THEN RAISE EXCEPTION 'not available' USING ERRCODE = 'P0002'; END IF;
  PERFORM 1 FROM app_core.document_versions
    WHERE tenant_id = p_tenant AND id = outgoing FOR UPDATE;
  PERFORM 1 FROM app_core.documents WHERE tenant_id = p_tenant AND id = doc_id FOR UPDATE;
  -- ACL／稽核 INSERT 的外鍵也會鎖歷史版本；舊 outgoing 不可在此反向等待。
  BEGIN
    PERFORM 1 FROM app_core.document_versions
      WHERE tenant_id = p_tenant AND document_id = doc_id AND id = p_version FOR SHARE NOWAIT;
  EXCEPTION WHEN lock_not_available THEN
    RAISE EXCEPTION 'history busy' USING ERRCODE = '40001';
  END;
  PERFORM 1 FROM app_core.users WHERE tenant_id = p_tenant AND id = p_actor FOR SHARE;
  IF (SELECT current_version_id FROM app_core.documents WHERE id = doc_id) IS DISTINCT FROM outgoing THEN
    RAISE EXCEPTION 'version changed' USING ERRCODE = '40001';
  END IF;
  SELECT jsonb_build_object('document_id', d.id, 'version_id', v.id, 'version_number', v.version_number,
    'title', d.title, 'category', d.category, 'owner_id', v.owner_id,
    'content', v.content, 'source_pages', v.source_pages, 'parser_version', v.parser_version)
    INTO result FROM app_core.documents d JOIN app_core.document_versions v
      ON (v.tenant_id, v.document_id) = (d.tenant_id, d.id)
    JOIN app_core.users u ON u.tenant_id = d.tenant_id AND u.id = p_actor AND u.is_active
    WHERE d.tenant_id = p_tenant AND d.id = doc_id AND d.status = 'ready'
      AND v.id = p_version AND v.status = 'ready' AND v.id <> d.current_version_id
      AND ((p_manage AND v.owner_id = p_actor) OR (NOT p_manage AND EXISTS (
        SELECT FROM app_core.history_readers h
        WHERE h.tenant_id = p_tenant AND h.version_id = v.id AND h.user_id = p_actor)));
  IF result IS NULL THEN RAISE EXCEPTION 'not available' USING ERRCODE = 'P0002'; END IF;
  RETURN result;
END $$;

CREATE FUNCTION app_core.list_history_versions(p_tenant bigint, p_actor bigint, p_after bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app_core, pg_temp AS $$
BEGIN
  IF NOT EXISTS (SELECT FROM app_core.users WHERE tenant_id = p_tenant AND id = p_actor AND is_active) THEN
    RAISE EXCEPTION 'not available' USING ERRCODE = 'P0002';
  END IF;
  IF p_after IS NULL OR p_after < 0 THEN RAISE EXCEPTION 'invalid request' USING ERRCODE = '22023'; END IF;
  RETURN (SELECT coalesce(jsonb_agg(to_jsonb(summary) ORDER BY version_id), '[]'::jsonb) FROM (
    SELECT d.id AS document_id, d.title, v.id AS version_id, v.version_number,
      v.owner_id = p_actor AS can_manage, h.user_id IS NOT NULL AS can_read
    FROM app_core.documents d JOIN app_core.document_versions v
      ON (v.tenant_id, v.document_id) = (d.tenant_id, d.id)
    LEFT JOIN app_core.history_readers h ON h.tenant_id = v.tenant_id AND h.version_id = v.id AND h.user_id = p_actor
    WHERE d.tenant_id = p_tenant AND d.status = 'ready' AND v.status = 'ready'
      AND v.id <> d.current_version_id AND v.id > p_after AND (v.owner_id = p_actor OR h.user_id IS NOT NULL)
    ORDER BY v.id LIMIT 100
  ) summary);
END $$;

CREATE FUNCTION app_core.get_history_access(p_tenant bigint, p_actor bigint, p_version bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app_core, pg_temp AS $$
DECLARE result jsonb;
BEGIN
  result := app_core.lock_history_version(p_tenant, p_actor, p_version, true)
    - 'content' - 'source_pages' - 'parser_version';
  RETURN result || jsonb_build_object('readers', (SELECT coalesce(jsonb_agg(to_jsonb(candidate) ORDER BY id), '[]'::jsonb)
    FROM (SELECT u.id, u.username, u.display_name, u.is_active, h.user_id IS NOT NULL AS granted
      FROM app_core.users u LEFT JOIN app_core.history_readers h
        ON h.tenant_id = u.tenant_id AND h.user_id = u.id AND h.version_id = p_version
      WHERE u.tenant_id = p_tenant AND (u.is_active OR h.user_id IS NOT NULL)) candidate),
    'events', (SELECT coalesce(jsonb_agg(to_jsonb(event) ORDER BY id DESC), '[]'::jsonb) FROM (
      SELECT e.id, e.action, e.actor_id, actor.username AS actor_username, e.subject_id,
        subject.username AS subject_username, e.occurred_at
      FROM app_core.history_audit_events e
      JOIN app_core.users actor ON actor.tenant_id = e.tenant_id AND actor.id = e.actor_id
      LEFT JOIN app_core.users subject ON subject.tenant_id = e.tenant_id AND subject.id = e.subject_id
      WHERE e.tenant_id = p_tenant AND e.version_id = p_version ORDER BY e.id DESC LIMIT 50
    ) event));
END $$;

CREATE FUNCTION app_core.set_history_access(p_tenant bigint, p_actor bigint, p_version bigint,
  p_reader bigint, p_granted boolean) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app_core, pg_temp AS $$
DECLARE target jsonb; audit_id bigint;
BEGIN
  target := app_core.lock_history_version(p_tenant, p_actor, p_version, true);
  IF p_granted IS NULL OR p_reader IS NULL THEN RAISE EXCEPTION 'invalid request' USING ERRCODE = '22023'; END IF;
  PERFORM 1 FROM app_core.users WHERE tenant_id = p_tenant AND id = p_reader FOR SHARE;
  IF NOT EXISTS (SELECT FROM app_core.users WHERE tenant_id = p_tenant AND id = p_reader
      AND (NOT p_granted OR is_active)) THEN
    RAISE EXCEPTION 'invalid reader' USING ERRCODE = '22023';
  END IF;
  IF p_granted THEN
    INSERT INTO app_core.history_readers (tenant_id, version_id, user_id, granted_by)
      VALUES (p_tenant, p_version, p_reader, p_actor) ON CONFLICT DO NOTHING;
  ELSE
    DELETE FROM app_core.history_readers WHERE tenant_id = p_tenant AND version_id = p_version AND user_id = p_reader;
  END IF;
  INSERT INTO app_core.history_audit_events (tenant_id, document_id, version_id, actor_id, action, subject_id)
    VALUES (p_tenant, (target->>'document_id')::bigint, p_version, p_actor,
      CASE WHEN p_granted THEN 'grant' ELSE 'revoke' END, p_reader) RETURNING id INTO audit_id;
  RETURN jsonb_build_object('version_id', p_version, 'reader_id', p_reader, 'granted', p_granted, 'audit_id', audit_id);
END $$;

CREATE FUNCTION app_core.read_history_version(p_tenant bigint, p_actor bigint, p_version bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, app_core, pg_temp AS $$
DECLARE result jsonb; audit_id bigint;
BEGIN
  result := app_core.lock_history_version(p_tenant, p_actor, p_version, false);
  INSERT INTO app_core.history_audit_events (tenant_id, document_id, version_id, actor_id, action)
    VALUES (p_tenant, (result->>'document_id')::bigint, p_version, p_actor, 'read') RETURNING id INTO audit_id;
  RETURN result || jsonb_build_object('audit_id', audit_id);
END $$;

ALTER FUNCTION app_core.lock_history_version(bigint,bigint,bigint,boolean) OWNER TO rag_history_access;
ALTER FUNCTION app_core.list_history_versions(bigint,bigint,bigint) OWNER TO rag_history_access;
ALTER FUNCTION app_core.get_history_access(bigint,bigint,bigint) OWNER TO rag_history_access;
ALTER FUNCTION app_core.set_history_access(bigint,bigint,bigint,bigint,boolean) OWNER TO rag_history_access;
ALTER FUNCTION app_core.read_history_version(bigint,bigint,bigint) OWNER TO rag_history_access;
REVOKE ALL ON FUNCTION app_core.lock_history_version(bigint,bigint,bigint,boolean),
  app_core.list_history_versions(bigint,bigint,bigint), app_core.get_history_access(bigint,bigint,bigint),
  app_core.set_history_access(bigint,bigint,bigint,bigint,boolean),
  app_core.read_history_version(bigint,bigint,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_core.list_history_versions(bigint,bigint,bigint),
  app_core.get_history_access(bigint,bigint,bigint), app_core.set_history_access(bigint,bigint,bigint,bigint,boolean),
  app_core.read_history_version(bigint,bigint,bigint) TO rag_app;
