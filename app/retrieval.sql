-- Fixed search paths and bounded operations: the worker never gets general write access.
DO $$
DECLARE tab text; p record;
BEGIN
 FOREACH tab IN ARRAY ARRAY['source_files','parsed_sources','chunks','index_manifests','model_usage'] LOOP
  EXECUTE format('ALTER TABLE app_core.%I ENABLE ROW LEVEL SECURITY',tab);
  EXECUTE format('ALTER TABLE app_core.%I FORCE ROW LEVEL SECURITY',tab);
  FOR p IN SELECT policyname FROM pg_policies WHERE schemaname='app_core' AND tablename=tab LOOP
   EXECUTE format('DROP POLICY %I ON app_core.%I',p.policyname,tab);
  END LOOP;
 END LOOP;
END $$;
CREATE POLICY source_current ON app_core.source_files FOR SELECT TO rag_app
 USING(tenant_id=app_core.tenant() AND app_core.readable_current(version_id));
CREATE POLICY parsed_current ON app_core.parsed_sources FOR SELECT TO rag_app
 USING(tenant_id=app_core.tenant() AND app_core.readable_current(version_id));
CREATE POLICY chunk_current ON app_core.chunks FOR SELECT TO rag_app
 USING(tenant_id=app_core.tenant() AND app_core.readable_current(version_id));
CREATE POLICY manifest_current ON app_core.index_manifests FOR SELECT TO rag_app
 USING(tenant_id=app_core.tenant() AND app_core.readable_current(version_id));
CREATE POLICY source_worker ON app_core.source_files FOR SELECT TO rag_worker USING(app_core.valid_job(version_id));
CREATE POLICY parsed_worker ON app_core.parsed_sources FOR SELECT TO rag_worker USING(app_core.valid_job(version_id));
CREATE POLICY chunk_worker ON app_core.chunks FOR SELECT TO rag_worker USING(app_core.valid_job(version_id));
CREATE POLICY manifest_worker ON app_core.index_manifests FOR SELECT TO rag_worker USING(app_core.valid_job(version_id));
CREATE POLICY usage_own ON app_core.model_usage FOR SELECT TO rag_app
 USING(tenant_id=app_core.tenant() AND actor_id=app_core.actor() AND job_id IS NULL);

CREATE OR REPLACE FUNCTION app_core.ingestion_inventory() RETURNS SETOF jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('document_id',d.id,'title',d.title,'category',d.category,'tenant_id',v.tenant_id,
 'owner_id',v.owner_id,'version_id',v.id,'state',v.state,'document_status',d.status,'visibility',v.visibility,
 'filename',coalesce(s.filename,'貼上文字'),'media_type',coalesce(s.media_type,'text/plain'),
 'bytes',coalesce(octet_length(s.body),octet_length(v.content)),'source_hash',v.source_hash,
 'parser',p.parser,'indexed',m.version_id IS NOT NULL,'profile',m.profile,'chunks',m.chunk_count,
 'stage',j.stage,'job_status',j.status)
 FROM app_core.document_versions v JOIN app_core.documents d ON d.id=v.document_id
 LEFT JOIN app_core.source_files s ON s.version_id=v.id
 LEFT JOIN app_core.parsed_sources p ON p.version_id=v.id
 LEFT JOIN app_core.index_manifests m ON m.version_id=v.id
 LEFT JOIN app_core.jobs j ON j.version_id=v.id
 WHERE v.tenant_id=app_core.tenant() AND (v.owner_id=app_core.actor() OR d.owner_id=app_core.actor())
 ORDER BY d.id,v.id
$$;

CREATE OR REPLACE FUNCTION app_core.upload(p_document bigint,p_title text,p_category text,p_filename text,p_media text,p_body bytea)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a bigint:=app_core.actor(); t bigint:=app_core.tenant(); d bigint; v bigint; j bigint; base bigint; content text;
BEGIN
 IF a IS NULL THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501'; END IF;
 IF octet_length(p_body) NOT BETWEEN 1 AND 10485760 OR p_media NOT IN ('application/pdf','text/plain')
 OR length(p_filename) NOT BETWEEN 1 AND 180 OR length(trim(p_title)) NOT BETWEEN 1 AND 180
 OR length(trim(p_category)) NOT BETWEEN 1 AND 80 THEN RAISE EXCEPTION 'invalid' USING ERRCODE='22023'; END IF;
 IF p_media='application/pdf' AND substring(p_body FROM 1 FOR 5)<>convert_to('%PDF-','UTF8')
 THEN RAISE EXCEPTION 'invalid PDF' USING ERRCODE='22023'; END IF;
 content:=CASE WHEN p_media='text/plain' THEN convert_from(p_body,'UTF8') ELSE '' END;
 IF octet_length(content)>1048576 THEN RAISE EXCEPTION 'invalid' USING ERRCODE='22023'; END IF;
 IF p_document IS NOT NULL THEN
  SELECT id,current_version_id INTO d,base FROM app_core.documents
   WHERE id=p_document AND tenant_id=t AND owner_id=a AND status='ready' FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'not found' USING ERRCODE='P0002'; END IF;
  IF EXISTS(SELECT 1 FROM app_core.jobs WHERE document_id=d AND status IN ('queued','running')) THEN
   RAISE EXCEPTION 'pending' USING ERRCODE='P0001'; END IF;
 ELSE d:=nextval('app_core.upload_document_id'); END IF;
 PERFORM id FROM app_core.users WHERE id=a AND is_active FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501'; END IF;
 v:=nextval(pg_get_serial_sequence('app_core.document_versions','id'));
 IF p_document IS NULL THEN
  INSERT INTO app_core.documents(id,tenant_id,owner_id,title,category,status,current_version_id)
   VALUES(d,t,a,trim(p_title),trim(p_category),'ready',v);
  base:=v;
 END IF;
 INSERT INTO app_core.document_versions(id,tenant_id,document_id,owner_id,content,source_hash)
  VALUES(v,t,d,a,content,encode(public.digest(p_body,'sha256'),'hex'));
 INSERT INTO app_core.source_files VALUES(t,d,v,p_filename,p_media,p_body,encode(public.digest(p_body,'sha256'),'hex'));
 INSERT INTO app_core.jobs(tenant_id,document_id,version_id,requested_by,base_version_id)
  VALUES(t,d,v,a,base) RETURNING id INTO j;
 UPDATE app_core.documents SET revision=revision+1 WHERE id=d;
 INSERT INTO app_core.audit_events(tenant_id,actor_id,action,document_id,version_id) VALUES(t,a,'uploaded',d,v);
 RETURN jsonb_build_object('document_id',d,'version_id',v,'job_id',j,'state','processing');
END $$;

CREATE OR REPLACE FUNCTION app_core.reindex(p_document bigint) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE d app_core.documents%ROWTYPE; j bigint;
BEGIN
 SELECT * INTO d FROM app_core.documents WHERE id=p_document AND tenant_id=app_core.tenant()
  AND owner_id=app_core.actor() AND status='ready' FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not found' USING ERRCODE='P0002'; END IF;
 IF EXISTS(SELECT 1 FROM app_core.index_manifests WHERE version_id=d.current_version_id)
 OR EXISTS(SELECT 1 FROM app_core.jobs WHERE document_id=d.id AND status IN ('queued','running'))
 THEN RAISE EXCEPTION 'already indexed or pending' USING ERRCODE='P0001'; END IF;
 INSERT INTO app_core.jobs(tenant_id,document_id,version_id,requested_by,base_version_id,kind)
  VALUES(d.tenant_id,d.id,d.current_version_id,d.owner_id,d.current_version_id,'index')
  ON CONFLICT(version_id) DO NOTHING RETURNING id INTO j;
 IF j IS NULL THEN RAISE EXCEPTION 'use bounded retry' USING ERRCODE='P0001'; END IF;
 RETURN jsonb_build_object('job_id',j,'state','processing');
END $$;

CREATE OR REPLACE FUNCTION app_core.worker_stage(p_version bigint,p_stage text,p_error text DEFAULT NULL) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT app_core.valid_job(p_version) THEN RETURN false; END IF;
 IF p_stage NOT IN ('parsing','embedding','publishing','failed') THEN RETURN false; END IF;
 UPDATE app_core.jobs SET stage=p_stage,
  error=CASE WHEN p_stage='failed' THEN left(p_error,160) ELSE NULL END,
  lease_until=now()+interval '300 seconds'
 WHERE version_id=p_version;
 RETURN true;
END $$;

CREATE OR REPLACE FUNCTION app_core.save_parsed(p_version bigint,p_content text,p_locations jsonb,p_parser text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v app_core.document_versions%ROWTYPE;
BEGIN
 IF NOT app_core.valid_job(p_version) THEN RETURN false; END IF;
 IF length(p_content) NOT BETWEEN 1 AND 60000 OR jsonb_array_length(p_locations) NOT BETWEEN 1 AND 1000
 THEN RAISE EXCEPTION 'size' USING ERRCODE='22023'; END IF;
 SELECT * INTO v FROM app_core.document_versions WHERE id=p_version;
 INSERT INTO app_core.parsed_sources VALUES(v.tenant_id,v.document_id,v.id,p_content,p_locations,p_parser)
 ON CONFLICT(version_id) DO NOTHING;
 RETURN true;
END $$;

CREATE OR REPLACE FUNCTION app_core.publish_chunks(p_version bigint,p_profile text,p_chunks jsonb) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v app_core.document_versions%ROWTYPE; c jsonb; n integer:=0; source text; location jsonb;
BEGIN
 SELECT * INTO v FROM app_core.document_versions WHERE id=p_version;
 IF NOT FOUND THEN RETURN false; END IF;
 PERFORM id FROM app_core.documents WHERE id=v.document_id FOR UPDATE;
 IF NOT app_core.valid_job(p_version) THEN RETURN false; END IF;
 IF p_profile NOT IN ('openai:text-embedding-3-small:1536:cl100k:600:60:v1','test:hash:1536:cl100k:600:60:v1')
 OR jsonb_array_length(p_chunks) NOT BETWEEN 1 AND 64
 THEN RAISE EXCEPTION 'invalid profile or count' USING ERRCODE='22023'; END IF;
 IF EXISTS(SELECT 1 FROM app_core.index_manifests WHERE version_id=p_version) THEN RETURN true; END IF;
 FOR c IN SELECT value FROM jsonb_array_elements(p_chunks) LOOP
  SELECT e.value INTO location FROM app_core.parsed_sources s,
   jsonb_array_elements(s.locations) e WHERE s.version_id=p_version
   AND e.value->>'kind'=c->>'location_kind' AND (e.value->>'number')::int=(c->>'location_no')::int;
  source:=location->>'text';
  IF source IS NULL OR (c->>'ordinal')::int<>n OR
   substring(source FROM (c->>'start_offset')::int+1 FOR (c->>'end_offset')::int-(c->>'start_offset')::int)
    IS DISTINCT FROM c->>'content'
  THEN RAISE EXCEPTION 'invalid source mapping' USING ERRCODE='23514'; END IF;
  INSERT INTO app_core.chunks(tenant_id,document_id,version_id,ordinal,location_kind,location_no,start_offset,end_offset,content,tokens,profile,embedding)
   VALUES(v.tenant_id,v.document_id,v.id,n,c->>'location_kind',(c->>'location_no')::int,
    (c->>'start_offset')::int,(c->>'end_offset')::int,c->>'content',(c->>'tokens')::int,p_profile,(c->>'embedding')::public.vector);
  n:=n+1;
 END LOOP;
 INSERT INTO app_core.index_manifests VALUES(v.tenant_id,v.document_id,v.id,p_profile,n,now());
 RETURN true;
END $$;

CREATE OR REPLACE FUNCTION app_core.reserve_usage(p_id uuid,p_operation text,p_input integer,p_output integer)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a bigint:=app_core.actor(); t bigint:=app_core.tenant(); jid bigint; credential text; amount bigint; spent bigint;
BEGIN
 IF a IS NOT NULL THEN credential:=current_setting('app.session_hash',true);
 ELSE
  SELECT j.id,j.requested_by,j.tenant_id,j.lease_hash INTO jid,a,t,credential FROM app_core.jobs j
   WHERE app_core.valid_job(j.version_id);
 END IF;
 IF a IS NULL THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501'; END IF;
 IF p_operation NOT IN ('embedding','answer') OR p_input NOT BETWEEN 1 AND 100000 OR
 (p_operation='embedding' AND p_output<>0) OR (p_operation='answer' AND (p_output<>1024 OR jid IS NOT NULL))
 THEN RAISE EXCEPTION 'invalid' USING ERRCODE='22023'; END IF;
 -- micro-TWD: $/million tokens * USD/TWD 40 * buffer 1.25. Fixed, no caller rates.
 amount:=ceil(CASE WHEN p_operation='embedding' THEN p_input*1.0 ELSE p_input*37.5+p_output*225 END);
 PERFORM pg_advisory_xact_lock(726402);
 SELECT coalesce(sum(charged_microtwd),0) INTO spent FROM app_core.model_usage
  WHERE month=date_trunc('month',now() AT TIME ZONE 'Asia/Taipei')::date;
 IF spent+amount>270000000 OR
 (SELECT count(*) FROM app_core.model_usage WHERE status='reserved')>=4 OR
 (SELECT count(*) FROM app_core.model_usage WHERE actor_id=a AND created_at>now()-interval '1 minute')>=20
 THEN RAISE EXCEPTION 'budget or rate limit' USING ERRCODE='P0001'; END IF;
 INSERT INTO app_core.model_usage(id,tenant_id,actor_id,job_id,credential_hash,operation,model,reserved_microtwd,charged_microtwd)
 VALUES(p_id,t,a,jid,credential,p_operation,
 CASE WHEN p_operation='embedding' THEN 'text-embedding-3-small' ELSE 'gpt-5.4-mini' END,amount,amount);
 RETURN amount;
END $$;
CREATE OR REPLACE FUNCTION app_core.settle_usage(p_id uuid,p_input integer,p_output integer,p_elapsed integer,p_success boolean)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u app_core.model_usage%ROWTYPE; cost bigint;
BEGIN
 SELECT * INTO u FROM app_core.model_usage WHERE id=p_id FOR UPDATE;
 IF NOT FOUND OR u.status<>'reserved' THEN RETURN false; END IF;
 IF u.job_id IS NULL THEN
  IF app_core.signed_actor() IS DISTINCT FROM u.actor_id OR current_setting('app.session_hash',true) IS DISTINCT FROM u.credential_hash THEN RETURN false; END IF;
 ELSE
  IF current_setting('app.job_id',true) IS DISTINCT FROM u.job_id::text OR
   encode(public.digest(current_setting('app.job_token',true),'sha256'),'hex') IS DISTINCT FROM u.credential_hash THEN RETURN false; END IF;
 END IF;
 cost:=ceil(CASE WHEN u.operation='embedding' THEN p_input*1.0 ELSE p_input*37.5+p_output*225 END);
 IF p_input IS NULL OR p_input<0 OR p_output IS NULL OR p_output<0 OR cost>u.reserved_microtwd THEN p_success:=false; END IF;
 UPDATE app_core.model_usage SET status=CASE WHEN p_success THEN 'settled' ELSE 'uncertain' END,
 charged_microtwd=CASE WHEN p_success THEN cost ELSE reserved_microtwd END,
 input_tokens=p_input,output_tokens=p_output,elapsed_ms=greatest(0,p_elapsed) WHERE id=p_id;
 RETURN true;
END $$;

-- Also use extracted PDF text in the separately authorized/audited history view.
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
 INSERT INTO app_core.audit_events(tenant_id,actor_id,action,document_id,version_id) VALUES(t,a,'history_read',v.document_id,v.id);
 RETURN QUERY SELECT v.id,v.document_id,d.title,coalesce(s.content,v.content),v.source_hash,v.created_at
 FROM app_core.documents d LEFT JOIN app_core.parsed_sources s ON s.version_id=v.id WHERE d.id=v.document_id;
END $$;
