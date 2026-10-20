# RLS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Use subagent-driven-development only when delegation is authorized. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepare a staged implementation of M2-11 / M2-12 without enabling RLS or changing application/database behavior in this preparation delivery.

**Architecture:** Separate authentication and business pools before removing authentication privileges from `rag_app`. Business connections open an explicit transaction and bind verified actor context with transaction-local settings; a distinct non-owner policy-reader role owns narrowly scoped boolean helpers to avoid recursive RLS. Introduce append-only migrations and prove the resulting boundaries using real PostgreSQL LOGIN connections.

**Tech Stack:** Python 3.12 container, FastAPI, psycopg 3 / psycopg_pool, PostgreSQL 16, pytest, Docker Compose test profile.

**Spec:** `docs/m2-rls-design.md` (all numbered sections), plus `README.md` and `docs/m2-versions.md`.

## Global Constraints

### 2026-10-20 version-write dependency update

The TXT write slice adds migration `004_m2_version_writes.sql`, protected
`version_sources` / `version_jobs` tables, and NOLOGIN `rag_version_writer` /
`rag_version_processor` roles. It does not enable RLS. The historical baseline
and seven-table policy checklist below predate that slice: subsequent RLS work
must append migrations after 004 and include **all nine tables** in the audit
and policy design. `tools/check_rls_readiness.py` now checks these resources.

Before enabling FORCE RLS, define narrowly scoped writer policies for the
SECURITY DEFINER owner and job/source policies for the internal processing
capability, while keeping web/processor direct table access revoked. Verify
create, claim, completion, failure, lease, rollback and concurrent sharing under
the final policies using real restricted connections. Existing explicit actor
parameters must also be reconciled with verified LOCAL context; arbitrary
runtime SQL remains within the trusted backend boundary until that work lands.
Durable jobs and a restricted TXT processing capability now exist, but no
running worker, worker login or recovery scheduler exists; R14 is still blocked.
Do not infer completed RLS or worker isolation from the TXT tests.

- “這是設計交付，沒有啟用 RLS、建立 DB 角色或執行資料庫驗收。” This preparation adds documentation and a catalog audit only. Every implementation checkbox below remains open.
- “RLS 與 API 授權並存；RLS 不能取代資料關聯約束與業務流程驗證。”
- “自訂 GUC（app.user_id 等）不是驗證憑證。” The backend and its parameterized SQL remain trusted; arbitrary runtime SQL can forge actor GUCs.
- “沒有任何一條規則依 admin 或 manager 自動開放內容、標題、分類或文件存在性。”
- “舊版即使 company、仍有舊 ACL、或呼叫者是其擁有者，也不得經一般查詢讀出。”
- “schema_migrations 僅 bootstrap 可存取。”
- Append new migration files after `003_m2_sharing.sql`; do not rewrite applied `001`, `002`, or `003`.
- No production migration, role creation, Docker mutation, or commit was authorized for this preparation. Future execution requires its own implementation/deployment scope.

## Observed baseline and blocking hazards

This subtask reviewed source on 2026-10-20 and did not open a PostgreSQL connection. The coordinating agent separately ran the optional read-only audit against the existing isolated `rag_test` database; its observations are recorded below, separately from behavioral acceptance.

| Source | Observation | Required gate |
| --- | --- | --- |
| `app/db.py:create_pool/get_db` | One pool; implicit connection-context transaction; no verified context binding or reset checks | Explicit business transaction and clean checkout/return |
| `app/main.py:optional_identity` | Session lookup/touch and repository queries share the cached `get_db` dependency | Separate auth pool and a single verified-identity dependency |
| `app/security.py:attempt_login` | Rate-limit commit intentionally survives failed login | Preserve commit on auth connection; never bind business context there |
| `app/main.py:login/logout`, `app/sharing.py` | Explicit commits can end LOCAL context | Put commits in the owning dependency/service; no business SQL after context expires |
| `001_m1_core.sql` | `rag_app` has whole-table `users` SELECT and sessions/limits DML | Revoke hash/session capability after auth split; column grants for business directory |
| `003_m2_sharing.sql` | Visibility UPDATE and reader INSERT/DELETE have no DB row authorization yet | Direct SQL negatives must fail before policy implementation, pass after |
| `app/bootstrap.py:ensure_runtime_role` | Sets basic no-bypass flags, does not impose NOINHERIT or remove memberships | Audit direct and indirect elevation paths; restrict membership |
| `tests/conftest.py` | Autouse fixtures call bootstrap and launch app | Never run this fixture suite for a read-only audit; future RLS suite needs explicit isolated test DB |
| `app/repository.py:replace_sharing` | Locks current version then re-reads in another READ COMMITTED statement | Preserve this protection; helper snapshot does not replace it |
| `Dockerfile` | Copies app/tests/evals, not `tools` | Run the audit locally with existing dependencies, or deliberately add audit packaging in a future change |

M2-02 must have its own accepted baseline before the RLS rollout. This plan does not infer acceptance from README test counts. Lifecycle, history, jobs, workers, chunks and embeddings are absent from the current schema; their acceptance is blocked by those missing capabilities.

## Preparation artifacts and safe audit

Delivered now:

- `docs/rls-implementation-plan.md`: file interfaces, sequencing, risks, acceptance/deployment gates.
- `tools/check_rls_readiness.py`: optional read-only catalog audit of required roles, RLS flags, table ownership and role memberships. It never bootstraps, creates missing roles, reads users/sessions/content, or changes DB objects.
- `tools/test_check_rls_readiness.py`: local tests of audit classification only; passing them is **not** a security test result.

Inspect the exact queries without installing psycopg or connecting:

```powershell
python tools/check_rls_readiness.py --sql
python -m unittest tools.test_check_rls_readiness
```

Only when a DB audit is separately intended, use an existing environment variable and existing connection credentials:

```powershell
python tools/check_rls_readiness.py --database-env DATABASE_URL
```

No DSN is accepted as a CLI argument or printed. Do not source a live `.env` merely to run this audit. The query set opens `BEGIN READ ONLY`, imposes a five-second statement timeout and rolls back. Existing-role catalogs are queried by name; missing roles become blockers rather than errors from `SET ROLE` or `has_*_privilege` calls. Exceptions emit a generic code without database detail or credentials.

Exit 0 is only query display/help success; exit 1 is a completed metadata audit reporting `NOT_READY`; exit 2 is configuration/dependency/connect/query failure, also `NOT_READY`. Even `metadata_status=NO_DETECTED_BLOCKERS` retains `security_acceptance=NOT_RUN`, `status=NOT_READY` and exit 1. The audit does not inspect effective grants, policies/helper semantics, login identity or transactions, so it cannot approve a deployment.

Preparation verification: the four local audit-classification tests first failed against an empty classifier, then passed after implementation. The coordinating agent independently reran those four tests and executed the read-only catalog query set against existing isolated `rag_test`: exit 1, `NOT_READY`, `metadata_status=BLOCKED`, `security_acceptance=NOT_RUN`; missing rag_auth/rag_policy_reader and ENABLE/FORCE absent on all seven target tables. `unsafe_role_flags:rag_app` reflected its current default INHERIT setting, a mismatch with the proposed NOINHERIT baseline; this flag alone is not evidence of an exploitable membership. The coordinator reports no bootstrap, DDL, DML or role change. Exact invocation/environment details belong in the coordinator's overall delivery record. No direct-SQL RLS behavior or pool enforcement was tested by that catalog audit.

## File ownership and interfaces for future implementation

| File | Responsibility / produced interface |
| --- | --- |
| `app/config.py` | Add `auth_database_url: str`, from required `AUTH_DATABASE_URL`; keep `DATABASE_URL` as business DSN; no production fallback between them |
| `app/db.py` | Keep `create_pool(dsn)`; add separate `get_auth_db(request)` and explicit transaction/context dependency described below; pool checkout/reset hooks |
| `app/security.py` | Keep `read_identity(auth_connection, raw_session, settings)` lookup/touch semantics; return internal verified identity, never password/token hashes to routes |
| `app/main.py` | Open/close `app.state.auth_pool` and `app.state.business_pool`; migrate auth routes and identity resolution first; then bind document routes |
| `app/sharing.py` | Accept the business dependency in route installation; remove ad hoc commits once the dependency owns success/rollback |
| `app/repository.py` | Existing query arguments remain `(connection, identity, ...)`; preserve manual tenant/ACL filters and current-version lock/recheck |
| `app/bootstrap.py` | Setup-only creation/reset of rag_app/rag_auth attributes; no runtime membership to policy role; migrations/seeding under owner only |
| `app/migrations/004_m2_rls_roles.sql` | Proposed next available number: helper role/schema/grants; final filename depends on lifecycle migrations merged first |
| `app/migrations/005_m2_rls_policies.sql` | Proposed subsequent number: helpers, final revoke/grants, policies, ENABLE/FORCE, all in bootstrap transaction |
| `compose.yaml`, `.env.example` | Separate auth password for local and test; auth DSN to web/tests; admin DSN stays setup/tests only |
| `tests/conftest.py` | Existing runtime_db remains real rag_app LOGIN; add `auth_db`, dedicated fixtures and size-one business pool fixture |
| `tests/rls_contract/test_catalog.py` | Owners/membership/ACL/policy metadata and genuine LOGIN assertions |
| `tests/rls_contract/test_reads.py` | Raw SQL actor/tenant/current-version/ready/ACL matrix |
| `tests/rls_contract/test_writes.py` | Raw SQL visibility/reader positive/negative matrix, column and FK integrity |
| `tests/rls_contract/test_context.py` | Reuse/rollback/errors/timeouts/session pollution, invalid GUC parsing |
| `tests/rls_contract/test_auth_boundary.py` | Auth capability separation and M1 session regression |
| `tests/rls_contract/test_concurrency.py` | Two-connection revocation and existing switch-vs-sharing regression |
| `docs/rls-validation.md` | Commands, versions, baseline failures, actual results, uncovered scope and approved rollout record |

Numbers `004` / `005` are reservations within this plan, not files delivered now. Rebase and choose the next unused numbers before implementation. The current bootstrap applies all pending migrations and seed in one outer transaction; retain that atomicity and the advisory transaction lock.

### Planned dependency contract

Define `VerifiedIdentity` as an internal mapping with `id`, `tenant_id`, `username`, `display_name`, `role`, `tenant_name`; authentication may retain `token_hash` only internally for touch, never as a business authorization input.

`get_auth_db(request) -> Iterator[Connection]` borrows only `auth_pool`. `optional_identity` depends on it and resolves Session expiry, idle timeout and active-account status before any business connection is borrowed. `require_identity` preserves the current 401 behavior.

`get_business_context(request, identity=Depends(require_identity)) -> Iterator[BusinessContext]` yields one immutable pair `{connection, identity}`. Optional page routes first resolve optional identity, return the login redirect when absent, then call the same scoped business context manager; do not create a second identity read or borrow an unauthenticated business connection. `BusinessContext` is a frozen dataclass with `connection: psycopg.Connection` and `identity: VerifiedIdentity`. Routes pass its members to existing repositories; they never accept IDs for binding from payload/header/query/path.

The owning context manager must establish a top-level transaction **before** any query, use two parameterized `set_config(..., true)` calls, and validate cleanup on return:

```python
with business_pool.connection() as conn:
    # checkout must already have validated idle state and no app.* session values
    with conn.transaction():
        conn.execute("SELECT set_config('app.user_id', %s, true)", (str(identity['id']),))
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(identity['tenant_id']),))
        yield BusinessContext(conn, identity)
```

This is a target interface, not copy-ready implementation: first add the dataclass and pool hooks in Task 2. Never yield a connection outside the context or call commit/rollback within repository business work. A failed session touch must propagate rather than creating a business context. The auth touch commits on its own connection before business begins. Use READ COMMITTED, short transactions; reauthenticate/rebind on any later business transaction. Rate-limit durability remains intentional and isolated.

## Task 1: Capture a failing security baseline in an isolated test DB

**Files:** create `tests/rls_contract/test_reads.py`, `test_catalog.py`; modify `tests/conftest.py` only for isolated fixtures.

**Interfaces:** consume existing `runtime_db` (dict rows) and `admin_db` (tuple rows); produce direct-SQL negative tests independent of API filters. Admin credentials create fixtures but are never the tested actor.

- [ ] Confirm the existing test baseline on an isolated test profile and save its exact result before changes.
- [ ] Add `assert runtime_db.execute('SELECT id FROM app_core.documents').fetchall() == []` with no context. It must fail on the current seeded schema; record that expected failure, do not xfail/skip it into a green security report.
- [ ] Assert `SELECT session_user, current_user` both equal `rag_app`; check safe flags and absence of direct memberships before counting any result as an RLS test.
- [ ] Build fixture actor lookup using `SELECT id, tenant_id FROM app_core.users WHERE username = %s` on admin_db, reading tuples `[0]`/`[1]`; bind only those values on runtime_db. Never mix dict and tuple conventions from the design examples.
- [ ] Add seed read expectations: a.owner=3, a.reader=2, a.staff=1, a.admin=1, a.manager=1, a.inactive=0 ready-current documents; all tenant IDs equal actor tenant. Add another-company actor and guessed IDs.
- [ ] Run the suite and retain the failing output. Existing API tests passing alongside failing direct SQL is the intended baseline finding.

Run later: `docker compose --profile test run --build --rm tests pytest -q tests/rls_contract/test_reads.py tests/rls_contract/test_catalog.py`.

Gate: failures are confirmed to arise from missing row enforcement, not fixture/login mistakes. These tests are deliberately red until Task 4; they cannot block an intermediate auth-only review by being mislabeled as passing acceptance.

## Task 2: Split auth capability and make business transactions explicit

**Files:** modify config/db/security/main/sharing/bootstrap/Compose/example environment; create `tests/rls_contract/test_auth_boundary.py`, `test_context.py`.

**Interfaces:** consumes Task 1 genuine LOGIN baseline; produces required `AUTH_DATABASE_URL`, `auth_pool`, `business_pool`, `BusinessContext` and `get_business_context` contracts above. Auth and business passwords are distinct; fail startup on missing auth DSN. Preserve current public identity output and uniform unauthorized/not-found responses.

- [ ] Write a test that auth_db can resolve/touch sessions and rag_app cannot query sessions, limits, or users.password_hash once final grants land. Add the inverse: rag_auth cannot SELECT documents/versions/readers.
- [ ] Write a pool-size-one test that binds A in a top-level transaction, finishes it, and proves both app.* settings are absent/empty before B is borrowed. Add commit, rollback, exception, cancellation/timeout and aborted transaction variants.
- [ ] Implement distinct pools and identity resolution first, with existing broad grants temporarily retained **only on the isolated test branch** until Task 4 finalizes the coordinated migration. Startup must close the first opened pool if the second fails.
- [ ] Route login/limit/logout through get_auth_db. Route document/sharing work through BusinessContext. Remove route-local sharing commits; preserve success response only after transaction commit succeeds, and rollback on dependency failure.
- [ ] Implement pool checkout/reset checks that run only when idle and finish their own inspection transaction. Remove any session-level app.* contamination, record only a fixed event code, and reject/discard the contaminated connection before business use. When reset cannot establish clean idle state, discard it. A normal `SELECT 1` health check does not prove this gate.
- [ ] Test session SET pollution deliberately: LOCAL may mask a poisoned session value and reveal it after commit. Verify cleanup/rejection, no residual values, and successful subsequent B context on a clean connection.
- [ ] Run all M1 auth/CSRF/rate-limit/session regression tests and existing document/sharing tests. Verify rate-limit failures still persist and expired/logout/inactive sessions never create business context.

Gate: identity comes only from valid Session lookup, auth commit never touches business transaction, no repository receives auth_pool connection, and no post-commit query runs on an expired context. Do not deploy the intermediate branch; old app + revoked auth grants or new app + absent auth role is an outage.

## Task 3: Review role, privilege and helper migration as one capability boundary

**Files:** proposed `004_m2_rls_roles.sql`, `005_m2_rls_policies.sql`; modify bootstrap; create catalog/helper negative tests.

**Interfaces:** produces app_guard helpers below, explicit policy-reader policies, least privilege grants; consumes trusted context and current 001→003 schema. `rag_worker` and `rag_share_writer` remain absent until their capability exists.

| Role | Login / attributes | Minimum object access |
| --- | --- | --- |
| rag_app | LOGIN, NOINHERIT, NOSUPERUSER, NOBYPASSRLS, NOCREATEDB, NOCREATEROLE, NOREPLICATION | Business SELECT, users safe-column SELECT, versions UPDATE(visibility), readers INSERT/DELETE; no sessions/limits, sequence, TRUNCATE, DDL or memberships |
| rag_auth | LOGIN, same safe flags | tenants(id,name); users(id,tenant_id,username,display_name,role,password_hash,is_active); sessions required SELECT/INSERT/UPDATE/DELETE; login_limits required DML |
| rag_policy_reader | NOLOGIN, same safe flags | SELECT users/documents/versions/readers through explicit role-specific internal true policies; no writes/memberships or table ownership |
| setup owner | Existing owner DSN, setup/tests only | Migrations, seed, role/function ownership changes; never handed to web |

Business users safe columns: id, tenant_id, username, display_name, role, is_active. Do not grant manager_id unless an implemented feature needs it. Column grants still apply under RLS. Revoke existing table-level users SELECT before granting columns; adding columns alone does not remove inherited whole-table capability. Explicitly revoke app sessions/limits privileges. Audit effective schema CREATE, database CREATE/TEMP as required by threat review, function EXECUTE and table/column privileges including PUBLIC and memberships. Runtime TEMP does not grant helper replacement: fixed helper search_path puts pg_temp last and uses qualified objects.

- [ ] Write catalog tests for safe role flags, login mode, memberships, role/schema/table/function ownership and effective privileges. Test SELECT password_hash and unauthorized columns fail while directory queries work.
- [ ] Add NOLOGIN rag_policy_reader, app_guard schema and required schema USAGE grants under setup owner. No runtime CREATE on app_core/app_guard/public or function ownership; revoke PUBLIC EXECUTE for app_guard functions in the same migration transaction as creation.
- [ ] Define safe `app_guard.actor_id() RETURNS bigint` and `tenant_id() RETURNS bigint` parsers: missing/empty/whitespace/noninteger/nonpositive/overflow => NULL, no fallback. Validate range before casting, do not rely on boolean short-circuit.
- [ ] Define `actor_ok() RETURNS boolean`; `can_read_document(bigint,bigint)`, `can_read_version(bigint,bigint)`, `can_manage_sharing(bigint,bigint)`, `can_see_reader(bigint,bigint,bigint)`, `can_grant_reader(bigint,bigint,bigint)`, `can_list_user(bigint,bigint)` all return boolean.
- [ ] Each boolean helper derives actor from GUCs, checks active user/tenant before resource lookup, and returns false for mismatched resource tenant. Resource IDs are lookup inputs, never caller authority. actor_id/tenant_id parsers need no table reads; boolean helper owner is policy-reader, non-owner of all protected tables.
- [ ] Use SECURITY DEFINER, STABLE, fixed `SET search_path = pg_catalog, pg_temp`, full app_core qualification, no dynamic SQL/row returns/LEAKPROOF. Revoke PUBLIC execution and grant only needed rag_app helper execution. rag_auth receives no document helper execution.
- [ ] Give policy-reader SELECT true policies on users/documents/versions/readers only. FORCE RLS stays enabled; helper internals never traverse business-role policy expressions. Test no recursion and no caller-replaceable temp/search_path objects.

Gate: functions cannot be replaced, called through PUBLIC/auth, or used as arbitrary row extraction. Inspect pg_proc owner/config/security mode and effective ACL. Testing `row_security=off` must produce refusal for restricted queries, never a bypass. Do not rely solely on catalog textual comparison for helper semantics.

## Task 4: Enable final policies in isolated DB and prove direct SQL enforcement

**Files:** finish proposed policy migration; create writes/reads/context tests and extend existing database tests.

**Interfaces:** consume helpers and pools; produce completed R01–R10, R12–R13 evidence from `docs/m2-rls-design.md`. R11 is accepted only for already implemented sharing/switch cases.

- [ ] Implement the policy map from design §6: document/current-version SELECT; visibility UPDATE USING/WITH CHECK can_manage_sharing; readers SELECT can_see_reader, INSERT can_grant_reader, DELETE can_manage_sharing; users directory and tenant self SELECT; auth-only true policies on required auth tables. All roles/commands explicit, no PUBLIC true policy.
- [ ] ENABLE and FORCE RLS on tenants/users/sessions/login_limits/documents/document_versions/version_readers. No schema_migrations grant/policy; retain all composite FKs. No document/versions INSERT/DELETE or broad users UPDATE grant.
- [ ] Test positive owner sharing, private/company/specified, target same-tenant active non-owner, revoke inactive target, and atomic rollback of mixed-invalid batches. DELETE policy must not require the removed reader to remain active.
- [ ] Test direct non-owner/admin/manager/cross-tenant writes, forbidden content/status/owner/tenant/current pointer changes and ACL UPDATE. Assert rowcount zero or InsufficientPrivilege as appropriate and verify unchanged data via admin fixture. Do not accept “SQL did not throw” as denial evidence.
- [ ] Test ready/current dual gate, NULL pointer, queued/failed new version with old current retained, switched current with no fallback, archived document, old owner/company/ACL version. Use dedicated fixture rows, not permanent changes to shared seed.
- [ ] Test every invalid context variant R02 independently in fresh transactions; controlled error requires rollback before reuse. Test direct helper cross-tenant parameters, temp objects, and the absence of ordinary owner-bypassing views. Any future views must be PG16 security_invoker.
- [ ] Use two real connections and events/barriers: revoke/disable on B, commit B, next READ COMMITTED statement on A must deny. State clearly that completed/ongoing statement snapshots and already delivered data cannot be recalled.
- [ ] Run max_size=1 pool tests, full existing M1/M2 suite, all direct SQL tests. Repeat only when failures or changed code justify it. A no-context query must deny even after prior identity transaction success.

Gate: R01–R10 and R12–R13 each have executed positive/negative evidence; no skip/xfail/metadata-only result counts as enforcement. Bind verified context in old direct repository tests that intentionally exercise authorized business behavior; keep new no-context negatives separate.

## Task 5: Upgrade, least-privilege rollout and rollback rehearsal

**Files:** tests/test_versions.py upgrade fixtures, bootstrap tests, docs/rls-validation.md, README only after acceptance.

**Interfaces:** final app/config/secrets and append-only migrations are one coordinated deployment unit; consumes all prior gates.

- [ ] Test clean install and upgrade from seeded 001→002→003 with unchanged IDs/content/ACL, all policies/grants present, repeat bootstrap safe. Test injected migration failure in an isolated DB rolls back helper/role/grant/RLS/schema_migrations changes without partial state.
- [ ] Preserve owner-only setup access, bootstrap advisory lock and explicit seed flow. Check no owner DSN in web/worker environment or logs; use fixed error codes rather than dumping secrets or SQL values.
- [ ] Rehearse in a disposable database: stop/drain old web requests; back up by the approved deployment process; provision auth secrets in setup; apply migration transaction; start matching new app; run auth + context-bound read/share smoke and direct SQL negatives. Do not connect old code during grants transition.
- [ ] Prefer rollback before migration commit. After commit, stop new traffic and restore a verified compatible app/schema/privilege state from the rehearsal procedure; do not “fix” an outage by disabling RLS, adding owner membership, or broadly granting SELECT.
- [ ] Record PostgreSQL/driver/image versions, migration list, role identity checks, exact commands, actual failures/fixes/retests and uncovered lifecycle cases. Update README M2-11/M2-12 only for the evidenced scope; full background-worker acceptance stays incomplete.

Gate: manual deployment approval follows complete reviewable artifacts and test evidence. This preparation does not supply that approval or a production deployment command.

## Deferred integration contracts and acceptance dependencies

| Future capability | Required integration before its acceptance | Status here |
| --- | --- | --- |
| M2-03/04 create/process/publish | New versions private/queued/no inherited ACL; immutable content; restricted creation/publish capability; same outgoing-version lock then document/other-version order; post-lock READ COMMITTED pointer/status/owner recheck | Not implemented/tested |
| M2-05 archive | Restricted status change blocks read/download/retrieval/publish/retry; no worker resurrection; lock with sharing/publish | Not implemented/tested |
| M2-06 history | Independent version history ACL + endpoint + audit; ordinary versions_read never expands through company/admin/owner flag | Not implemented/tested |
| M2-07–09 transfer | Minimal approval fields; reject self-receipt/cross-tenant/inactive/repeat approval; explicit version scope; preserve reader ACL; common locks | Not implemented/tested |
| M2-10 account disable | Restricted columns/capability, no role/tenant/manager/hash edits; direct actor validity recheck; define stronger target-active-at-commit locking only if required | Not implemented/tested |
| M3A jobs/worker | Persistent DB claim/lease/generation; dedicated rag_worker; no actor identity from payload; worker sees only claimed queued version, not all current versions; pre-external-call and writeback checks | R14 blocked by schema/worker absence |
| M3A/M3C chunks/embeddings | Tenant/version composite FKs, processing completeness, parent can_read_version; authorized Top-K before reranker/model; fresh pre-model and pre-delivery checks | R15 blocked by schema/retrieval absence |

For every lifecycle writer, lock outgoing current version first, then document/other rows in a documented consistent ID order. Existing sharing lock/recheck test is retained, but future archive/publish/transfer writers must each add a two-connection race test. Direct row policies alone cannot guarantee whole ACL replacement atomicity or mandatory locking; retain the API transaction. If arbitrary DML must obey full sharing workflow, separately introduce rag_share_writer + one constrained definer operation and revoke direct rag_app writes; do not pretend current direct policies enforce that stronger guarantee.

## Recommended first implementation slice

Execute Task 1 plus Task 2 on an isolated feature branch: preserve a real failing direct-SQL baseline, split authentication from business connections, and prove trusted LOCAL context and pool cleanup without deploying policy changes. This is the smallest independently reviewable slice that removes the largest integration obstacle. Its acceptance is auth regression + context isolation; RLS remains explicitly incomplete/red until Tasks 3–5.

The first **RLS-enforcing** slice is the coordinated Tasks 2–4 application/role/helper/policy unit covering all seven current target tables. Do not enable only documents while leaving versions readable, or remove auth privileges before the matching app is ready. Worker/history/transfer acceptance belongs to later capability-specific slices.

## Spec coverage review

Design §1–3 → baseline and Tasks 1/3; §4 → Task 2/4; §5–6 → Task 3/4; §7 → Tasks 2/4 and lifecycle lock dependency; §8 → deferred lifecycle table; §9 → R14/R15 dependencies; §10–11 → Tasks 1–5 with real execution evidence; §12 → documented threat/snapshot/definer limitations. No production RLS, background-worker isolation, or complete M2 delivery is claimed by this plan.
