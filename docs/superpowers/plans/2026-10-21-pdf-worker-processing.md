# PDF worker processing implementation plan

> **For agentic workers:** Use subagent-driven-development for isolated implementation tasks and independent review. The user authorized PDF processing on the existing TXT version/worker/recovery architecture, tests and a PR; execute through completion.

**Goal:** Accept bounded text PDFs into the current durable version workflow, preserve physical page evidence, and publish only validated results through the existing ownership/lease/atomicity controls.

**Architecture:** Keep the same queue and restricted worker. Add immutable source format, overloaded create/finish capabilities, persisted PDF page JSON on document_versions, a constrained subprocess parser and API/HTML support. TXT APIs and fingerprints remain compatible. No separate queue or direct worker table grants.

**Tech stack:** PostgreSQL 16, Python 3.12 Linux Docker, pypdf 6.21.1, FastAPI/Jinja, pytest with real PostgreSQL.

## Binding design and scope

- TXT remains 1 MiB / 200,000 characters. PDF is at most 2 MiB, 20 physical pages and 200,000 extracted characters. Header and suffix must agree; MIME is not trusted. PDF parsing occurs only in worker, not request/DB transactions.
- Native text PDF only. Reject encrypted, empty/scanned/unreadable pages, invalid text, active forms/actions/attachments, rotated/complex layout that cannot be conservatively validated. Preserve page order and numeric/part text; no OCR or semantic rewriting. Do not claim arbitrary layout correctness.
- Parser child gets only PDF bytes, no DB/session secrets or inherited descriptors. Linux resource limits: 512 MiB address space, 10 CPU seconds, 15 second wall timeout, bounded input/output. Unsupported containment environment fails closed. Nonzero exit/timeout/parse failure must not kill the durable worker or publish partial text.
- Parsed pages contract: JSON array of {"page": 1, "text": "..."}, consecutive 1-based physical pages. Persist pages and parser version on the target version, bind them to source digest. Reader API/HTML uses existing current-version authorization.
- New DB create signature app_core.create_version(tenant,actor,document,expected,source,key,format). Existing six-argument TXT call remains valid with identical fingerprint. version_service.create_version(..., source_format='txt') is compatible.
- New complete capability app_core.complete_pdf_version_job(job uuid,lease uuid,source_sha256 text,pages jsonb); wrapper version_service.complete_pdf_job(connection,job_id,lease_token,source_sha256,pages). Only worker/processor can execute. Validate bounded page shape, sequential pages, nonempty safe text, exact source format/digest. Publisher rechecks owner/activity/archive/base/lease before atomic content+pages+pointer+job commit.
- New failure capability app_core.fail_pdf_version_job(job uuid,lease uuid,error text); wrapper fail_pdf_job(...). Error allowlist uses safe fixed PDF codes only. Existing TXT complete/fail signatures remain intact; TXT complete cannot publish PDF.
- claim_version_job adds source_format. Recovery remains format-independent, with the same 5-minute lease, 10/20-second backoff and maximum 3 claims. Stop/kill during extraction leaves lease for recovery, stale tokens cannot publish; semantic parse failures terminate with a safe code.
- Page fields: document_versions.source_pages jsonb NULL for TXT/seed; parser_version text NULL for TXT/seed. SQL-created PDF page content joined with two newlines; API includes source_pages/parser_version only via existing authorized effective-version query.
- Existing lack of RLS, OCR, download API, chunks/embeddings, RAG, paid calls and host auto-restart remains documented. No existing Issue is closed solely by this PR.

## Task 1: Migration and restricted PDF publication

Files: app/migrations/006_pdf_processing.sql; tests/test_pdf_versions.py. Do not rewrite 001–005.

- [x] Write DB tests first. Example: create_version(...,pdfBytes,key,'pdf') queues private/empty-ACL source_format pdf; complete_pdf_version_job with claimed token and digest stores pages and switches only at commit. Existing function absence must fail initially.
- [x] Add default txt format with per-format byte constraint, PDF header checks, compatible create wrapper/fingerprint, source format in claims, page metadata columns, strict parsed-result validation and terminal failure codes.
- [x] Reuse outgoing → document → target → job locks and all current finish revalidation. Preserve retry scheduling and legacy permissions; revoke PUBLIC on every new function and grant only exact capabilities.
- [x] Test digest/format/shape/lease rejection, rollback, owner/active/archive/base changes, private/no-fallback, restricted role denial, upgrades and unchanged TXT behavior on real PostgreSQL.

## Task 2: Bounded parser process

Files: app/pdf_parser.py, app/pdf_child.py, tests/test_pdf_parser.py, tests/pdf_fixtures.py, requirements.txt, requirements.lock.

- [x] Write generated PDF fixtures (pypdf low-level objects; no real data) and parser tests before implementation. Run tests to observe missing implementation failure.
- [x] Implement parsePdf(sourceBytes)->list[dict] and PdfProcessingError.code, verified child result validation, sanitized environment, process cleanup and enforced bounds. Child accepts bounded stdin and emits bounded JSON, with safe failure codes and suppressed parser diagnostics.
- [x] Success: native single-column pages, including text preservation. Reject corrupt/encrypted/scanned/mixed pages, unsupported layouts/actions, excessive bytes/pages/chars; enforce wall/resource failures. Test actual subprocess behavior, not only mocks.
- [x] Pin pypdf 6.21.1 and document BSD-3-Clause adoption. Do not import prototype code into production.

## Task 3: Upload and authorized page presentation

Files: app/version_routes.py, app/templates/versions.html, app/templates/document.html, app/repository.py, tests/test_pdf_routes.py.

- [x] Write request tests before implementation: suffix/header mismatch, PDF queued response, owner/tenant/CSRF limits, streamed size bound, page display escaping/unauthorized denial.
- [x] Extend existing multipart contract without new client identity fields; preserve TXT 1 MiB and allow PDF 2 MiB. Pass source_format to compatible service. Display processing failure guidance and PDF limits.
- [x] Read PDF page metadata only through existing authorized effective-version query, render escaped per-page text. Keep TXT rendering unchanged.

## Task 4: Worker integration and acceptance

Files: app/version_service.py, app/worker.py, tests/test_pdf_worker.py, docs/pdf-processing.md, README.md, docs/dependency-licenses.md, docs/rls-implementation-plan.md as needed.

- [x] Write worker tests for publish, parse failure preserving old version, stale lease, lost-worker recovery and shutdown during extraction. Observe RED before integration.
- [x] Route claims by immutable source_format, parse outside DB transaction, publish with digest/pages under existing lease. Stop before publication after signal; leave unfinished lease for recovery. Store safe allowlisted errors for bad inputs.
- [x] Run isolated parser tests, then serialized real PostgreSQL suite, offline CI tool/parser tests, fresh migration/upgrade tests and real process smoke. Retain RED/failure/fix records.
- [x] Independent review across parser containment and transactional integration; resolve actionable findings, rerun affected tests and complete regression.
- [x] Update docs with concrete limits/unsupported layouts, commands/results and remaining scope.
- [ ] Commit/push branch, create and attach PR against main, inspect CI.

## Execution record

- Baseline main 086f382 matches the previous verified 253 passed / 120 subtests tree; clean working directory. User authorization covers implementation and PR, not merge.
- Work in a dedicated feature branch in the existing clean checkout; no additional worktree or environment-secret copy needed.

- Initial RED: missing SQL PDF overload, parser module and source_format service; PDF upload returned 422. Implemented and verified targeted behaviors.
- Independent review found invisible/cropped/overlapping text acceptance and TXT header sniff on PDF page text. Added six RED regressions, fixed with conservative font metrics/crop/line checks and separate PDF text validation.
- Fresh test-db plus rebuilt runtime/test images: 366 passed; after final text-operation fixes mounted source full regression: 369 passed, 120 subtests; existing Starlette warning only. Parser 40 cases included in final full suite; readiness/CI tool tests 12 OK. Offline prototype requires its separate dependencies and will run in PR CI.

- Follow-up review confirmed all reported parser fixes by inspection; raw text objects reject merged shows and nonzero TJ adjustment. Three additional RED/green regressions retained.
