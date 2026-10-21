-- 交接能力獨立授權；修訂記錄阻止擁有權或帳號狀態往返後復活舊申請。
DO $roles$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='rag_transfer_access') THEN CREATE ROLE rag_transfer_access; END IF;
END $roles$;
ALTER ROLE rag_transfer_access WITH NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER TABLE app_core.users ADD COLUMN transfer_revision bigint NOT NULL DEFAULT 0;
ALTER TABLE app_core.documents ADD COLUMN owner_revision bigint NOT NULL DEFAULT 0;
ALTER TABLE app_core.document_versions ADD COLUMN owner_revision bigint NOT NULL DEFAULT 0;
CREATE FUNCTION app_core.bump_transfer_revision() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,app_core,pg_temp AS $$
BEGIN
  IF TG_TABLE_NAME='users' THEN
    NEW.transfer_revision := OLD.transfer_revision + CASE WHEN NEW.is_active IS DISTINCT FROM OLD.is_active OR NEW.manager_id IS DISTINCT FROM OLD.manager_id THEN 1 ELSE 0 END;
  ELSE
    NEW.owner_revision := OLD.owner_revision + CASE WHEN NEW.owner_id IS DISTINCT FROM OLD.owner_id THEN 1 ELSE 0 END;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER transfer_user_revision BEFORE UPDATE ON app_core.users FOR EACH ROW EXECUTE FUNCTION app_core.bump_transfer_revision();
CREATE TRIGGER transfer_document_revision BEFORE UPDATE ON app_core.documents FOR EACH ROW EXECUTE FUNCTION app_core.bump_transfer_revision();
CREATE TRIGGER transfer_version_revision BEFORE UPDATE ON app_core.document_versions FOR EACH ROW EXECUTE FUNCTION app_core.bump_transfer_revision();
REVOKE ALL ON FUNCTION app_core.bump_transfer_revision() FROM PUBLIC;
CREATE TABLE app_core.transfer_requests (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id bigint NOT NULL,
  document_id bigint NOT NULL,
  current_version_id bigint NOT NULL,
  requester_id bigint NOT NULL,
  manager_id bigint NOT NULL,
  recipient_id bigint NOT NULL,
  document_revision bigint NOT NULL,
  requester_revision bigint NOT NULL,
  manager_revision bigint NOT NULL,
  recipient_revision bigint NOT NULL,
  reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 500),
  status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '7 days',
  decided_at timestamptz,
  UNIQUE(tenant_id,document_id,id),
  UNIQUE(tenant_id,id),
  CHECK(requester_id<>recipient_id AND manager_id<>recipient_id AND requester_id<>manager_id),
  FOREIGN KEY(tenant_id,document_id,current_version_id) REFERENCES app_core.document_versions(tenant_id,document_id,id),
  FOREIGN KEY(tenant_id,requester_id) REFERENCES app_core.users(tenant_id,id),
  FOREIGN KEY(tenant_id,manager_id) REFERENCES app_core.users(tenant_id,id),
  FOREIGN KEY(tenant_id,recipient_id) REFERENCES app_core.users(tenant_id,id)
);
CREATE TABLE app_core.transfer_request_versions (
  request_id bigint NOT NULL,
  tenant_id bigint NOT NULL,
  document_id bigint NOT NULL,
  version_id bigint NOT NULL,
  owner_revision bigint NOT NULL,
  PRIMARY KEY(request_id,version_id),
  FOREIGN KEY(tenant_id,document_id,request_id) REFERENCES app_core.transfer_requests(tenant_id,document_id,id) ON DELETE CASCADE,
  FOREIGN KEY(tenant_id,document_id,version_id) REFERENCES app_core.document_versions(tenant_id,document_id,id)
);
CREATE TABLE app_core.transfer_audit_events (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  request_id bigint NOT NULL,
  tenant_id bigint NOT NULL,
  actor_id bigint NOT NULL,
  FOREIGN KEY(tenant_id,request_id) REFERENCES app_core.transfer_requests(tenant_id,id) ON DELETE CASCADE,
  FOREIGN KEY(tenant_id,actor_id) REFERENCES app_core.users(tenant_id,id),
  action text NOT NULL CHECK(action IN ('created','approved','rejected')),
  occurred_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX transfer_request_list ON app_core.transfer_requests(tenant_id,id);
GRANT USAGE ON SCHEMA app_core TO rag_transfer_access;
GRANT SELECT ON app_core.documents,app_core.document_versions TO rag_transfer_access;
GRANT SELECT(id,tenant_id,is_active,manager_id,transfer_revision,username,display_name) ON app_core.users TO rag_transfer_access;
GRANT UPDATE(owner_id) ON app_core.documents,app_core.document_versions TO rag_transfer_access;
GRANT UPDATE(is_active) ON app_core.users TO rag_transfer_access;
GRANT SELECT,INSERT,UPDATE ON app_core.transfer_requests TO rag_transfer_access;
GRANT SELECT,INSERT ON app_core.transfer_request_versions,app_core.transfer_audit_events TO rag_transfer_access;
GRANT USAGE ON SEQUENCE app_core.transfer_requests_id_seq,app_core.transfer_audit_events_id_seq TO rag_transfer_access;
CREATE FUNCTION app_core.get_transfer_options(p_tenant bigint,p_actor bigint,p_document bigint) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,app_core,pg_temp AS $$
DECLARE result jsonb; manager bigint;
BEGIN
  SELECT u.manager_id INTO manager FROM app_core.users u JOIN app_core.users m ON m.tenant_id=u.tenant_id AND m.id=u.manager_id AND m.is_active
    WHERE u.tenant_id=p_tenant AND u.id=p_actor AND u.is_active;
  IF manager IS NULL THEN RAISE EXCEPTION 'not available' USING ERRCODE='P0002'; END IF;
  SELECT jsonb_build_object('document_id',d.id,'title',d.title,'current_version_id',d.current_version_id,
    'versions',(SELECT coalesce(jsonb_agg(jsonb_build_object('version_id',v.id,'version_number',v.version_number) ORDER BY v.version_number),'[]'::jsonb)
      FROM app_core.document_versions v WHERE v.tenant_id=p_tenant AND v.document_id=d.id AND v.owner_id=p_actor AND v.status='ready'),
    'recipients',(SELECT coalesce(jsonb_agg(jsonb_build_object('id',u.id,'username',u.username,'display_name',u.display_name) ORDER BY u.id),'[]'::jsonb)
      FROM app_core.users u WHERE u.tenant_id=p_tenant AND u.is_active AND u.id NOT IN(p_actor,manager))) INTO result
    FROM app_core.documents d JOIN app_core.document_versions v ON (v.tenant_id,v.document_id,v.id)=(d.tenant_id,d.id,d.current_version_id)
    WHERE d.tenant_id=p_tenant AND d.id=p_document AND d.owner_id=p_actor AND d.status='ready' AND v.owner_id=p_actor AND v.status='ready';
  IF result IS NULL THEN RAISE EXCEPTION 'not available' USING ERRCODE='P0002'; END IF;
  IF EXISTS(SELECT FROM app_core.document_versions WHERE tenant_id=p_tenant AND document_id=p_document AND status IN('queued','processing')) THEN
    RAISE EXCEPTION 'processing pending' USING ERRCODE='40001';
  END IF;
  RETURN result;
END $$;
CREATE FUNCTION app_core.create_transfer(p_tenant bigint,p_actor bigint,p_document bigint,p_expected bigint,p_versions bigint[],p_recipient bigint,p_reason text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,app_core,pg_temp AS $$
DECLARE options jsonb; outgoing bigint; request bigint; manager bigint;
BEGIN
  options:=app_core.get_transfer_options(p_tenant,p_actor,p_document);
  IF p_expected IS NULL OR p_expected<=0 OR p_versions IS NULL OR cardinality(p_versions) NOT BETWEEN 1 AND 100
    OR array_ndims(p_versions)<>1 OR array_position(p_versions,NULL) IS NOT NULL
    OR EXISTS(SELECT FROM unnest(p_versions) v WHERE v<=0)
    OR (SELECT count(DISTINCT v) FROM unnest(p_versions) v)<>cardinality(p_versions)
    OR p_reason IS NULL OR length(btrim(p_reason)) NOT BETWEEN 1 AND 500 OR p_recipient IS NULL THEN
    RAISE EXCEPTION 'invalid request' USING ERRCODE='22023';
  END IF;
  outgoing:=(options->>'current_version_id')::bigint;
  PERFORM 1 FROM app_core.document_versions WHERE tenant_id=p_tenant AND id=outgoing FOR UPDATE;
  PERFORM 1 FROM app_core.documents WHERE tenant_id=p_tenant AND id=p_document FOR UPDATE;
  BEGIN
    PERFORM 1 FROM app_core.document_versions WHERE tenant_id=p_tenant AND document_id=p_document AND id=ANY(p_versions) AND id<>outgoing ORDER BY id FOR UPDATE NOWAIT;
  EXCEPTION WHEN lock_not_available THEN RAISE EXCEPTION 'history busy' USING ERRCODE='40001'; END;
  SELECT manager_id INTO manager FROM app_core.users WHERE tenant_id=p_tenant AND id=p_actor;
  PERFORM 1 FROM app_core.users WHERE tenant_id=p_tenant AND id IN(p_actor,manager,p_recipient) ORDER BY id FOR SHARE;
  options:=app_core.get_transfer_options(p_tenant,p_actor,p_document);
  IF manager IS DISTINCT FROM (SELECT manager_id FROM app_core.users WHERE tenant_id=p_tenant AND id=p_actor) THEN RAISE EXCEPTION 'manager changed' USING ERRCODE='40001'; END IF;
  IF (options->>'current_version_id')::bigint<>outgoing OR outgoing<>p_expected THEN RAISE EXCEPTION 'version changed' USING ERRCODE='40001'; END IF;
  IF NOT outgoing=ANY(p_versions) OR (SELECT count(*) FROM app_core.document_versions WHERE tenant_id=p_tenant AND document_id=p_document AND id=ANY(p_versions) AND owner_id=p_actor AND status='ready')<>cardinality(p_versions)
    OR p_recipient IN(p_actor,manager) OR NOT EXISTS(SELECT FROM app_core.users WHERE tenant_id=p_tenant AND id=p_recipient AND is_active) THEN
    RAISE EXCEPTION 'invalid scope or recipient' USING ERRCODE='22023';
  END IF;
  INSERT INTO app_core.transfer_requests(tenant_id,document_id,current_version_id,requester_id,manager_id,recipient_id,document_revision,requester_revision,manager_revision,recipient_revision,reason)
    SELECT p_tenant,p_document,outgoing,p_actor,manager,p_recipient,d.owner_revision,u.transfer_revision,m.transfer_revision,r.transfer_revision,btrim(p_reason)
    FROM app_core.documents d,app_core.users u,app_core.users m,app_core.users r
    WHERE d.id=p_document AND u.id=p_actor AND m.id=manager AND r.id=p_recipient RETURNING id INTO request;
  INSERT INTO app_core.transfer_request_versions SELECT request,p_tenant,p_document,id,owner_revision FROM app_core.document_versions WHERE tenant_id=p_tenant AND document_id=p_document AND id=ANY(p_versions);
  INSERT INTO app_core.transfer_audit_events(request_id,tenant_id,actor_id,action) VALUES(request,p_tenant,p_actor,'created');
  RETURN jsonb_build_object('request_id',request,'status','pending');
END $$;
CREATE FUNCTION app_core.list_transfers(p_tenant bigint,p_actor bigint,p_after bigint) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,app_core,pg_temp AS $$
BEGIN
  IF NOT EXISTS(SELECT FROM app_core.users WHERE tenant_id=p_tenant AND id=p_actor AND is_active) THEN RAISE EXCEPTION 'not available' USING ERRCODE='P0002'; END IF;
  IF p_after IS NULL OR p_after<0 THEN RAISE EXCEPTION 'invalid request' USING ERRCODE='22023'; END IF;
  RETURN(SELECT coalesce(jsonb_agg(summary ORDER BY id),'[]'::jsonb) FROM (
    SELECT t.id,jsonb_build_object('request_id',t.id,'status',t.status,'requester_id',t.requester_id,'requester_username',u.username,
      'recipient_id',t.recipient_id,'recipient_username',r.username,'reason',t.reason,'created_at',t.created_at,'expires_at',t.expires_at,
      'can_decide',t.status='pending' AND t.manager_id=p_actor AND u.manager_id=p_actor AND t.expires_at>clock_timestamp()) ||
      CASE WHEN t.requester_id=p_actor THEN jsonb_build_object('document_id',t.document_id,'version_ids',(SELECT jsonb_agg(version_id ORDER BY version_id) FROM app_core.transfer_request_versions WHERE request_id=t.id)) ELSE '{}'::jsonb END AS summary
    FROM app_core.transfer_requests t JOIN app_core.users u ON u.id=t.requester_id JOIN app_core.users r ON r.id=t.recipient_id
    WHERE t.tenant_id=p_tenant AND t.id>p_after AND (t.requester_id=p_actor OR (t.manager_id=p_actor AND u.manager_id=p_actor AND t.status='pending')) ORDER BY t.id LIMIT 100
  ) rows);
END $$;
CREATE FUNCTION app_core.decide_transfer(p_tenant bigint,p_actor bigint,p_request bigint,p_approve boolean) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,app_core,pg_temp AS $$
DECLARE request app_core.transfer_requests%ROWTYPE; outgoing bigint; document app_core.documents%ROWTYPE; result_status text;
BEGIN
  SELECT t.* INTO request FROM app_core.transfer_requests t JOIN app_core.users u ON u.id=t.requester_id AND u.manager_id=p_actor
    JOIN app_core.users m ON m.tenant_id=t.tenant_id AND m.id=p_actor AND m.is_active
    WHERE t.tenant_id=p_tenant AND t.id=p_request AND t.manager_id=p_actor FOR UPDATE OF t;
  IF NOT FOUND THEN RAISE EXCEPTION 'not available' USING ERRCODE='P0002'; END IF;
  IF p_approve IS NULL THEN RAISE EXCEPTION 'invalid request' USING ERRCODE='22023'; END IF;
  IF request.status<>'pending' OR request.expires_at<=clock_timestamp() THEN RAISE EXCEPTION 'stale request' USING ERRCODE='40001'; END IF;
  SELECT current_version_id INTO outgoing FROM app_core.documents WHERE tenant_id=p_tenant AND id=request.document_id;
  PERFORM 1 FROM app_core.document_versions WHERE tenant_id=p_tenant AND id=outgoing FOR UPDATE;
  PERFORM 1 FROM app_core.documents WHERE tenant_id=p_tenant AND id=request.document_id FOR UPDATE;
  BEGIN
    PERFORM 1 FROM app_core.document_versions WHERE tenant_id=p_tenant AND document_id=request.document_id
      AND id IN(SELECT version_id FROM app_core.transfer_request_versions WHERE request_id=p_request) AND id<>outgoing ORDER BY id FOR UPDATE NOWAIT;
  EXCEPTION WHEN lock_not_available THEN RAISE EXCEPTION 'history busy' USING ERRCODE='40001'; END;
  PERFORM 1 FROM app_core.users WHERE tenant_id=p_tenant AND id IN(request.requester_id,request.manager_id,request.recipient_id) ORDER BY id FOR SHARE;
  SELECT * INTO document FROM app_core.documents WHERE tenant_id=p_tenant AND id=request.document_id;
  IF request.expires_at<=clock_timestamp() OR NOT EXISTS(SELECT FROM app_core.users u,app_core.users m,app_core.users r
    WHERE u.tenant_id=p_tenant AND m.tenant_id=p_tenant AND r.tenant_id=p_tenant AND u.id=request.requester_id AND m.id=request.manager_id AND r.id=request.recipient_id
      AND u.is_active AND m.is_active AND r.is_active AND u.manager_id=m.id AND u.transfer_revision=request.requester_revision
      AND m.transfer_revision=request.manager_revision AND r.transfer_revision=request.recipient_revision) THEN
    RAISE EXCEPTION 'accounts changed' USING ERRCODE='40001';
  END IF;
  IF p_approve THEN
    IF document.current_version_id IS DISTINCT FROM outgoing OR document.current_version_id<>request.current_version_id OR document.owner_id<>request.requester_id
      OR document.owner_revision<>request.document_revision OR document.status<>'ready'
      OR EXISTS(SELECT FROM app_core.document_versions WHERE tenant_id=p_tenant AND document_id=request.document_id AND status IN('queued','processing'))
      OR EXISTS(SELECT FROM app_core.transfer_request_versions s JOIN app_core.document_versions v ON v.id=s.version_id
        WHERE s.request_id=p_request AND (v.status<>'ready' OR v.owner_id<>request.requester_id OR v.owner_revision<>s.owner_revision)) THEN
      RAISE EXCEPTION 'scope changed' USING ERRCODE='40001';
    END IF;
    UPDATE app_core.document_versions SET owner_id=request.recipient_id WHERE tenant_id=p_tenant AND document_id=request.document_id AND id IN(SELECT version_id FROM app_core.transfer_request_versions WHERE request_id=p_request);
    UPDATE app_core.documents SET owner_id=request.recipient_id WHERE tenant_id=p_tenant AND id=request.document_id;
  END IF;
  result_status:=CASE WHEN p_approve THEN 'approved' ELSE 'rejected' END;
  UPDATE app_core.transfer_requests SET status=result_status,decided_at=clock_timestamp() WHERE id=p_request;
  INSERT INTO app_core.transfer_audit_events(request_id,tenant_id,actor_id,action) VALUES(p_request,p_tenant,p_actor,result_status);
  RETURN jsonb_build_object('request_id',p_request,'status',result_status);
END $$;
ALTER FUNCTION app_core.get_transfer_options(bigint,bigint,bigint) OWNER TO rag_transfer_access;
ALTER FUNCTION app_core.create_transfer(bigint,bigint,bigint,bigint,bigint[],bigint,text) OWNER TO rag_transfer_access;
ALTER FUNCTION app_core.list_transfers(bigint,bigint,bigint) OWNER TO rag_transfer_access;
ALTER FUNCTION app_core.decide_transfer(bigint,bigint,bigint,boolean) OWNER TO rag_transfer_access;
REVOKE ALL ON FUNCTION app_core.get_transfer_options(bigint,bigint,bigint),app_core.create_transfer(bigint,bigint,bigint,bigint,bigint[],bigint,text),app_core.list_transfers(bigint,bigint,bigint),app_core.decide_transfer(bigint,bigint,bigint,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_core.get_transfer_options(bigint,bigint,bigint),app_core.create_transfer(bigint,bigint,bigint,bigint,bigint[],bigint,text),app_core.list_transfers(bigint,bigint,bigint),app_core.decide_transfer(bigint,bigint,bigint,boolean) TO rag_app;
