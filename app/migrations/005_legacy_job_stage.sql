-- M2 jobs did not run the M3 indexing pipeline; do not label them searchable.
UPDATE app_core.jobs SET stage='legacy_complete' WHERE status='done' AND stage='queued';
UPDATE app_core.jobs SET stage=status WHERE status IN ('failed','cancelled') AND stage='queued';
