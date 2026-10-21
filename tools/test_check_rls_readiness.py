"""Local audit-tool tests only: these do not verify PostgreSQL RLS."""
import unittest

from tools.check_rls_readiness import ROLE_LOGINS, TABLES, evaluate


class ReadinessReportTests(unittest.TestCase):
    def test_version_write_resources_are_audited(self):
        self.assertIn('version_sources', TABLES)
        self.assertIn('version_jobs', TABLES)
        self.assertIs(ROLE_LOGINS.get('rag_version_writer'), False)
        self.assertIs(ROLE_LOGINS.get('rag_version_processor'), False)

    def test_missing_catalog_objects_cannot_report_ready(self):
        report = evaluate([], [], [])
        self.assertEqual(report["status"], "NOT_READY")
        self.assertIn("missing_role:rag_auth", report["blockers"])
        self.assertIn("missing_table:documents", report["blockers"])

    def test_disabled_rls_and_owner_runtime_are_blockers(self):
        report = evaluate(
            [{"rolname": "rag_app", "rolsuper": False, "rolbypassrls": False,
              "rolcreaterole": False, "rolcreatedb": False, "rolreplication": False,
              "rolinherit": False, "rolcanlogin": True}],
            [{"relname": "documents", "relrowsecurity": False,
              "relforcerowsecurity": False, "runtime_owner": True}], [],
        )
        self.assertIn("rls_not_forced:documents", report["blockers"])
        self.assertIn("runtime_owner:documents", report["blockers"])

    def test_membership_is_not_hidden_by_safe_role_flags(self):
        report = evaluate([], [], [{"member_name": "rag_app", "role_name": "other"}])
        self.assertIn("runtime_role_membership", report["blockers"])

    def test_even_complete_metadata_does_not_certify_security(self):
        roles = [dict(rolname=name, rolsuper=False, rolbypassrls=False,
                      rolcreaterole=False, rolcreatedb=False, rolreplication=False,
                      rolinherit=False, rolcanlogin=login)
                 for name, login in [("rag_app", True), ("rag_auth", True),
                                     ("rag_policy_reader", False), ("rag_version_writer", False),
                                     ("rag_version_processor", False)]]
        tables = [dict(relname=name, relrowsecurity=True,
                       relforcerowsecurity=True, runtime_owner=False)
                  for name in ["tenants", "users", "sessions", "login_limits",
                               "documents", "document_versions", "version_readers",
                               "version_sources", "version_jobs"]]
        report = evaluate(roles, tables, [])
        self.assertEqual(report["status"], "NOT_READY")
        self.assertEqual(report["metadata_status"], "NO_DETECTED_BLOCKERS")
        self.assertEqual(report["security_acceptance"], "NOT_RUN")


if __name__ == "__main__":
    unittest.main()
