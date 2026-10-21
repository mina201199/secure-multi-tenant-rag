REVOKE ALL ON ALL TABLES IN SCHEMA app_core FROM rag_app,rag_worker;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA app_core FROM rag_app,rag_worker;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA app_core FROM PUBLIC,rag_app,rag_worker;
GRANT USAGE ON SCHEMA app_core TO rag_app,rag_worker;
GRANT SELECT ON app_core.tenants,app_core.documents,app_core.document_versions,
 app_core.version_readers,app_core.history_permissions,app_core.jobs,
 app_core.transfer_documents,app_core.transfer_versions TO rag_app;
GRANT SELECT(id,tenant_id,username,display_name,role,is_active,manager_id) ON app_core.users TO rag_app;
GRANT SELECT(id,applicant_id,recipient_id,reason,status) ON app_core.transfer_requests TO rag_app;
GRANT SELECT,INSERT,UPDATE,DELETE ON app_core.login_limits TO rag_app;
-- Explicit RLS write-denial coverage. These privileges cannot bypass the absent write policies.
GRANT INSERT,UPDATE,DELETE ON app_core.version_readers,app_core.history_permissions TO rag_app;
GRANT EXECUTE ON FUNCTION app_core.signed_actor(),app_core.actor(),app_core.tenant(),
 app_core.auth_candidate(text),app_core.auth_resolve(text),app_core.auth_issue(),app_core.auth_logout(text),
 app_core.owns_document(bigint),app_core.owns_version(bigint),
 app_core.readable_current(bigint),app_core.readable_history(bigint),
 app_core.change(text,jsonb),app_core.version_inventory(),app_core.history_read(bigint) TO rag_app;
GRANT SELECT ON app_core.document_versions,app_core.jobs TO rag_worker;
GRANT EXECUTE ON FUNCTION app_core.valid_job(bigint),app_core.claim_job(),app_core.finish_job(bigint,text,boolean) TO rag_worker;
GRANT SELECT ON app_core.source_files,app_core.parsed_sources,app_core.chunks,app_core.index_manifests TO rag_app,rag_worker;
GRANT SELECT(id,operation,model,price_revision,month,reserved_microtwd,charged_microtwd,status,input_tokens,output_tokens,elapsed_ms,created_at)
 ON app_core.model_usage TO rag_app;
GRANT EXECUTE ON FUNCTION app_core.upload(bigint,text,text,text,text,bytea),app_core.reindex(bigint) TO rag_app;
GRANT EXECUTE ON FUNCTION app_core.ingestion_inventory() TO rag_app;
GRANT EXECUTE ON FUNCTION app_core.worker_stage(bigint,text,text),app_core.save_parsed(bigint,text,jsonb,text),
 app_core.publish_chunks(bigint,text,jsonb) TO rag_worker;
GRANT EXECUTE ON FUNCTION app_core.reserve_usage(uuid,text,integer,integer),
 app_core.settle_usage(uuid,integer,integer,integer,boolean) TO rag_app,rag_worker;
