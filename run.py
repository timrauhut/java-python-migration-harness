"""Run all harness variants repeatedly, then build summary.json and report.html from every stored run.

Usage:
  python run.py --dataset /dataset                # 3 new runs of v0-v3 (calls Claude, costs money)
  python run.py --dataset . --repeats 0           # no API calls: only rebuild summary.json and report.html

repotransbench.py stores each run as <results-dir>/runs/<variant>/run-<k>/report.json and never overwrites one.
"""
import argparse
import html
import json
import statistics
import subprocess
import sys
from collections import Counter
from pathlib import Path

from failures import CATEGORIES, final_failures
from repotransbench import EXPECTED_PUBLIC_COUNTS, EXPECTED_TEST_COUNTS, PROJECTS, VARIANTS

ROOT = Path(__file__).resolve().parent
TOTAL = sum(EXPECTED_TEST_COUNTS)
PUBLIC = sum(EXPECTED_PUBLIC_COUNTS)
SAME_CONFIG = ('dataset_version', 'dataset_summary_sha256', 'source_tree_sha256', 'test_suite_sha256',
               'model', 'evaluator_version', 'budget_usd_per_project', 'timeout_seconds',
               'python_version', 'pytest_version', 'claude_agent_sdk_version')
NUMBERS = ('passed_pct', 'first_attempt_pct', 'successful_migrations', 'valid_suites', 'turn_limit_projects',
           'attempts', 'cost_usd')
# Plain-language meaning of each failure category, for the report.
MEANING = {'missing name': 'The test uses a class, function or constant that does not exist, usually because it was '
                           'renamed (random_nano_id instead of randomNanoId).',
           'missing module': 'The test imports a file the agent never created.',
           'wrong import path': "The agent's own files import each other without the src. prefix.",
           'missing dependency': 'The code needs a third-party package that is not installed.',
           'wrong call signature': 'The function exists but takes different arguments.',
           'wrong result': 'The code runs but returns a wrong value.',
           'other exception': 'The code raises an error the test does not expect.'}


def scored_passes(result):
    """Invalid suites earn no credit, including in historical reports."""
    return result.get("passed", 0) if result.get("test_success_rate") is not None else 0


def run_stats(report):
    """Scores of one run. An invalid suite contributes zero to every score."""
    projects = report['projects']
    def split(attempt):  # passed public / hidden tests, if the run recorded it
        folders = [(p['history'][attempt].get('passed_by_folder')
                    if p['history'][attempt].get('test_success_rate') is not None else {})
                   if p['history'] else {} for p in projects]
        if any(f is None for f in folders):
            return None
        return {'public': sum(f.get('public_tests', 0) for f in folders),
                'hidden': sum(f.get('tests', 0) for f in folders)}
    return {'passed_pct': round(100 * sum(scored_passes(p) for p in projects) / TOTAL, 2),
            'successful_migrations': sum(bool(p['migration_passed']) for p in projects),
            'valid_suites': sum(p['test_success_rate'] is not None for p in projects),
            'turn_limit_projects': sum(any(h.get('agent_error') == 'error_max_turns' for h in p['history'])
                                       for p in projects),
            'first_attempt_pct': round(100 * sum(scored_passes(p['history'][0]) if p['history'] else 0
                                                 for p in projects) / TOTAL, 2),
            'attempts': sum(p['iterations'] for p in projects),
            'cost_usd': round(sum(p.get('estimated_cost_usd') or 0 for p in projects), 4),
            'passed_first_attempt': split(0), 'passed_final': split(-1)}


def summarize(runs):
    stats = [run_stats(r) for r in runs]
    categories = Counter()
    for report in runs:
        for p in report['projects']:
            categories.update(final_failures(p)[0])
    splits = {}
    for key in ('passed_first_attempt', 'passed_final'):
        if all(s[key] for s in stats):
            splits[key] = {side: sum(s[key][side] for s in stats) for side in ('public', 'hidden')}
    projects = [p for report in runs for p in report['projects']]
    accounting = {
        'passed': sum(scored_passes(p) for p in projects),
        'failed_in_valid_suites': sum(p['expected_total'] - scored_passes(p) for p in projects
                                      if p['test_success_rate'] is not None),
        'tests_in_invalid_suites': sum(p['expected_total'] for p in projects
                                       if p['test_success_rate'] is None),
        'invalid_suites': sum(p['test_success_rate'] is None for p in projects),
    }
    return {'test_accounting': accounting,
            'description': VARIANTS[runs[0]['config']['variant']]['description'],
            'prompt_version': runs[0]['config']['prompt_version'],
            'max_attempts': runs[0]['config']['max_attempts'],
            'runs': len(runs), 'per_run': stats,
            'mean': {k: round(statistics.mean(s[k] for s in stats), 2) for k in NUMBERS},
            'sd': ({k: round(statistics.stdev(s[k] for s in stats), 2) for k in NUMBERS}
                   if len(stats) > 1 else None),
            'total': {k: round(sum(s[k] for s in stats), 4) for k in NUMBERS if not k.endswith('_pct')},
            'failed_tests_by_category': dict(categories.most_common()),
            'public_hidden': splits or None,
            'per_project_rates': {name: [next(p['test_success_rate'] for p in r['projects'] if p['project'] == name)
                                         for r in runs] for name in PROJECTS}}


def render_report(summary, runs, output):
    """One offline page; every number comes from the stored reports."""
    e = lambda value: html.escape(str(value), quote=True)
    variants = summary['variants']
    n_projects = len(PROJECTS)
    counts = sorted({d['runs'] for d in variants.values()})
    n_runs = str(counts[0]) if len(counts) == 1 else f'{counts[0]}–{counts[-1]}'
    page = ['<!doctype html><html lang="en"><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            '<title>Migration Harness Report</title>',
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700'
            '&family=JetBrains+Mono:wght@400;600&display=swap"><style>',
            # Same design language as the slide deck: near-black, off-white, grey cards, Inter + JetBrains Mono.
            ':root{--ink:#171717;--muted:#444444;--line:#dcdcdc;--paper:#f2f2f2;--bg:#fcfcfc;--dark:#090909;'
            '--dark-card:#1a1a1a;--on-dark:#aeaeae;--bar:#171717;--soft:#bdbdbd;--red:#a1353b;--green:#18734b}',
            '*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);'
            'font:16px/1.55 Inter,system-ui,-apple-system,"Segoe UI",Arial,sans-serif}',
            '.wrap{max-width:1040px;margin:auto;padding:32px 20px 64px}',
            'header{background:var(--dark);color:#f5f5f5;border-radius:16px;padding:40px 44px}',
            '.logo{font-weight:600;line-height:1.05;letter-spacing:.25em;font-size:22px;color:#fafafa;margin:0 0 40px}',
            '.kicker{margin:0;color:var(--on-dark);font-size:13px;letter-spacing:.18em;text-transform:uppercase}',
            'header h1{margin:10px 0 14px;font-size:clamp(30px,5vw,48px);font-weight:700;line-height:1.05;letter-spacing:-.03em}',
            'header p{margin:0;color:#d4d4d4;max-width:760px;font-weight:300;font-size:18px}',
            'h2{font-size:28px;font-weight:700;letter-spacing:-.02em;margin:52px 0 6px}.intro{color:var(--muted);margin:0 0 16px;max-width:780px}',
            '.panel{background:var(--paper);border-radius:16px;padding:22px 26px;overflow-x:auto}',
            '.keys{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;margin-top:14px}',
            '.keys .panel{background:var(--ink);color:#f5f5f5}.keys .panel b{display:block;font-size:17px;font-weight:600;color:#d4d4d4;margin-bottom:4px}',
            '.row{display:grid;grid-template-columns:52px 1fr 150px;gap:16px;align-items:center;padding:14px 0;border-bottom:1px solid var(--line)}',
            '.row:last-child{border-bottom:0}.v{font-weight:700;font-size:20px}.what{font-size:14px;color:var(--muted);margin-bottom:6px}',
            # Dot plot as on the slides: band = lowest to highest run, dots = runs, black line = average.
            '.track{position:relative;height:40px}.track::before{content:"";position:absolute;left:0;right:0;top:19px;height:2px;background:var(--line)}',
            '.range{position:absolute;top:12px;height:16px;background:#dcdcdc;border-radius:8px}',
            '.dot{position:absolute;top:13px;width:14px;height:14px;margin-left:-7px;border-radius:50%;background:#8a8a8a}',
            '.mean{position:absolute;top:0;bottom:0;width:4px;margin-left:-2px;background:var(--ink);border-radius:2px}',
            '.axis{display:grid;grid-template-columns:52px 1fr 150px;gap:16px;padding-top:6px}',
            '.ticks{position:relative;height:18px;font-size:12px;color:var(--muted)}.ticks span{position:absolute;transform:translateX(-50%)}',
            '.ticks span:first-child{transform:none}.ticks span:last-child{transform:translateX(-100%)}',
            '.num{font-variant-numeric:tabular-nums}.score{font-size:24px;font-weight:700;letter-spacing:-.02em}'
            '.score small{display:block;font-size:13px;color:var(--muted);font-weight:400;letter-spacing:0}',
            'table{width:100%;border-collapse:collapse;min-width:600px}th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top}',
            'tr:last-child td{border-bottom:0}th{font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}'
            'td.num,th.num{text-align:right}',
            '.invalid{color:var(--red)}.pass{color:var(--green);font-weight:700}.meaning{color:var(--muted);font-size:14px}',
            'details{background:var(--paper);border-radius:12px;margin:6px 0}summary{cursor:pointer;padding:10px 16px;font-weight:600}',
            'pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:0;padding:16px;border-top:1px solid var(--line);'
            'font:12px/1.5 "JetBrains Mono","Courier New",monospace;max-height:520px;overflow:auto}',
            '@media(max-width:640px){.row{grid-template-columns:44px 1fr}.row .score{grid-column:2}header{padding:28px 24px}}',
            'footer{display:flex;gap:20px;align-items:center;color:var(--muted);font-size:12px;margin-top:40px;'
            'padding-top:20px;border-top:1px solid var(--line)}',
            'footer .logo{font-size:14px;color:var(--ink);margin:0;flex:none}</style><div class="wrap">',
            '<header><p class="logo">RAU<br>HUT</p><p class="kicker">Coding challenge · RepoTransBench v1.0</p>'
            '<h1>Java → Python<br>migration harness</h1>',
            f'<p>A coding agent ({e(summary["model"])}) translates {n_projects} Java repositories to Python. '
            f'The harness then runs each project\'s official tests ({TOTAL} in total) on the result. '
            f'Each version tests an instruction bundle or an attempt strategy and was run {n_runs} times.</p></header>',
            '<div class="keys">',
            f'<div class="panel"><b>Tests passed</b>Share of the {TOTAL} official tests that pass. '
            'All tests in invalid suites receive zero credit.</div>',
            f'<div class="panel"><b>Projects fully migrated</b>Projects where every official test passes, '
            f'out of {n_projects} per run.</div>',
            f'<div class="panel"><b>Runs</b>The agent is not deterministic, so every version ran {n_runs} times. '
            'The black line is the average, the dots are the single runs.</div></div>',
            '<h2>Results</h2><p class="intro">Tests passed per version. <b>Black line:</b> average of the runs · <b>dots:</b> single runs · '
            '<b>grey band:</b> lowest to highest run. Same model, projects and tests.</p><div class="panel">']
    def score_label(d):
        label = f'{d["mean"]["passed_pct"]:.1f}%'
        return label + (f' ± {d["sd"]["passed_pct"]:.1f}' if d['sd'] else '')

    for v, d in variants.items():
        m = d['mean']
        dots = ''.join(f'<span class="dot" style="left:{s["passed_pct"]}%" title="run {i + 1}: {s["passed_pct"]:g}%"></span>'
                       for i, s in enumerate(d['per_run']))
        lo, hi = min(s['passed_pct'] for s in d['per_run']), max(s['passed_pct'] for s in d['per_run'])
        page.append(f'<div class="row"><div class="v">{e(v)}</div><div><div class="what">{e(d["description"])}</div>'
                    f'<div class="track"><span class="range" style="left:{lo}%;width:{hi - lo}%"></span>{dots}'
                    f'<span class="mean" style="left:{m["passed_pct"]}%" title="average: {m["passed_pct"]:g}%"></span></div></div>'
                    f'<div class="score num">{score_label(d)}<small>{m["successful_migrations"]:.1f} of {n_projects} '
                    'fully migrated</small></div></div>')
    page.append('<div class="axis"><div></div><div class="ticks">' +
                ''.join(f'<span style="left:{t}%">{t}%</span>' for t in (0, 25, 50, 75, 100)) + '</div><div></div></div></div>')
    page.append('<h2>The numbers</h2><p class="intro">Mean ± sample standard deviation for tests passed; single runs are in brackets. '
                '<i>First attempt</i> is the score before any repeat or repair.</p><div class="panel"><table>'
                '<tr><th>Version</th><th class="num">Tests passed</th><th class="num">First attempt</th>'
                '<th class="num">Fully migrated</th><th class="num">Agent attempts</th><th class="num">Cost per run</th></tr>')
    for v, d in variants.items():
        m, runs_ = d['mean'], d['per_run']
        page.append(f'<tr><td><b>{e(v)}</b></td><td class="num"><b>{score_label(d)}</b> '
                    f'({" · ".join(f"{s["passed_pct"]:.0f}" for s in runs_)})</td>'
                    f'<td class="num">{m["first_attempt_pct"]:.0f}%</td>'
                    f'<td class="num">{m["successful_migrations"]:.1f} / {n_projects}</td>'
                    f'<td class="num">{m["attempts"]:.0f}</td><td class="num">${m["cost_usd"]:.2f}</td></tr>')
    page.append('</table></div>')
    page.append('<h2>Test accounting</h2><p class="intro">Counts across all runs. '
                'Passed tests, failed tests in valid suites, and tests in invalid suites sum to the fixed denominator. '
                'Diagnostic failure records below can include several collection errors from one invalid suite.</p>'
                '<div class="panel"><table><tr><th>Version</th><th>Passed</th><th>Failed in valid suites</th>'
                '<th>Tests in invalid suites</th><th>Invalid suites</th></tr>')
    for v, d in variants.items():
        a = d['test_accounting']
        page.append(f'<tr><td>{e(v)}</td><td>{a["passed"]}</td><td>{a["failed_in_valid_suites"]}</td>'
                    f'<td>{a["tests_in_invalid_suites"]}</td><td>{a["invalid_suites"]}</td></tr>')
    page.append('</table></div>')
    page.append('<h2>Why tests fail</h2><p class="intro">Recorded failure entries from final attempts, including collection errors, grouped by their error '
                '(rules in failures.py). These are diagnostic records, not the number of unsuccessful tests.</p>'
                '<div class="panel"><table><tr><th>Reason</th>' + ''.join(f'<th class="num">{e(v)}</th>' for v in variants) + '</tr>')
    names = [n for n in ['wrong import path', *(c for c, _ in CATEGORIES)]
             if any(n in d['failed_tests_by_category'] for d in variants.values())]
    for name in names:
        page.append(f'<tr><td><b>{e(name)}</b><div class="meaning">{e(MEANING.get(name, ""))}</div></td>' +
                    ''.join(f'<td class="num">{d["failed_tests_by_category"].get(name, 0)}</td>' for d in variants.values()) + '</tr>')
    page.append('<tr><th>Total failure records</th>' + ''.join(f'<th class="num">{sum(d["failed_tests_by_category"].values())}</th>'
                                                          for d in variants.values()) + '</tr></table></div>')
    split = {v: d['public_hidden'] for v, d in variants.items()
             if d['public_hidden'] and d['max_attempts'] > 1 and 'passed_first_attempt' in d['public_hidden']}
    if split:
        page.append(f'<h2>Do the fixes hold on unseen tests?</h2><p class="intro">The tests are split: {PUBLIC} public tests may be '
                    f'shown to the agent as feedback, {TOTAL - PUBLIC} hidden tests never are. Hidden-test gains support '
                    'generalization within these projects; they do not establish performance on new repositories.</p>'
                    '<div class="panel"><table><tr><th>Version</th><th>Tests</th><th class="num">First attempt</th><th class="num">Final</th></tr>')
        for v, s in split.items():
            n = variants[v]['runs']
            for side, size in (('public', PUBLIC), ('hidden', TOTAL - PUBLIC)):
                a, b = s['passed_first_attempt'][side], s['passed_final'][side]
                page.append(f'<tr><td><b>{e(v)}</b></td><td>{side} ({size} tests × {n} runs)</td>'
                            f'<td class="num">{100 * a / (size * n):.0f}%</td><td class="num">{100 * b / (size * n):.0f}%</td></tr>')
        page.append('</table></div>')
    page.append(f'<h2>Per project</h2><p class="intro">Tests passed in each of the {n_runs} runs. ✓ = all tests pass, '
                '<span class="invalid">—</span> = the suite was invalid (e.g. a collection error or skipped test), so the project counts 0.</p>'
                '<div class="panel"><table><tr><th>Project</th>' + ''.join(f'<th>{e(v)}</th>' for v in variants) + '</tr>')
    cell = lambda r: ('<span class="invalid">—</span>' if r is None else
                      '<span class="pass">✓</span>' if r == 100 else f'{r:.0f}%')
    for name in PROJECTS:
        page.append(f'<tr><td>{e(name)}</td>' + ''.join(
            '<td class="num" style="text-align:left">' + ' · '.join(cell(r) for r in d['per_project_rates'][name]) + '</td>'
            for d in variants.values()) + '</tr>')
    page.append('</table></div><h2>Raw records</h2><p class="intro">Everything the harness stored per project and run, '
                'including every attempt, test failure, token count and cost.</p>')
    for v, reports in runs.items():
        for k, report in enumerate(reports, 1):
            for p in report['projects']:
                rate = '—' if p['test_success_rate'] is None else f'{p["test_success_rate"]}%'
                page.append(f'<details><summary>{e(v)} · run {k} · {e(p["project"])} · '
                            f'{rate} · {p["iterations"]} attempt(s)</summary>'
                            f'<pre>{e(json.dumps(p, indent=2))}</pre></details>')
    page.append(f'<footer><p class="logo">RAU<br>HUT</p><span>Dataset {e(summary["dataset_version"])}, metadata SHA-256 {e(summary["dataset_summary_sha256"])}. '
                'Generated by run.py from results/runs/*/run-*/report.json.</span></footer></div></html>')
    output.write_text('\n'.join(page) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--variants', nargs='+', choices=list(VARIANTS), default=list(VARIANTS))
    parser.add_argument('--repeats', type=int, default=3, help='New runs per variant (0: only rebuild the report)')
    parser.add_argument('--budget', type=float, default=5.0, help='Max USD per project per run')
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--work-dir', type=Path, default=ROOT / 'work')
    parser.add_argument('--results-dir', type=Path, default=ROOT / 'results')
    args = parser.parse_args()
    results = args.results_dir.resolve()
    for k in range(args.repeats):
        for variant in args.variants:
            print(f'\n=== {variant} · new run {k + 1}/{args.repeats} ===', flush=True)
            status = subprocess.run([sys.executable, str(ROOT / 'repotransbench.py'),
                                     '--dataset', str(args.dataset.resolve()), '--variant', variant,
                                     '--work-dir', str(args.work_dir.resolve() / variant),
                                     '--results-dir', str(results),
                                     '--budget', str(args.budget), '--timeout', str(args.timeout)])
            if status.returncode != 0:
                raise RuntimeError(f'{variant} run failed (exit code {status.returncode})')
    runs = {v: [json.loads(path.read_text()) for path in
                sorted((results / 'runs' / v).glob('run-*/report.json'),
                       key=lambda path: int(path.parent.name.removeprefix('run-')))]
            for v in VARIANTS}
    runs = {v: reports for v, reports in runs.items() if reports}
    if not runs:
        raise RuntimeError(f'No stored runs in {results / "runs"}')
    first = next(iter(runs.values()))[0]['config']
    for v, reports in runs.items():
        for report in reports:
            if [p['project'] for p in report['projects']] != list(PROJECTS):
                raise RuntimeError(f'{v} run used different projects')
            for key in SAME_CONFIG:
                if report['config'][key] != first[key]:
                    raise RuntimeError(f'{v} run used a different {key}: {report["config"][key]!r}')
    summary = {key: first[key] for key in ('dataset_version', 'dataset_summary_sha256', 'model')}
    summary['variants'] = {v: summarize(reports) for v, reports in runs.items()}
    (results / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    render_report(summary, runs, results / 'report.html')
    for v, d in summary['variants'].items():
        print(f'{v}: passed {d["mean"]["passed_pct"]}% ± {(d["sd"] or {}).get("passed_pct", "–")} over {d["runs"]} run(s), '
              f'successful {d["mean"]["successful_migrations"]}/{len(PROJECTS)}')
    print(f'Summary: {results / "summary.json"}\nReport:  {results / "report.html"}')


if __name__ == '__main__':
    main()
