"""Read-only catalog audit. No bootstrap, SET ROLE, DDL, DML or application reads.

Run with --sql to inspect queries, or --database-env DATABASE_URL to connect.
Exit 1 always means NOT_READY; exit 2 means configuration/connection failure.
Passing catalog checks cannot establish policy semantics or pool isolation.
"""
import argparse
import json
import os


ROLE_LOGINS = {"rag_app": True, "rag_auth": True, "rag_policy_reader": False,
               "rag_version_writer": False, "rag_version_processor": False, "rag_worker": True}
TABLES = ("tenants", "users", "sessions", "login_limits", "documents",
          "document_versions", "version_readers", "version_sources", "version_jobs")
QUERIES = (
    """SELECT rolname, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb,
              rolreplication, rolinherit, rolcanlogin
       FROM pg_catalog.pg_roles
       WHERE rolname IN ('rag_app', 'rag_auth', 'rag_policy_reader',
                        'rag_version_writer', 'rag_version_processor', 'rag_worker')""",
    """SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
              owner_role.rolname IN ('rag_app', 'rag_auth', 'rag_policy_reader',
                                    'rag_version_writer', 'rag_version_processor', 'rag_worker') AS runtime_owner
       FROM pg_catalog.pg_class c
       JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
       JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = c.relowner
       WHERE n.nspname = 'app_core' AND c.relkind = 'r'
         AND c.relname IN ('tenants', 'users', 'sessions', 'login_limits',
                          'documents', 'document_versions', 'version_readers',
                          'version_sources', 'version_jobs')""",
    # Reject all direct memberships for these minimal roles. Any indirect path
    # must start with a direct edge, regardless of PG16 INHERIT/SET options.
    """SELECT member_role.rolname AS member_name, granted_role.rolname AS role_name
       FROM pg_catalog.pg_auth_members m
       JOIN pg_catalog.pg_roles member_role ON member_role.oid = m.member
       JOIN pg_catalog.pg_roles granted_role ON granted_role.oid = m.roleid
       WHERE member_role.rolname IN ('rag_app', 'rag_auth', 'rag_policy_reader',
                                    'rag_version_writer', 'rag_version_processor', 'rag_worker')""",
)


def evaluate(roles, tables, memberships):
    blockers = []
    by_name = {row["rolname"]: row for row in roles}
    for name, login in ROLE_LOGINS.items():
        role = by_name.get(name)
        if role is None:
            blockers.append(f"missing_role:{name}")
            continue
        if any(role[flag] for flag in ("rolsuper", "rolbypassrls", "rolcreaterole",
                                       "rolcreatedb", "rolreplication", "rolinherit")):
            blockers.append(f"unsafe_role_flags:{name}")
        if role["rolcanlogin"] != login:
            blockers.append(f"wrong_login_capability:{name}")
    by_table = {row["relname"]: row for row in tables}
    for name in TABLES:
        table = by_table.get(name)
        if table is None:
            blockers.append(f"missing_table:{name}")
            continue
        if not (table["relrowsecurity"] and table["relforcerowsecurity"]):
            blockers.append(f"rls_not_forced:{name}")
        if table["runtime_owner"]:
            blockers.append(f"runtime_owner:{name}")
    if memberships:
        blockers.append("runtime_role_membership")
    return {
        "status": "NOT_READY",
        "metadata_status": "BLOCKED" if blockers else "NO_DETECTED_BLOCKERS",
        "security_acceptance": "NOT_RUN",
        "blockers": blockers,
        "unverified": ["actual_login_identity", "effective_table_column_schema_grants",
                       "function_ownership_acl_search_path", "policy_roles_and_semantics",
                       "direct_sql_positive_negative_matrix", "auth_business_pool_isolation",
                       "fresh_and_upgrade_migrations", "concurrency_and_lifecycle"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--sql", action="store_true", help="print the read-only query set")
    modes.add_argument("--database-env", metavar="ENV_NAME",
                       help="environment variable holding an existing DSN; never printed")
    args = parser.parse_args()
    if args.sql:
        print("BEGIN READ ONLY;\nSET LOCAL statement_timeout = '5s';")
        print(";\n".join(QUERIES) + ";\nROLLBACK;")
        return 0
    dsn = os.environ.get(args.database_env)
    if not dsn:
        print(json.dumps({"status": "NOT_READY", "audit_error": "missing_dsn_environment"}))
        return 2
    try:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(dsn, connect_timeout=5, row_factory=dict_row,
                            autocommit=True) as conn:
            # This is the first statement; no implicit transaction precedes it.
            conn.execute("BEGIN READ ONLY")
            conn.execute("SET LOCAL statement_timeout = '5s'")
            snapshots = [conn.execute(query).fetchall() for query in QUERIES]
            conn.rollback()
        print(json.dumps(evaluate(*snapshots), sort_keys=True))
        return 1
    except Exception:
        # psycopg exceptions can include DSN, server addresses or parameter detail.
        print(json.dumps({"status": "NOT_READY", "audit_error": "catalog_audit_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
