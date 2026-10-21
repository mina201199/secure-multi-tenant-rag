-- Every privileged function has a fixed search_path and schema-qualified tables.
-- Runtime never receives the context signing secret or ownership of these functions.
CREATE OR REPLACE FUNCTION app_core.signed_actor() RETURNS bigint
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a text; t text; h text; sig text; secret text;
BEGIN
 a:=current_setting('app.actor_id',true); t:=current_setting('app.tenant_id',true);
 h:=current_setting('app.session_hash',true); sig:=current_setting('app.context_signature',true);
 IF a IS NULL OR t IS NULL OR h IS NULL OR sig IS NULL THEN RETURN NULL; END IF;
 SELECT value INTO secret FROM app_private.runtime_secret WHERE id;
 IF sig IS DISTINCT FROM encode(public.hmac('db-context:'||a||':'||t||':'||h,secret,'sha256'),'hex')
 THEN RETURN NULL; END IF;
 RETURN (SELECT id FROM app_core.users WHERE id=a::bigint AND tenant_id=t::bigint AND is_active);
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION app_core.actor() RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT s.user_id FROM app_core.sessions s
 WHERE s.user_id=app_core.signed_actor()
 AND s.token_hash=current_setting('app.session_hash',true)
 AND s.expires_at>now() AND s.last_seen_at>now()-interval '30 minutes'
$$;
CREATE OR REPLACE FUNCTION app_core.tenant() RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT tenant_id FROM app_core.users WHERE id=app_core.actor()
$$;
CREATE OR REPLACE FUNCTION app_core.auth_candidate(p_username text)
RETURNS TABLE(id bigint,tenant_id bigint,password_hash text,is_active boolean)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT u.id,u.tenant_id,u.password_hash,u.is_active FROM app_core.users u WHERE username=p_username
$$;
CREATE OR REPLACE FUNCTION app_core.auth_resolve(p_hash text)
RETURNS TABLE(id bigint,tenant_id bigint,username text,display_name text,role text,tenant_name text,token_hash text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 UPDATE app_core.sessions s SET last_seen_at=now()
 WHERE s.token_hash=p_hash AND s.expires_at>now()
 AND s.last_seen_at>now()-interval '30 minutes'
 AND EXISTS(SELECT 1 FROM app_core.users u WHERE u.id=s.user_id AND u.is_active);
 IF NOT FOUND THEN RETURN; END IF;
 RETURN QUERY SELECT u.id,u.tenant_id,u.username,u.display_name,u.role,t.name,s.token_hash
 FROM app_core.sessions s JOIN app_core.users u ON u.id=s.user_id
 JOIN app_core.tenants t ON t.id=u.tenant_id WHERE s.token_hash=p_hash AND u.is_active;
END $$;
CREATE OR REPLACE FUNCTION app_core.auth_issue() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a bigint:=app_core.signed_actor();
BEGIN
 IF a IS NULL THEN RAISE EXCEPTION 'denied' USING ERRCODE='42501'; END IF;
 INSERT INTO app_core.sessions VALUES(current_setting('app.session_hash'),a,now(),now(),now()+interval '8 hours');
END $$;
CREATE OR REPLACE FUNCTION app_core.auth_logout(p_hash text) RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 DELETE FROM app_core.sessions WHERE token_hash=p_hash
$$;
CREATE OR REPLACE FUNCTION app_core.owns_document(p_id bigint) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM app_core.documents WHERE id=p_id AND owner_id=app_core.actor() AND tenant_id=app_core.tenant())
$$;
CREATE OR REPLACE FUNCTION app_core.owns_version(p_id bigint) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM app_core.document_versions WHERE id=p_id AND owner_id=app_core.actor() AND tenant_id=app_core.tenant())
$$;
CREATE OR REPLACE FUNCTION app_core.readable_current(p_id bigint) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(
 SELECT 1 FROM app_core.document_versions v JOIN app_core.documents d ON d.current_version_id=v.id
 WHERE v.id=p_id AND v.tenant_id=app_core.tenant() AND d.status='ready' AND v.state='ready'
 AND (v.owner_id=app_core.actor() OR v.visibility='company' OR
      (v.visibility='selected' AND EXISTS(SELECT 1 FROM app_core.version_readers r
       WHERE r.version_id=v.id AND r.tenant_id=v.tenant_id AND r.user_id=app_core.actor()))))
$$;
CREATE OR REPLACE FUNCTION app_core.readable_history(p_id bigint) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(
 SELECT 1 FROM app_core.document_versions v JOIN app_core.documents d ON d.id=v.document_id
 JOIN app_core.history_permissions h ON h.version_id=v.id AND h.tenant_id=v.tenant_id
 WHERE v.id=p_id AND v.tenant_id=app_core.tenant() AND v.state='ready'
 AND (d.current_version_id<>v.id OR d.status='archived') AND h.user_id=app_core.actor())
$$;
CREATE OR REPLACE FUNCTION app_core.valid_job(p_version bigint) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 RETURN EXISTS(SELECT 1 FROM app_core.jobs j JOIN app_core.documents d ON d.id=j.document_id
 JOIN app_core.users u ON u.id=j.requested_by
 WHERE j.id=nullif(current_setting('app.job_id',true),'')::bigint AND j.version_id=p_version
 AND j.lease_hash=encode(public.digest(current_setting('app.job_token',true),'sha256'),'hex')
 AND j.status='running' AND j.lease_until>now() AND d.status='ready'
 AND d.owner_id=j.requested_by AND u.is_active);
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN RETURN false;
END $$;

DO $$
DECLARE tab text; p record;
BEGIN
 FOREACH tab IN ARRAY ARRAY['tenants','users','sessions','documents','document_versions','version_readers',
 'history_permissions','jobs','transfer_requests','transfer_documents','transfer_versions','audit_events']
 LOOP
  EXECUTE format('ALTER TABLE app_core.%I ENABLE ROW LEVEL SECURITY',tab);
  EXECUTE format('ALTER TABLE app_core.%I FORCE ROW LEVEL SECURITY',tab);
  FOR p IN SELECT policyname FROM pg_policies WHERE schemaname='app_core' AND tablename=tab LOOP
   EXECUTE format('DROP POLICY %I ON app_core.%I',p.policyname,tab);
  END LOOP;
 END LOOP;
END $$;
CREATE POLICY tenant_read ON app_core.tenants FOR SELECT TO rag_app USING(id=app_core.tenant());
CREATE POLICY user_read ON app_core.users FOR SELECT TO rag_app USING(tenant_id=app_core.tenant());
CREATE POLICY session_read ON app_core.sessions FOR SELECT TO rag_app
 USING(user_id=app_core.actor() AND token_hash=current_setting('app.session_hash',true));
CREATE POLICY document_read ON app_core.documents FOR SELECT TO rag_app
 USING(tenant_id=app_core.tenant() AND (owner_id=app_core.actor() OR app_core.readable_current(current_version_id)
 OR (current_setting('app.mode',true)='history' AND EXISTS(
 SELECT 1 FROM app_core.document_versions v WHERE v.document_id=documents.id AND app_core.readable_history(v.id)))));
CREATE POLICY version_read ON app_core.document_versions FOR SELECT TO rag_app
 USING(tenant_id=app_core.tenant() AND (app_core.readable_current(id)
 OR (state IN ('processing','failed') AND owner_id=app_core.actor() AND app_core.owns_document(document_id))
 OR (current_setting('app.mode',true)='history' AND app_core.readable_history(id))));
CREATE POLICY reader_read ON app_core.version_readers FOR SELECT TO rag_app
 USING(tenant_id=app_core.tenant() AND (user_id=app_core.actor() OR app_core.owns_version(version_id)));
CREATE POLICY history_grant_read ON app_core.history_permissions FOR SELECT TO rag_app
 USING(tenant_id=app_core.tenant() AND (user_id=app_core.actor() OR app_core.owns_version(version_id)));
CREATE POLICY job_read ON app_core.jobs FOR SELECT TO rag_app
 USING(tenant_id=app_core.tenant() AND app_core.owns_document(document_id));
CREATE POLICY transfer_read ON app_core.transfer_requests FOR SELECT TO rag_app
 USING(tenant_id=app_core.tenant() AND (applicant_id=app_core.actor() OR
 (supervisor_id=app_core.actor() AND status='pending')));
CREATE POLICY transfer_scope_read ON app_core.transfer_documents FOR SELECT TO rag_app
 USING(tenant_id=app_core.tenant() AND EXISTS(SELECT 1 FROM app_core.transfer_requests t
 WHERE t.id=transfer_id AND t.applicant_id=app_core.actor()));
CREATE POLICY transfer_version_read ON app_core.transfer_versions FOR SELECT TO rag_app
 USING(tenant_id=app_core.tenant() AND EXISTS(SELECT 1 FROM app_core.transfer_requests t
 WHERE t.id=transfer_id AND t.applicant_id=app_core.actor()));
CREATE POLICY audit_read ON app_core.audit_events FOR SELECT TO rag_app
 USING(tenant_id=app_core.tenant() AND actor_id=app_core.actor());
CREATE POLICY worker_version_read ON app_core.document_versions FOR SELECT TO rag_worker USING(app_core.valid_job(id));
CREATE POLICY worker_job_read ON app_core.jobs FOR SELECT TO rag_worker USING(app_core.valid_job(version_id));
-- No INSERT/UPDATE/DELETE policies: raw DML is denied by RLS. Only the bounded
-- lifecycle functions below can mutate these tables, after checking actor/scope.
