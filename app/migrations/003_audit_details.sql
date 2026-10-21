ALTER TABLE app_core.audit_events ADD COLUMN details jsonb NOT NULL DEFAULT '{}'::jsonb
 CHECK(jsonb_typeof(details)='object');
-- Older bootstrap code could recreate this retired, empty M1 table on rerun.
DROP TABLE IF EXISTS app_core.document_readers;
