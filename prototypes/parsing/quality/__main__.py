"""Run from repository root: python -m prototypes.parsing.quality."""
import json

from . import check_case, load_manifest


def main():
    reports = [check_case(case) for case in load_manifest()]
    print(json.dumps({'fictional_only': True, 'indexable': False, 'cases': reports}, ensure_ascii=True, indent=2))
    return 0 if all(report['expectation_met'] for report in reports) else 1


if __name__ == '__main__':
    raise SystemExit(main())
