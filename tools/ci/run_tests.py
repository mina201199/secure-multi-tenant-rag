"""Run trusted fictional tests; emit counts only, never captured test bodies.

Raw pytest XML lives in a private temporary directory and is deleted on exit.
All child stdout/stderr is discarded, including on failure. Reproduce failures
locally to inspect details; never upload raw logs or JUnit from these tests.
"""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET


def pytest_counts(xml):
    root = ET.fromstring(xml)
    suites = [root] if root.tag == 'testsuite' else list(root.findall('testsuite'))
    counts = {key: 0 for key in ('tests', 'failures', 'errors', 'skipped')}
    if not suites:
        raise ValueError('missing suites')
    for suite in suites:
        for key in counts:
            value = int(suite.get(key, '0'))
            if value < 0:
                raise ValueError('negative count')
            counts[key] += value
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('pytest', 'unittest'))
    parser.add_argument('--output', required=True)
    parser.add_argument('paths', nargs='+')
    args = parser.parse_args()
    counts = {}
    code = 2
    try:
        if args.mode == 'pytest':
            with tempfile.TemporaryDirectory(prefix='ci-private-') as directory:
                report = Path(directory) / 'raw.xml'
                code = subprocess.run(
                    [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                     '--tb=no', '--junitxml', str(report), *args.paths],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    check=False,
                ).returncode
                if report.exists():
                    counts = pytest_counts(report.read_text(encoding='utf-8'))
                if code == 0:
                    if counts.get('tests', 0) <= 0:
                        code = 2
                    elif counts['failures'] or counts['errors']:
                        code = 1
        else:
            # Executing this file by path puts tools/ci, not the repository,
            # on sys.path. Offline tests import their repository packages.
            sys.path.insert(0, str(Path.cwd()))
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                suite = unittest.TestSuite()
                for path in args.paths:
                    suite.addTests(unittest.defaultTestLoader.discover(path))
                result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
            counts = dict(tests=result.testsRun, failures=len(result.failures),
                          errors=len(result.errors), skipped=len(result.skipped))
            code = 0 if result.wasSuccessful() and result.testsRun else 1
    except Exception:
        # Exception text may contain fixture data or a connection string.
        code = 2
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    unit = ('junit_reported_cases_including_subtests' if args.mode == 'pytest'
            else 'unittest_cases')
    summary = dict(schema_version=1, runner=args.mode, count_unit=unit,
                   exit_code=code, **counts)
    output.write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
