"""Count diagnostic failure categories in one or more harness reports.

Usage: python failures.py results/runs/v0/run-1/report.json [more reports ...]

Uses the final attempt of each project. Each recorded failure (including collection errors) is put into the first
matching category below, based on its pytest exception line.
"""
import json
import re
import sys
from collections import Counter

CATEGORIES = (
    ('missing dependency', r"ModuleNotFoundError: No module named '(?!src\b)"),
    ('missing module', r'ModuleNotFoundError'),
    ('missing name', r'ImportError|has no attribute'),
    ('wrong call signature', r'TypeError'),
    ('wrong result', r'AssertionError|^assert\b'),
    ('other exception', r''),
)


def category(message, project):
    # The project's own package imported without the "src." prefix is not a dependency.
    missing = re.search(r"No module named '(?!src\b)([^'.]+)", message)
    if missing and missing.group(1).replace('_', '') in re.sub(r'[-_]', '', project.lower()):
        return 'wrong import path'
    return next(name for name, pattern in CATEGORIES if re.search(pattern, message))


def final_failures(p):
    """Category counts of failure records in a project's final attempt, and whether they are approximate."""
    last = p['history'][-1] if p.get('history') else {}
    failures = last.get('failures')
    approx = failures is None  # older reports: only the truncated output tail exists
    if approx:
        failures = {i: l[1:].strip() for i, l in enumerate(last.get('output_tail', '').splitlines())
                    if re.match(r'E\s+\w+(Error|Exception)\b', l)}
    return Counter(category(m, p['project']) for m in failures.values()), approx


def main(paths):
    total_tests, total_projects = Counter(), Counter()
    for path in paths:
        report = json.load(open(path))
        print(f'\n{path}  ({report.get("config", {}).get("variant", "?")})')
        for p in report['projects']:
            last = p['history'][-1] if p.get('history') else {}
            tests, approx = final_failures(p)
            flags = [p['status']]
            if last.get('agent_error') == 'error_max_turns':
                flags.append('turn limit hit')
            if 'timed out' in (p.get('error') or ''):
                flags.append('test timeout')
            total_projects.update(flags)
            total_tests.update(tests)
            rate = '—' if p['test_success_rate'] is None else f'{p["test_success_rate"]}%'
            folders = last.get('passed_by_folder')
            split = '' if folders is None else (f'  [passed public {folders.get("public_tests", 0)}, '
                                                f'hidden {folders.get("tests", 0)}; attempts {len(p["history"])}]')
            print(f'  {p["project"][:38]:38} {rate:>7}  {", ".join(flags)}{split}')
            for name, count in tests.most_common():
                print(f'      {count:3} {name}{" (approx., from output tail)" if approx else ""}')
    print(f'\nTotal over {len(paths)} report(s)')
    print('  projects: ' + ', '.join(f'{n} {k}' for k, n in total_projects.most_common()))
    print('  failure records: ' + ', '.join(f'{n} {k}' for k, n in total_tests.most_common()))


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
