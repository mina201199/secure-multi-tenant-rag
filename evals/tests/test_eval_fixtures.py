"""Offline fixture integrity only: no app imports, database, network, or model calls."""
import json
from collections import Counter
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class EvaluationFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = json.loads((ROOT / "core_questions.json").read_text(encoding="utf-8"))
        cls.corpus = json.loads((ROOT / cls.dataset["corpus"]).read_text(encoding="utf-8"))
        cls.accounts = {a["id"]: a for a in cls.corpus["accounts"]}
        cls.versions = {v["id"]: v for v in cls.corpus["versions"]}
        cls.questions = cls.dataset["questions"]

    def eligible(self, account, version):
        return (
            account["active"]
            and account["company"] == version["company"]
            and version["current"]
            and version["document_status"] == "ready"
            and version["status"] == "ready"
            and (
                account["id"] == version["owner"]
                or version["visibility"] == "company"
                or account["id"] in version["readers"]
            )
        )

    def test_version_and_fictional_provenance(self):
        self.assertEqual(self.dataset["schema_version"], "1.0")
        self.assertEqual(self.dataset["dataset_version"], self.corpus["dataset_version"])
        self.assertEqual(self.corpus["kind"], "proposed_fictional_evaluation_fixture_not_database_seed")
        self.assertFalse(self.dataset["review"]["independent_human_signoff"])

    def test_thirty_unique_individually_authored_questions(self):
        self.assertEqual(len(self.questions), 30)
        self.assertEqual({q["id"] for q in self.questions}, {f"C{i:02}" for i in range(1, 31)})
        self.assertEqual(len({q["question"] for q in self.questions}), 30)
        self.assertEqual(Counter(q["split"] for q in self.questions), {"dev": 18, "acceptance": 12})

    def test_case_schema_and_context(self):
        required = {"id", "split", "category", "account", "company", "relation", "authorization", "version_status", "question", "expected", "allowed_evidence"}
        for q in self.questions:
            with self.subTest(case=q["id"]):
                self.assertEqual(set(q), required)
                self.assertEqual(q["company"], self.accounts[q["account"]]["company"])
                for field in ("relation", "authorization", "question", "category"):
                    self.assertIsInstance(q[field], str)
                    self.assertTrue(q[field].strip())
                self.assertTrue(q["version_status"])
                self.assertEqual(set(q["expected"]), {"decision", "answer", "required_facts", "forbidden_facts", "reason"})
                self.assertIn(q["expected"]["decision"], {"answer", "refuse"})
                self.assertTrue(q["expected"]["reason"])

    def test_accounts_and_tenant_bound_relationships(self):
        self.assertEqual(len(self.accounts), len(self.corpus["accounts"]))
        for a in self.accounts.values():
            self.assertIn(a["company"], self.corpus["companies"])
            self.assertIn(a["role"], {"employee", "admin"})
            if a["manager"]:
                self.assertEqual(a["company"], self.accounts[a["manager"]]["company"])
        manager = self.accounts["fx-a.owner"]["manager"]
        self.assertEqual(manager, "fx-a.manager")
        self.assertFalse(self.accounts["fx-a.inactive"]["active"])

    def test_corpus_versions_and_acl(self):
        self.assertEqual(len(self.versions), len(self.corpus["versions"]))
        current_counts = Counter()
        document_companies = {}
        numbers = set()
        for v in self.versions.values():
            self.assertEqual(v["company"], self.accounts[v["owner"]]["company"])
            self.assertEqual(document_companies.setdefault(v["document"], v["company"]), v["company"])
            self.assertIn(v["visibility"], {"company", "private"})
            self.assertIn(v["status"], {"queued", "processing", "ready", "failed", "archived"})
            self.assertIn(v["document_status"], {"ready", "archived"})
            self.assertNotIn((v["document"], v["number"]), numbers)
            numbers.add((v["document"], v["number"]))
            self.assertTrue(v["lines"])
            self.assertTrue(all(isinstance(line, str) and line for line in v["lines"]))
            for reader in v["readers"]:
                self.assertEqual(v["company"], self.accounts[reader]["company"])
            current_counts[v["document"]] += int(v["current"])
        self.assertTrue(all(n == 1 for n in current_counts.values()))

    def test_declared_version_status_matches_corpus(self):
        for q in self.questions:
            for version_id, label in q["version_status"].items():
                with self.subTest(case=q["id"], version=version_id):
                    v = self.versions[version_id]
                    prefix = "current" if v["current"] else "historical"
                    self.assertTrue(label.startswith(f"{prefix}_{v['status']}"))
                    if "foreign" in label:
                        self.assertNotEqual(v["company"], q["company"])
                    if "no_acl" in label:
                        a = self.accounts[q["account"]]
                        self.assertEqual(v["visibility"], "private")
                        self.assertNotEqual(a["id"], v["owner"])
                        self.assertNotIn(a["id"], v["readers"])
                    if label.endswith("_reader"):
                        self.assertIn(q["account"], v["readers"])

    def test_allowed_evidence_is_real_authorized_current_and_ready(self):
        for q in self.questions:
            for citation in q["allowed_evidence"]:
                with self.subTest(case=q["id"], citation=citation):
                    version_id, locator = citation.split(":")
                    v = self.versions[version_id]
                    self.assertIn(version_id, q["version_status"])
                    self.assertTrue(locator.startswith("L"))
                    self.assertTrue(1 <= int(locator[1:]) <= len(v["lines"]))
                    self.assertTrue(self.eligible(self.accounts[q["account"]], v))
            self.assertEqual(len(q["allowed_evidence"]), len(set(q["allowed_evidence"])))

    def test_reference_answers_supported_and_refusals_have_no_citations(self):
        for q in self.questions:
            with self.subTest(case=q["id"]):
                e = q["expected"]
                self.assertTrue(e["answer"])
                if e["decision"] == "answer":
                    self.assertTrue(q["allowed_evidence"])
                    self.assertTrue(e["required_facts"])
                    lines = []
                    for ref in q["allowed_evidence"]:
                        vid, loc = ref.split(":")
                        lines.append(self.versions[vid]["lines"][int(loc[1:]) - 1])
                    for fact in e["required_facts"]:
                        self.assertIn(fact, "\n".join(lines))
                        self.assertIn(fact, e["answer"])
                else:
                    self.assertEqual(q["allowed_evidence"], [])
                    self.assertEqual(e["required_facts"], [])
                for forbidden in e["forbidden_facts"]:
                    self.assertNotIn(forbidden, e["answer"])

    def test_split_coverage(self):
        shared = {"cross_company", "answerable", "no_answer", "parts_distinction", "same_company_unauthorized", "role_no_bypass", "version_conflict", "status_exclusion", "prompt_injection"}
        categories = {q["category"] for q in self.questions}
        self.assertTrue(shared <= categories)
        for split in ("dev", "acceptance"):
            cases = [q for q in self.questions if q["split"] == split]
            self.assertEqual({q["expected"]["decision"] for q in cases}, {"answer", "refuse"})
            self.assertTrue((shared - {"answerable"}) <= {q["category"] for q in cases})
        self.assertIn("version_acl_reset", categories)
        self.assertIn("inactive_account", categories)

    def test_injection_fixture_is_data_and_not_an_evidence_allowance(self):
        attack = self.versions["a-safety-v1"]["lines"][1]
        self.assertIn("忽略平台規則", attack)
        for q in self.questions:
            self.assertNotIn("a-safety-v1:L2", q["allowed_evidence"])
        # Account/role eligibility must not be inferred from text inside that source.
        self.assertFalse(self.eligible(self.accounts["fx-a.admin"], self.versions["a-meeting-v1"]))
        self.assertFalse(self.eligible(self.accounts["fx-a.manager"], self.versions["a-meeting-v1"]))
        self.assertFalse(self.eligible(self.accounts["fx-a.reader"], self.versions["a-maint-v2"]))


if __name__ == "__main__":
    unittest.main()
