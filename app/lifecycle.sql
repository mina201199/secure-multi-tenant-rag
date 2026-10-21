CREATE OR REPLACE FUNCTION app_core.change(p_action text,p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
 a bigint:=app_core.actor(); t bigint:=app_core.tenant();
 d app_core.documents%ROWTYPE; v app_core.document_versions%ROWTYPE;
 tr app_core.transfer_requests%ROWTYPE; j app_core.jobs%ROWTYPE;
 u app_core.users%ROWTYPE; x record; item jsonb;
 ids bigint[]; docs bigint[]; versions bigint[]; result_id bigint; job_id bigint; target bigint;
 supervisor bigint; vis text; content text;
BEGIN
 IF a IS NULL THEN RAISE EXCEPTION 'authentication required' USING ERRCODE='42501'; END IF;
 IF p_action IN ('sharing','create_version','archive','retry') THEN
  SELECT * INTO d FROM app_core.documents
   WHERE id=(p->>'document_id')::bigint AND tenant_id=t AND owner_id=a FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'not found' USING ERRCODE='P0002'; END IF;
  IF d.status<>'ready' THEN RAISE EXCEPTION 'state changed' USING ERRCODE='P0001'; END IF;
  PERFORM id FROM app_core.users WHERE id=a AND is_active FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501'; END IF;
 END IF;
 IF p_action='sharing' THEN
  IF d.current_version_id<>(p->>'version_id')::bigint THEN
   RAISE EXCEPTION 'state changed' USING ERRCODE='P0001'; END IF;
  vis:=p->>'visibility';
  IF vis NOT IN ('private','company','selected') THEN RAISE EXCEPTION 'invalid' USING ERRCODE='22023'; END IF;
  ids:=ARRAY(SELECT DISTINCT value::bigint FROM jsonb_array_elements_text(p->'user_ids'));
  IF cardinality(ids)>100 OR (vis<>'selected' AND cardinality(ids)>0) THEN
   RAISE EXCEPTION 'invalid' USING ERRCODE='22023'; END IF;
  PERFORM id FROM app_core.users WHERE id=ANY(ids) AND tenant_id=t AND is_active ORDER BY id FOR SHARE;
  IF (SELECT count(*) FROM app_core.users WHERE id=ANY(ids) AND tenant_id=t AND is_active)<>cardinality(ids)
  THEN RAISE EXCEPTION 'invalid recipient' USING ERRCODE='22023'; END IF;
  UPDATE app_core.document_versions SET visibility=vis WHERE id=d.current_version_id AND owner_id=a;
  IF NOT FOUND THEN RAISE EXCEPTION 'state changed' USING ERRCODE='P0001'; END IF;
  DELETE FROM app_core.version_readers WHERE version_id=d.current_version_id;
  INSERT INTO app_core.version_readers SELECT t,d.current_version_id,unnest(ids);
  UPDATE app_core.documents SET revision=revision+1 WHERE id=d.id;
  INSERT INTO app_core.audit_events(tenant_id,actor_id,action,document_id,version_id,details)
   VALUES(t,a,'sharing',d.id,d.current_version_id,jsonb_build_object('visibility',vis,'user_ids',ids));
  RETURN jsonb_build_object('version_id',d.current_version_id);
 ELSIF p_action='create_version' THEN
  IF EXISTS(SELECT 1 FROM app_core.jobs WHERE document_id=d.id AND status IN ('queued','running')) THEN
   RAISE EXCEPTION 'pending version exists' USING ERRCODE='P0001'; END IF;
  content:=p->>'content';
  IF content IS NULL OR octet_length(content)>1048576 OR length(content)<1 THEN
   RAISE EXCEPTION 'invalid content' USING ERRCODE='22023'; END IF;
  INSERT INTO app_core.document_versions(tenant_id,document_id,owner_id,content,source_hash)
   VALUES(t,d.id,a,content,encode(public.digest(content,'sha256'),'hex')) RETURNING id INTO result_id;
  INSERT INTO app_core.jobs(tenant_id,document_id,version_id,requested_by,base_version_id)
   VALUES(t,d.id,result_id,a,d.current_version_id) RETURNING id INTO job_id;
  UPDATE app_core.documents SET revision=revision+1 WHERE id=d.id;
  INSERT INTO app_core.audit_events(tenant_id,actor_id,action,document_id,version_id)
   VALUES(t,a,'version_created',d.id,result_id);
  RETURN jsonb_build_object('version_id',result_id,'job_id',job_id,'state','processing');
 ELSIF p_action='archive' THEN
  UPDATE app_core.documents SET status='archived',revision=revision+1 WHERE id=d.id;
  UPDATE app_core.jobs SET status='cancelled',lease_hash=NULL,lease_until=NULL,error='文件已封存'
   WHERE document_id=d.id AND status IN ('queued','running');
  UPDATE app_core.document_versions SET state='failed' WHERE document_id=d.id AND state='processing';
  INSERT INTO app_core.audit_events(tenant_id,actor_id,action,document_id) VALUES(t,a,'archived',d.id);
  RETURN jsonb_build_object('status','archived');
 ELSIF p_action='retry' THEN
  SELECT * INTO j FROM app_core.jobs WHERE id=(p->>'job_id')::bigint AND document_id=d.id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'not found' USING ERRCODE='P0002'; END IF;
  IF j.status<>'failed' OR j.attempts>=3 OR j.requested_by<>a OR
    EXISTS(SELECT 1 FROM app_core.jobs WHERE document_id=d.id AND status IN ('queued','running'))
  THEN RAISE EXCEPTION 'cannot retry' USING ERRCODE='P0001'; END IF;
  UPDATE app_core.jobs SET status='queued',base_version_id=d.current_version_id,error=NULL,lease_hash=NULL,lease_until=NULL WHERE id=j.id;
  UPDATE app_core.document_versions SET state='processing' WHERE id=j.version_id AND j.kind='version';
  UPDATE app_core.documents SET revision=revision+1 WHERE id=d.id;
  INSERT INTO app_core.audit_events(tenant_id,actor_id,action,document_id,version_id) VALUES(t,a,'retried',d.id,j.version_id);
  RETURN jsonb_build_object('job_id',j.id,'status','queued');
 ELSIF p_action='history_permissions' THEN
  SELECT * INTO v FROM app_core.document_versions WHERE id=(p->>'version_id')::bigint AND tenant_id=t;
  IF NOT FOUND THEN RAISE EXCEPTION 'not found' USING ERRCODE='P0002'; END IF;
  PERFORM id FROM app_core.documents WHERE id=v.document_id FOR UPDATE;
  SELECT * INTO v FROM app_core.document_versions WHERE id=v.id AND owner_id=a FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'not found' USING ERRCODE='P0002'; END IF;
  IF v.state<>'ready' THEN RAISE EXCEPTION 'not ready' USING ERRCODE='P0001'; END IF;
  ids:=ARRAY(SELECT DISTINCT value::bigint FROM jsonb_array_elements_text(p->'user_ids'));
  IF cardinality(ids)>100 THEN RAISE EXCEPTION 'invalid' USING ERRCODE='22023'; END IF;
  PERFORM id FROM app_core.users WHERE (id=ANY(ids) OR id=a) AND tenant_id=t AND is_active ORDER BY id FOR SHARE;
  IF (SELECT count(*) FROM app_core.users WHERE id=ANY(ids) AND tenant_id=t AND is_active)<>cardinality(ids)
     OR app_core.actor() IS NULL THEN RAISE EXCEPTION 'invalid recipient' USING ERRCODE='22023'; END IF;
  DELETE FROM app_core.history_permissions WHERE version_id=v.id;
  INSERT INTO app_core.history_permissions SELECT t,v.id,unnest(ids),a;
  UPDATE app_core.documents SET revision=revision+1 WHERE id=v.document_id;
  INSERT INTO app_core.audit_events(tenant_id,actor_id,action,document_id,version_id,details)
   VALUES(t,a,'history_permissions',v.document_id,v.id,jsonb_build_object('user_ids',ids));
  RETURN jsonb_build_object('version_id',v.id);
 ELSIF p_action='transfer' THEN
  docs:=ARRAY(SELECT DISTINCT (value->>'document_id')::bigint FROM jsonb_array_elements(p->'scope') ORDER BY 1);
  IF cardinality(docs)<1 OR cardinality(docs)>20 THEN RAISE EXCEPTION 'invalid scope' USING ERRCODE='22023'; END IF;
  PERFORM id FROM app_core.documents WHERE id=ANY(docs) ORDER BY id FOR UPDATE;
  IF (SELECT count(*) FROM app_core.documents WHERE id=ANY(docs) AND tenant_id=t AND owner_id=a AND status='ready')<>cardinality(docs)
  THEN RAISE EXCEPTION 'not found' USING ERRCODE='P0002'; END IF;
  IF EXISTS(SELECT 1 FROM app_core.jobs WHERE document_id=ANY(docs) AND status IN ('queued','running')) THEN
   RAISE EXCEPTION 'pending version exists' USING ERRCODE='P0001'; END IF;
  target:=(p->>'recipient_id')::bigint;
  PERFORM id FROM app_core.users WHERE id IN (a,target) ORDER BY id FOR SHARE;
  SELECT manager_id INTO supervisor FROM app_core.users WHERE id=a AND is_active;
  IF supervisor IS NULL OR supervisor=target OR target=a OR NOT EXISTS(
    SELECT 1 FROM app_core.users WHERE id=target AND tenant_id=t AND is_active) THEN
   RAISE EXCEPTION 'invalid recipient' USING ERRCODE='22023'; END IF;
  PERFORM id FROM app_core.users WHERE id=supervisor AND tenant_id=t AND is_active FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'supervisor unavailable' USING ERRCODE='P0001'; END IF;
  IF length(trim(p->>'reason')) NOT BETWEEN 1 AND 500 THEN RAISE EXCEPTION 'invalid reason' USING ERRCODE='22023'; END IF;
  INSERT INTO app_core.transfer_requests(tenant_id,applicant_id,recipient_id,supervisor_id,reason)
   VALUES(t,a,target,supervisor,p->>'reason') RETURNING id INTO result_id;
  FOR item IN SELECT value FROM jsonb_array_elements(p->'scope') LOOP
   SELECT * INTO d FROM app_core.documents WHERE id=(item->>'document_id')::bigint;
   versions:=ARRAY(SELECT DISTINCT value::bigint FROM jsonb_array_elements_text(item->'version_ids'));
   IF cardinality(versions)<1 OR cardinality(versions)>100 OR NOT d.current_version_id=ANY(versions) OR
     (SELECT count(*) FROM app_core.document_versions WHERE id=ANY(versions) AND document_id=d.id AND owner_id=a AND state='ready')<>cardinality(versions)
   THEN RAISE EXCEPTION 'invalid versions' USING ERRCODE='22023'; END IF;
   INSERT INTO app_core.transfer_documents VALUES(t,result_id,d.id,d.revision,d.current_version_id);
   INSERT INTO app_core.transfer_versions SELECT t,result_id,d.id,unnest(versions);
   INSERT INTO app_core.audit_events(tenant_id,actor_id,action,document_id,details)
    VALUES(t,a,'transfer_requested',d.id,jsonb_build_object('transfer_id',result_id,'recipient_id',target,'version_ids',versions));
  END LOOP;
  RETURN jsonb_build_object('id',result_id,'status','pending');
 ELSIF p_action IN ('approve','reject') THEN
  SELECT * INTO tr FROM app_core.transfer_requests WHERE id=(p->>'transfer_id')::bigint
    AND tenant_id=t AND supervisor_id=a FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'not found' USING ERRCODE='P0002'; END IF;
  IF tr.status<>'pending' OR tr.expires_at<=now() THEN RAISE EXCEPTION 'stale request' USING ERRCODE='P0001'; END IF;
  -- All requests acquire document locks in the same order.
  PERFORM d0.id FROM app_core.documents d0 JOIN app_core.transfer_documents s ON s.document_id=d0.id
   WHERE s.transfer_id=tr.id ORDER BY d0.id FOR UPDATE OF d0;
  PERFORM id FROM app_core.users WHERE id IN (a,tr.applicant_id,tr.recipient_id) ORDER BY id FOR SHARE;
  IF app_core.actor() IS NULL OR NOT EXISTS(SELECT 1 FROM app_core.users WHERE id=tr.applicant_id AND is_active AND manager_id=a)
   OR NOT EXISTS(SELECT 1 FROM app_core.users WHERE id=tr.recipient_id AND is_active AND tenant_id=t)
   OR tr.recipient_id=a THEN RAISE EXCEPTION 'stale request' USING ERRCODE='P0001'; END IF;
  IF EXISTS(SELECT 1 FROM app_core.transfer_documents s JOIN app_core.documents q ON q.id=s.document_id
   WHERE s.transfer_id=tr.id AND (q.owner_id<>tr.applicant_id OR q.status<>'ready' OR q.tenant_id<>t
   OR q.revision<>s.expected_revision OR q.current_version_id<>s.expected_version_id))
   OR EXISTS(SELECT 1 FROM app_core.transfer_versions s JOIN app_core.document_versions q ON q.id=s.version_id
   WHERE s.transfer_id=tr.id AND (q.owner_id<>tr.applicant_id OR q.tenant_id<>t OR q.state<>'ready'))
  THEN RAISE EXCEPTION 'stale request' USING ERRCODE='P0001'; END IF;
  IF p_action='approve' THEN
   UPDATE app_core.documents SET owner_id=tr.recipient_id,revision=revision+1
    WHERE id IN (SELECT document_id FROM app_core.transfer_documents WHERE transfer_id=tr.id);
   UPDATE app_core.document_versions SET owner_id=tr.recipient_id
    WHERE id IN (SELECT version_id FROM app_core.transfer_versions WHERE transfer_id=tr.id);
  END IF;
  UPDATE app_core.transfer_requests SET status=CASE WHEN p_action='approve' THEN 'approved' ELSE 'rejected' END,decided_at=now()
   WHERE id=tr.id;
  FOR x IN SELECT document_id FROM app_core.transfer_documents WHERE transfer_id=tr.id LOOP
   INSERT INTO app_core.audit_events(tenant_id,actor_id,action,document_id,details)
    VALUES(t,a,'transfer_'||p_action,x.document_id,jsonb_build_object('transfer_id',tr.id,'recipient_id',tr.recipient_id));
  END LOOP;
  RETURN jsonb_build_object('id',tr.id,'status',CASE WHEN p_action='approve' THEN 'approved' ELSE 'rejected' END);
 ELSIF p_action='account' THEN
  PERFORM id FROM app_core.users WHERE id IN (a,(p->>'user_id')::bigint) ORDER BY id FOR UPDATE;
  IF NOT EXISTS(SELECT 1 FROM app_core.users WHERE id=a AND is_active AND role='admin') THEN
   RAISE EXCEPTION 'denied' USING ERRCODE='42501'; END IF;
  SELECT * INTO u FROM app_core.users WHERE id=(p->>'user_id')::bigint AND tenant_id=t;
  IF NOT FOUND THEN RAISE EXCEPTION 'not found' USING ERRCODE='P0002'; END IF;
  UPDATE app_core.users SET is_active=(p->>'is_active')::boolean WHERE id=u.id;
  IF NOT (p->>'is_active')::boolean THEN DELETE FROM app_core.sessions WHERE user_id=u.id; END IF;
  INSERT INTO app_core.audit_events(tenant_id,actor_id,action,details)
   VALUES(t,a,CASE WHEN (p->>'is_active')::boolean THEN 'account_enabled' ELSE 'account_disabled' END,jsonb_build_object('user_id',u.id));
  RETURN jsonb_build_object('user_id',u.id,'is_active',(p->>'is_active')::boolean);
 END IF;
 RAISE EXCEPTION 'unknown operation' USING ERRCODE='22023';
END $$;

CREATE OR REPLACE FUNCTION app_core.version_inventory()
RETURNS TABLE(id bigint,document_id bigint,owner_id bigint,state text,visibility text,current_version_id bigint,document_status text,created_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT v.id,v.document_id,v.owner_id,v.state,v.visibility,d.current_version_id,d.status,v.created_at
 FROM app_core.document_versions v JOIN app_core.documents d ON d.id=v.document_id
 WHERE v.tenant_id=app_core.tenant() AND (v.owner_id=app_core.actor() OR d.owner_id=app_core.actor())
 ORDER BY v.document_id,v.created_at,v.id
$$;
CREATE OR REPLACE FUNCTION app_core.history_read(p_id bigint)
RETURNS TABLE(id bigint,document_id bigint,title text,content text,source_hash text,created_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a bigint:=app_core.actor(); t bigint:=app_core.tenant(); v app_core.document_versions%ROWTYPE;
BEGIN
 IF NOT app_core.readable_history(p_id) THEN RAISE EXCEPTION 'not found' USING ERRCODE='P0002'; END IF;
 SELECT * INTO v FROM app_core.document_versions WHERE document_versions.id=p_id;
 PERFORM d.id FROM app_core.documents d WHERE d.id=v.document_id FOR SHARE;
 PERFORM u.id FROM app_core.users u WHERE u.id=a AND u.is_active FOR SHARE;
 IF app_core.actor() IS NULL OR NOT app_core.readable_history(p_id) THEN RAISE EXCEPTION 'not found' USING ERRCODE='P0002'; END IF;
 INSERT INTO app_core.audit_events(tenant_id,actor_id,action,document_id,version_id)
 VALUES(t,a,'history_read',v.document_id,v.id);
 RETURN QUERY SELECT v.id,v.document_id,d.title,v.content,v.source_hash,v.created_at FROM app_core.documents d WHERE d.id=v.document_id;
END $$;

CREATE OR REPLACE FUNCTION app_core.claim_job()
RETURNS TABLE(job_id bigint,version_id bigint,lease_token text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE j app_core.jobs%ROWTYPE; token text;
BEGIN
 -- Bounded leases recover after worker termination; no more than three attempts.
 FOR j IN SELECT * FROM app_core.jobs WHERE status='queued' OR (status='running' AND lease_until<now())
   ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1 LOOP
  IF j.attempts>=3 THEN
   UPDATE app_core.jobs SET status='failed',lease_hash=NULL,lease_until=NULL,error='已達重試上限' WHERE id=j.id;
   UPDATE app_core.document_versions SET state='failed' WHERE id=j.version_id AND j.kind='version';
   RETURN;
  END IF;
  token:=encode(public.gen_random_bytes(32),'hex');
  UPDATE app_core.jobs SET status='running',attempts=attempts+1,lease_hash=encode(public.digest(token,'sha256'),'hex'),
    lease_until=now()+interval '300 seconds' WHERE id=j.id;
  RETURN QUERY SELECT j.id,j.version_id,token;
 END LOOP;
END $$;
CREATE OR REPLACE FUNCTION app_core.finish_job(p_job bigint,p_token text,p_success boolean)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE j app_core.jobs%ROWTYPE; d app_core.documents%ROWTYPE; valid boolean;
BEGIN
 SELECT * INTO j FROM app_core.jobs WHERE id=p_job;
 IF NOT FOUND THEN RETURN false; END IF;
 -- Document before job: same order as archive/retry, preventing deadlocks.
 SELECT * INTO d FROM app_core.documents WHERE id=j.document_id FOR UPDATE;
 SELECT * INTO j FROM app_core.jobs WHERE id=p_job FOR UPDATE;
 IF j.status<>'running' OR j.lease_until<=now() OR
    j.lease_hash IS DISTINCT FROM encode(public.digest(p_token,'sha256'),'hex') THEN RETURN false; END IF;
 PERFORM id FROM app_core.users WHERE id=j.requested_by FOR SHARE;
 valid:=d.status='ready' AND d.owner_id=j.requested_by AND d.current_version_id=j.base_version_id
    AND EXISTS(SELECT 1 FROM app_core.users WHERE id=j.requested_by AND is_active)
    AND EXISTS(SELECT 1 FROM app_core.document_versions WHERE id=j.version_id AND owner_id=j.requested_by
     AND (state='processing' OR (j.kind='index' AND state='ready')))
    AND EXISTS(SELECT 1 FROM app_core.index_manifests m WHERE m.version_id=j.version_id
     AND m.chunk_count=(SELECT count(*) FROM app_core.chunks c WHERE c.version_id=j.version_id AND c.profile=m.profile));
 IF NOT valid OR NOT p_success THEN
  UPDATE app_core.jobs SET status='failed',stage='failed',error=coalesce(error,'版本處理失敗或狀態已變更'),lease_hash=NULL,lease_until=NULL WHERE id=j.id;
  UPDATE app_core.document_versions SET state='failed' WHERE id=j.version_id AND state='processing' AND j.kind='version';
  INSERT INTO app_core.audit_events(tenant_id,actor_id,action,document_id,version_id)
   VALUES(j.tenant_id,j.requested_by,'version_failed',d.id,j.version_id);
  RETURN false;
 END IF;
 UPDATE app_core.document_versions SET state='ready' WHERE id=j.version_id;
 UPDATE app_core.documents SET current_version_id=j.version_id,revision=revision+1 WHERE id=d.id;
 UPDATE app_core.jobs SET status='done',stage='searchable',error=NULL,lease_hash=NULL,lease_until=NULL WHERE id=j.id;
 INSERT INTO app_core.audit_events(tenant_id,actor_id,action,document_id,version_id)
   VALUES(j.tenant_id,j.requested_by,'version_activated',d.id,j.version_id);
 RETURN true;
END $$;
