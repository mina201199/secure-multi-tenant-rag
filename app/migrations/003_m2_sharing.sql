-- M2-02: column-scoped sharing writes; lifecycle pointers and users stay read-only.
GRANT UPDATE (visibility) ON app_core.document_versions TO rag_app;
GRANT INSERT, DELETE ON app_core.version_readers TO rag_app;
