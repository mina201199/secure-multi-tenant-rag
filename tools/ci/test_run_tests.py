"""The artifact must never contain test output or environment values."""
import unittest
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from tools.ci.run_tests import main, pytest_counts


class SafeResultsTests(unittest.TestCase):
    def test_only_numeric_allowlisted_fields_leave_xml(self):
        xml = '<testsuites secret="password"><testsuite tests="2" failures="1" errors="0" skipped="0" time="1.5"><testcase name="secret"><failure>private document</failure><system-out>token</system-out></testcase></testsuite></testsuites>'
        self.assertEqual(pytest_counts(xml), dict(tests=2, failures=1, errors=0, skipped=0))

    def test_nested_suite_aggregates_are_not_double_counted(self):
        xml = '<testsuites><testsuite tests="2" failures="0" errors="0" skipped="1"><testsuite tests="2" failures="0" errors="0" skipped="1" /></testsuite></testsuites>'
        self.assertEqual(pytest_counts(xml)['tests'], 2)

    def test_invalid_numeric_attributes_are_rejected(self):
        with self.assertRaises(ValueError):
            pytest_counts('<testsuites><testsuite tests="secret" /></testsuites>')

    def run_report(self, xml):
        # Fault-inject the child/report boundary: even a zero child exit must
        # not certify missing, empty or inconsistent result evidence.
        def child(command, **kwargs):
            if xml is not None:
                Path(command[command.index('--junitxml') + 1]).write_text(xml)
            return type('Completed', (), {'returncode': 0})()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'result.json'
            with patch('sys.argv', ['runner', 'pytest', '--output', str(output), 'tests']), \
                    patch('tools.ci.run_tests.subprocess.run', side_effect=child), \
                    redirect_stdout(io.StringIO()):
                code = main()
            summary = json.loads(output.read_text())
            self.assertNotEqual(code, 0)
            self.assertEqual(summary['exit_code'], code)

    def test_success_requires_report(self):
        self.run_report(None)

    def test_success_requires_nonempty_test_evidence(self):
        self.run_report('<testsuites><testsuite tests="0" failures="0" errors="0" skipped="0" /></testsuites>')

    def test_success_rejects_failed_report_even_when_child_returns_zero(self):
        self.run_report('<testsuites><testsuite tests="2" failures="1" errors="0" skipped="0" /></testsuites>')
