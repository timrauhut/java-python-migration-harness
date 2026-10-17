"""Minimal five-project RepoTransBench Java -> Python harness.

Uses the official dataset's target_projects/Java/Python/<project>/run_tests.sh.
Run translated tests only inside an isolated disposable environment: benchmark
scripts are untrusted executable code.
"""
import argparse
import ast
import hashlib
import importlib.metadata
import os
import platform
import xml.etree.ElementTree as ET
import asyncio
import json
import re
import shutil
import signal
import sys
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent
PROMPTS = BASE / 'prompts'
MODEL = 'claude-haiku-4-5-20251001'
DATASET_VERSION = 'RepoTransBench v1.0'
DATASET_SUMMARY_SHA256 = 'c4430828b3a37548796b1d78b8211b4c6b2c1c7b850a7f5e46f12f6c04a99ef2'
SOURCE_TREE_SHA256 = '75946316c167048cd6b89aaa53c5ea8bab262fd8713ffe5c88f8c490a96e45d4'
TEST_SUITE_SHA256 = '005d4bc56a53dfd54ca8429248372060ba51dc935a8c518bc3ede672eea1e587'
PROJECTS = (
    'aventrix_jnanoid',
    'agarciadom_xeger',
    'editorconfig_editorconfig-jetbrains',
    'txusballesteros_fit-chart',
    'googlearchive_android-BluetoothLeGatt',
)
EXPECTED_TEST_COUNTS = (23, 40, 12, 16, 19)
EXPECTED_PUBLIC_COUNTS = (9, 21, 6, 8, 8)  # of those, in public_tests/ (repair feedback)
# Each iteration differs from the previous one only in what is listed here.
VARIANTS = {
    'v0': dict(system='v0_system.txt', initial='v0_initial.txt', attempts=1, prompt_version='minimal-1',
               description='Minimal prompt, one attempt'),
    'v1': dict(system='v1_system.txt', initial='v0_initial.txt', attempts=1, prompt_version='interface-rules-1',
               description='v0 + interface rules (layout, Java names, signatures, stdlib only, scope)'),
    'v2': dict(system='v1_system.txt', initial='v0_initial.txt', attempts=5, prompt_version='interface-rules-1+repeat-5',
               repair=None, feedback=False,
               description='v1 with 5 attempts, same task message each time, no test feedback (control for v3)'),
    'v3': dict(system='v1_system.txt', initial='v0_initial.txt', attempts=5, prompt_version='interface-rules-1+public-repair-1',
               repair='v3_repair.txt', feedback=True,
               description='v1 + up to 4 repairs from public-test feedback'),
}


def choose_projects(dataset, manifest_dir=None):
    """Use the five audited RepoTransBench v1.0 Java->Python cases."""
    root = dataset / 'target_projects' / 'Java' / 'Python'
    sources = dataset / 'source_projects' / 'Java'
    if not root.is_dir() or not sources.is_dir():
        raise FileNotFoundError('Expected target_projects/Java/Python and source_projects/Java')
    summary = dataset / 'target_projects' / 'projects_summary.jsonl'
    if not summary.is_file() or hashlib.sha256(summary.read_bytes()).hexdigest() != DATASET_SUMMARY_SHA256:
        raise ValueError('Dataset metadata differs from the audited RepoTransBench v1.0 archive')
    available = {p.name: p for p in root.iterdir()
                 if p.is_dir() and (p / 'run_tests.sh').is_file()
                 and (sources / p.name).is_dir()}
    selected = list(PROJECTS)
    missing = sorted(set(selected) - set(available))
    if missing:
        raise ValueError(f'Projects missing Java source or test runner: {missing}')
    for name in selected:
        if not any((sources / name).rglob('*.java')):
            raise ValueError(f'No Java files for {name}')
    source_digest = hashlib.sha256()
    for name, count_expected, public_expected in zip(PROJECTS, EXPECTED_TEST_COUNTS, EXPECTED_PUBLIC_COUNTS):
        if (available[name] / 'run_tests.sh').read_text().strip() != '#!/bin/bash\nset -e\npytest':
            raise ValueError(f'Official pytest runner changed for {name}')
        ids = expected_test_ids(available[name])
        if len(ids) != count_expected or sum(i.startswith('public_tests/') for i in ids) != public_expected:
            raise ValueError(f'Official test identities changed for {name}')
        for file in sorted((sources / name).rglob('*')):
            if file.is_file():
                source_digest.update(file.relative_to(sources).as_posix().encode() + b'\0')
                source_digest.update(hashlib.sha256(file.read_bytes()).digest())
    suites = {name: protected_fingerprints(available[name]) for name in PROJECTS}
    suite_digest = hashlib.sha256(json.dumps(suites, sort_keys=True).encode()).hexdigest()
    if source_digest.hexdigest() != SOURCE_TREE_SHA256 or suite_digest != TEST_SUITE_SHA256:
        raise ValueError('Selected Java source or official Python tests differ from the audited archive')
    manifest = (manifest_dir or BASE / 'work' / 'repotransbench') / 'selected_projects.json'
    if manifest.is_file() and json.loads(manifest.read_text())['projects'] != selected:
        raise ValueError('Selection manifest differs from requested fixed projects')
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        'strategy': 'Five audited v1.0 suites with implementation-dependent tests and one pytest invocation',
        'dataset_version': DATASET_VERSION,
        'dataset_summary_sha256': DATASET_SUMMARY_SHA256,
        'source_tree_sha256': SOURCE_TREE_SHA256,
        'test_suite_sha256': TEST_SUITE_SHA256,
        'model': MODEL,
        'dataset': str(dataset.resolve()),
        'projects': selected,
        'java_lines': {name: sum(sum(1 for _ in java.open('rb'))
                                 for java in (sources / name).rglob('*.java')) for name in selected},
        'java_production_lines': {
            name: sum(sum(1 for _ in java.open('rb')) for java in (sources / name).rglob('*.java')
                      if ('test' not in java.relative_to(sources / name).parts and
                          not java.stem.lower().endswith(('test', 'tests'))))
            for name in selected}
    }, indent=2) + '\n')
    return [available[name] for name in selected]


def protected_fingerprints(work):
    """Snapshot benchmark/reference inputs, including path names (detect deletions/additions)."""
    result = {}
    for p in work.rglob('*'):
        if not p.is_file():
            continue
        rel = p.relative_to(work)
        parts = rel.parts
        if any(x in ('.pytest_cache', '__pycache__', '.git', '.venv', 'venv') for x in parts):
            continue
        if (p.name == 'run_tests.sh' or p.name.startswith('test_') or
            p.name.endswith('_test.py') or any('test' in x.lower() for x in parts[:-1]) or
            '_java_reference' in parts or p.name in ('pytest.ini', 'conftest.py', 'pyproject.toml', 'tox.ini',
                                                     'project_structure.txt')):
            result[str(rel)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return result


def expected_test_ids(work):
    """Read static pytest IDs from untouched official test files.

    The five fixed suites use plain test functions and Test* methods, without
    parametrization or dynamic collection. Refuse other forms rather than
    guessing a baseline from an incomplete import of untranslated code.
    """
    ids = []
    for folder in ('tests', 'public_tests'):
        for file in sorted((work / folder).rglob('test_*.py')):
            tree = ast.parse(file.read_text(), filename=str(file))
            path = file.relative_to(work).as_posix()
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test'):
                    if node.decorator_list:
                        raise ValueError(f'Unsupported decorated test: {path}::{node.name}')
                    ids.append(f'{path}::{node.name}')
                elif isinstance(node, ast.ClassDef) and node.name.startswith('Test'):
                    if node.decorator_list:
                        raise ValueError(f'Unsupported decorated test class: {path}::{node.name}')
                    for method in node.body:
                        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name.startswith('test'):
                            if method.decorator_list:
                                raise ValueError(f'Unsupported decorated test: {path}::{node.name}::{method.name}')
                            ids.append(f'{path}::{node.name}::{method.name}')
            if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and
                   n.name == 'pytest_generate_tests' for n in ast.walk(tree)):
                raise ValueError(f'Dynamic pytest collection in {path}')
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('No unique static test identities found')
    return sorted(ids)


def junit_identity(node_id):
    """Convert an audited plain pytest node ID to its JUnit classname/name."""
    path, *names = node_id.split('::')
    return (path.removesuffix('.py').replace('/', '.') +
            ('.' + '.'.join(names[:-1]) if len(names) > 1 else ''), names[-1])


def run_bounded(command, work, env, timeout):
    """Kill the whole pytest process group if the time limit expires."""
    process = subprocess.Popen(command, cwd=work, env=env, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def collect_tests(work, timeout):
    """Confirm pytest still collects every ID frozen from the official files."""
    try:
        env = os.environ.copy()
        env['PYTHONPATH'] = str(work.resolve())
        env.pop('PYTEST_ADDOPTS', None)
        env.pop('ANTHROPIC_API_KEY', None)
        env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
        env['PYTHONNOUSERSITE'] = '1'
        p = run_bounded([sys.executable, '-m', 'pytest', '--collect-only', '-q', '--color=no'],
                        work, env, timeout)
        ids = sorted(re.findall(r'^(\S+::\S+)\s*$', p.stdout, re.M))
        if p.returncode != 0 or not ids or len(ids) != len(set(ids)):
            return None, (p.stdout + '\n' + p.stderr)[-2500:]
        return ids, None
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)


def parse_junit(path):
    """Read actual executed tests, not an agent-supplied summary."""
    root = ET.parse(path).getroot()
    cases = list(root.iter('testcase'))
    ids = []
    failures = {}
    passed = failed = skipped = 0
    passed_by_folder = {}
    for case in cases:
        # pytest writes classname and name; unique matching uses this pair.
        identity = (case.get('classname', ''), case.get('name', ''))
        ids.append(identity)
        problem = case.find('failure')
        if problem is None:
            problem = case.find('error')
        if case.find('skipped') is not None:
            skipped += 1
        elif problem is not None:
            failed += 1
            # pytest marks the exception lines with "E   "; keep the first one.
            lines = [l for l in (problem.text or problem.get('message', '')).splitlines() if l.strip()]
            marked = [l[1:].strip() for l in lines if l.startswith('E ')]
            failures['::'.join(identity)] = (marked or lines[-1:] or [''])[0][:300]
        else:
            passed += 1
            folder = identity[0].split(".")[0]
            passed_by_folder[folder] = passed_by_folder.get(folder, 0) + 1
    return passed, failed, skipped, ids, failures, passed_by_folder


def evaluate(work, timeout, expected_ids=None, protected=None):
    script = work / 'run_tests.sh'
    if not script.is_file():
        return dict(migration_passed=False, test_success_rate=None, passed=0,
                    failed=0, skipped=0, total=0, expected_total=len(expected_ids or []),
                    error='Missing run_tests.sh')
    # The benchmark script must execute pytest so PYTEST_ADDOPTS can produce
    # JUnit XML. If not, no success is claimed; a project-specific adapter is needed.
    xml_file = work / '.harness-test-results.xml'
    xml_file.unlink(missing_ok=True)
    env = os.environ.copy()
    env['PYTHONPATH'] = str(work.resolve())
    env.pop('ANTHROPIC_API_KEY', None)
    env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    env['PYTHONNOUSERSITE'] = '1'
    env['PYTEST_ADDOPTS'] = '--junitxml=' + str(xml_file)
    try:
        p = run_bounded(['bash', 'run_tests.sh'], work, env, timeout)
        output = (p.stdout + '\n' + p.stderr)[-5000:]
        if not xml_file.is_file():
            return dict(migration_passed=False, test_success_rate=None,
                        passed=0, failed=0, skipped=0, total=0,
                        expected_total=len(expected_ids or []),
                        exit_code=p.returncode, output_tail=output,
                        error='No pytest JUnit XML generated (unsupported test runner or test collection failed)')
        passed, failed, skipped, executed, failures, passed_by_folder = parse_junit(xml_file)
        total = passed + failed + skipped
        current_ids, collection_error = collect_tests(work, timeout)
        suite_ok = (expected_ids is not None and current_ids == expected_ids and
                    sorted(executed) == sorted(junit_identity(node) for node in expected_ids)
                    and skipped == 0)
        files_ok = protected is not None and protected_fingerprints(work) == protected
        valid = suite_ok and files_ok
        rate = round(100 * passed / len(expected_ids), 2) if valid else None
        ok = (valid and p.returncode == 0 and failed == 0)
        issues = []
        if not suite_ok:
            issues.append('Collected/executed test identities differ from immutable baseline, or tests were skipped')
        if not files_ok:
            issues.append('Protected tests, scripts or Java reference changed')
        if collection_error:
            issues.append('Test collection: ' + collection_error[-700:])
        return dict(migration_passed=ok, test_success_rate=rate, passed=passed,
                    failed=failed, skipped=skipped, total=total,
                    expected_total=len(expected_ids) if expected_ids else None,
                    suite_verified=suite_ok, protected_files_unchanged=files_ok,
                    evaluation_valid=valid,
                    exit_code=p.returncode, output_tail=output, failures=failures,
                    passed_by_folder=passed_by_folder,
                    error='; '.join(issues) if issues else None)
    except (OSError, subprocess.TimeoutExpired, ET.ParseError) as exc:
        return dict(migration_passed=False, test_success_rate=None,
                    passed=0, failed=0, skipped=0, total=0,
                    expected_total=len(expected_ids or []), error=str(exc))


def public_feedback(work, timeout):
    """Run only public_tests/ for repair feedback; output of the hidden tests/ never reaches the agent."""
    env = os.environ.copy()
    env['PYTHONPATH'] = str(work.resolve())
    env.pop('PYTEST_ADDOPTS', None)
    env.pop('ANTHROPIC_API_KEY', None)
    env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    env['PYTHONNOUSERSITE'] = '1'
    try:
        p = run_bounded([sys.executable, '-m', 'pytest', 'public_tests', '-q', '--tb=short', '--color=no'],
                        work, env, timeout)
        return {'all_public_passed': p.returncode == 0,
                'public_test_output': (p.stdout + '\n' + p.stderr)[-5000:]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'all_public_passed': False, 'public_test_output': f'Public test run failed: {exc}'}


async def migrate(work, iteration, feedback, budget, variant="v0"):
    from claude_agent_sdk import ClaudeAgentOptions, HookMatcher, ResultError, ResultMessage, query
    denied_tools = {}
    async def keep_tools_in_workspace(input_data, _tool_use_id, _context):
        tool = input_data.get('tool_name')
        values = input_data.get('tool_input') or {}
        raw = values.get('file_path') or values.get('path')
        if tool in ('Read', 'Write', 'Edit') and not raw:
            denied = True
        else:
            path = Path(raw).expanduser() if raw else work
            path = (path if path.is_absolute() else work / path).resolve()
            denied = not path.is_relative_to(work)
            if tool in ('Write', 'Edit'):
                denied |= not path.is_relative_to(work / 'src')
        if tool == 'Glob':
            pattern = values.get('pattern', '')
            denied |= (not isinstance(pattern, str) or pattern.startswith(('/', '~')) or
                       '..' in pattern.split('/') or '{' in pattern)
        if denied:
            denied_tools[tool] = denied_tools.get(tool, 0) + 1
            return {'hookSpecificOutput': {'hookEventName': 'PreToolUse',
                    'permissionDecision': 'deny',
                    'permissionDecisionReason': 'Only the project workspace is available; edit src/ only'}}
        return {}
    options = ClaudeAgentOptions(
        cwd=str(work),
        model=MODEL,
        tools=['Read', 'Write', 'Edit', 'Glob', 'Grep'],
        allowed_tools=['Read', 'Write', 'Edit', 'Glob', 'Grep'],
        disallowed_tools=['Bash', 'Task', 'Agent', 'Workflow', 'WebFetch', 'WebSearch'],
        permission_mode='acceptEdits',
        setting_sources=[],
        strict_mcp_config=True,
        max_turns=40,
        max_budget_usd=budget,
        hooks={'PreToolUse': [HookMatcher(matcher='Read|Write|Edit|Glob|Grep',
                                        hooks=[keep_tools_in_workspace])]},
        system_prompt=(PROMPTS / VARIANTS[variant]['system']).read_text().rstrip('\n'),
    )
    if iteration == 1 or not VARIANTS[variant].get('repair'):
        prompt = (PROMPTS / VARIANTS[variant]['initial']).read_text().rstrip('\n')
    else:
        repair = (PROMPTS / VARIANTS[variant]['repair']).read_text().rstrip('\n')
        prompt = repair.format(iteration=iteration)
        if feedback is not None:
            prompt += '\n' + json.dumps(feedback, indent=2)
    cost = None
    usage = None
    agent_error = None
    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage):
                cost = message.total_cost_usd
                usage = message.usage
                agent_error = message.subtype if message.is_error else None
    except ResultError as exc:
        cost = exc.data.get('total_cost_usd', cost)
        usage = exc.data.get('usage', usage)
        agent_error = exc.subtype or 'terminal_error'
    if denied_tools:
        print(f'  Claude denied tool calls: {denied_tools}', flush=True)
    if (cost is None or not isinstance(usage, dict) or
            'input_tokens' not in usage or 'output_tokens' not in usage):
        raise RuntimeError('Claude SDK did not report cost and token usage')
    return cost, {key: usage.get(key, 0) for key in
                  ('input_tokens', 'output_tokens', 'cache_read_input_tokens',
                   'cache_creation_input_tokens')}, agent_error


async def run_project(project, dataset, work_root, iterations, budget, timeout, variant="v0"):
    working = work_root / project.name
    agent_work = work_root / (project.name + '.agent')
    if working.exists():
        shutil.rmtree(working)
    shutil.copytree(project, working)
    source = dataset / 'source_projects' / 'Java' / project.name
    if not source.is_dir():
        return {'project': project.name, 'status': 'error', 'migration_passed': False,
                'test_success_rate': None, 'iterations': 0,
                'error': f'Missing original Java repository: {source}'}
    if agent_work.exists():
        for directory in agent_work.rglob('*'):
            if directory.is_dir() and not directory.is_symlink():
                directory.chmod(0o755)
        shutil.rmtree(agent_work)
    agent_work.mkdir()
    reference = agent_work / '_java_reference'
    for file in source.rglob('*'):
        if not file.is_file():
            continue
        relative = file.relative_to(source)
        if (any(part.lower() in ('test', 'tests', 'public_tests', 'original_tests')
                for part in relative.parts[:-1]) or
                file.stem.lower().endswith(('test', 'tests')) or
                file.suffix.lower() not in ('.java', '.xml', '.gradle', '.properties', '.md', '.txt')):
            continue
        destination = reference / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, destination)
    structure = project / 'project_structure.txt'
    if structure.is_file():
        shutil.copy2(structure, reference / structure.name)
    for file in reference.rglob('*'):
        if file.is_file():
            file.chmod(0o444)
    for directory in reference.rglob('*'):
        if directory.is_dir():
            directory.chmod(0o555)
    reference.chmod(0o555)
    # Freeze tests from the untouched official target, before any migration.
    protected = protected_fingerprints(working)
    agent_protected = {k: v for k, v in protected_fingerprints(agent_work).items()
                       if k.startswith('_java_reference/')}
    expected_ids = expected_test_ids(working)
    report = {'project': project.name, 'status': 'not_run', 'migration_passed': False,
              'test_success_rate': None, 'iterations': 0, 'variant': variant,
              'history': [], 'cost_complete': True}
    # Explicit algorithm: one-shot -> evaluate -> (feedback -> repair -> evaluate)*.
    # The attempt count includes the initial one-shot attempt, not just repairs.
    cost = 0.0
    tokens = {key: 0 for key in ('input_tokens', 'output_tokens', 'cache_read_input_tokens',
                                  'cache_creation_input_tokens')}
    for i in range(1, iterations + 1):
        if cost >= budget:
            report['status'] = 'budget_exhausted'
            break
        phase = 'one_shot' if i == 1 else 'repair'
        try:
            uses_tests = VARIANTS[variant].get('feedback', False)
            feedback = report['history'][-1]['public_feedback'] if i > 1 and uses_tests else None
            attempt_cost, attempt_tokens, agent_error = await migrate(
                agent_work, i, feedback, budget - cost, variant=variant)
            cost += attempt_cost
            for key in tokens:
                tokens[key] += attempt_tokens[key]
            current = {k: v for k, v in protected_fingerprints(agent_work).items()
                       if k.startswith('_java_reference/')}
            if current != agent_protected:
                changed = sorted(key for key in current.keys() | agent_protected.keys()
                                 if current.get(key) != agent_protected.get(key))
                raise ValueError(f'Claude changed protected reference files: {changed}')
            implementation = working / 'src'
            if implementation.exists():
                shutil.rmtree(implementation)
            generated = agent_work / 'src'
            if generated.is_dir():
                for file in generated.rglob('*.py'):
                    relative = file.relative_to(generated)
                    if (file.is_symlink() or
                            any(part.lower() in ('test', 'tests', 'public_tests', 'original_tests')
                                for part in relative.parts[:-1]) or
                            file.name.startswith('test_') or file.name.endswith('_test.py')):
                        continue
                    destination = implementation / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(file, destination)
            result = evaluate(working, timeout, expected_ids, protected)
            report.update({k: result[k] for k in ('migration_passed', 'test_success_rate',
                                                   'passed', 'expected_total')})
            report['error'] = result.get('error') or (f'Claude SDK: {agent_error}' if agent_error else None)
            report['history'].append({'iteration': i, 'phase': phase,
                                      'estimated_cost_usd': round(attempt_cost, 4),
                                      'tokens': attempt_tokens, 'agent_error': agent_error, **result})
            report['iterations'] = i
            report['status'] = ('passed' if result['migration_passed'] else
                                'failed' if result['test_success_rate'] is not None else
                                'evaluation_invalid')
            # Failed test integrity / protected-file check cannot be repaired
            # through generated implementation edits. Stop rather than retrying.
            # Without test feedback the loop must not stop on test results either: that would leak them.
            if (result['migration_passed'] and uses_tests) or (agent_error and agent_error != 'error_max_turns'):
                break
            if result.get('protected_files_unchanged') is False:
                report['status'] = 'evaluation_invalid'
                break
            if i < iterations and uses_tests:
                # Repair feedback comes from public_tests/ only. Stop once they all pass, so a
                # further repair request cannot reveal that hidden tests still fail.
                report['history'][-1]['public_feedback'] = public_feedback(working, timeout)
                if report['history'][-1]['public_feedback']['all_public_passed']:
                    break
        except Exception as ex:
            report['status'] = 'error'
            report['error'] = f'{type(ex).__name__}: {ex}'
            report['cost_complete'] = False
            break
    report['estimated_cost_usd'] = round(cost, 4) if report['cost_complete'] else None
    report['tokens'] = tokens
    (working / 'migration_report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dataset', required=True, type=Path, help='Extracted RepoTransBench dataset root')
    ap.add_argument('--variant', choices=list(VARIANTS), default='v0')
    ap.add_argument('--work-dir', type=Path, default=BASE / 'work' / 'repotransbench')
    ap.add_argument('--budget', type=float, default=5.0, help='USD per project')
    ap.add_argument('--timeout', type=int, default=180)
    ap.add_argument('--results-dir', type=Path, help='Also store the final report as <dir>/runs/<variant>/run-<k>/report.json')
    ap.add_argument('--list', action='store_true', help='List the five selected project names without running')
    args = ap.parse_args()
    if args.budget <= 0:
        ap.error('--budget must be > 0')
    projects = choose_projects(args.dataset.resolve(), args.work_dir.resolve())
    iterations = VARIANTS[args.variant]['attempts']
    if args.list:
        print('\n'.join(p.name for p in projects))
        return
    def installed_version(package):
        try:
            return importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            return None
    config = {
        'dataset_version': DATASET_VERSION,
        'dataset_summary_sha256': DATASET_SUMMARY_SHA256,
        'source_tree_sha256': SOURCE_TREE_SHA256,
        'test_suite_sha256': TEST_SUITE_SHA256,
        'project_ids': [p.name for p in projects],
        'model': MODEL,
        'variant': args.variant,
        'prompt_version': VARIANTS[args.variant]['prompt_version'],
        'evaluator_version': 'static-junit-1',
        'max_attempts': iterations,
        'budget_usd_per_project': args.budget,
        'timeout_seconds': args.timeout,
        'python_version': platform.python_version(),
        'pytest_version': installed_version('pytest'),
        'claude_agent_sdk_version': installed_version('claude-agent-sdk'),
    }
    work_root = args.work_dir.resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    results = []
    for p in projects:
        print(f'Project: {p.name}', flush=True)
        report = await run_project(p, args.dataset.resolve(), work_root, iterations, args.budget, args.timeout, variant=args.variant)
        results.append(report)
        print(f"  {report['status']}: {report['test_success_rate']}%", flush=True)
        if report.get('error'):
            print(f"  reason: {report['error']}", flush=True)
        (work_root / 'report.json').write_text(json.dumps({'config': config, 'projects': results, 'summary': {
            'migration_passed': sum(r['migration_passed'] for r in results),
            'total_projects': len(results),
            'test_success_rate': None if any(r['test_success_rate'] is None for r in results)
                else round(sum(r['test_success_rate'] for r in results) / len(results), 2)
        }}, indent=2) + '\n')
    print(f'Report: {work_root / "report.json"}')
    if args.results_dir:
        runs = args.results_dir.resolve() / 'runs' / args.variant
        k = 1 + max((int(d.name.removeprefix('run-')) for d in runs.glob('run-*')), default=0)
        (runs / f'run-{k}').mkdir(parents=True)  # never overwrite a stored run
        shutil.copy2(work_root / 'report.json', runs / f'run-{k}' / 'report.json')
        print(f'Stored: {runs / f"run-{k}" / "report.json"}')


if __name__ == '__main__':
    asyncio.run(main())
