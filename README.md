# Java → Python Migration Harness

A reproducible harness that turns a coding agent (Claude Agent SDK, Claude Haiku 4.5) into a Java → Python repository migrator. It is evaluated on five [RepoTransBench v1.0](https://github.com/DeepSoftwareAnalytics/RepoTransBench/releases/tag/v1.0) projects with their official test suites (110 tests). Starting from a minimal baseline, each version changes one thing, based on the failures of the previous one.

## Results

Mean over 6 runs per version, same model, projects and tests:

| Version | What it adds | Tests passed | Projects fully migrated | Cost per run |
|---|---|---|---|---|
| v0 | Minimal prompt, one attempt | 22.6% | 0.3 of 5 | $1.49 |
| v1 | + five interface rules in the system prompt | 32.7% | 0.3 of 5 | $0.84 |
| v2 | v1 × 5 attempts, no feedback (control) | 20.4% | 0.7 of 5 | $4.37 |
| **v3** | **v1 + up to 4 repairs from public-test feedback** | **84.1%** | **2.3 of 5** | $2.28 |

- **Most baseline failures are interface mismatches, not wrong logic:** renamed functions, invented modules, changed signatures. v1's rules removed whole error types: missing modules and dependencies went to 0, and the turn limit was hit in 7% of projects instead of 60%.
- **Retrying without feedback doesn't help:** in v2 the score changed in only 2 of 120 attempt-to-attempt steps.
- **Public-test feedback does:** hidden tests, which the agent never sees, rose from 44% to 82%, almost as much as the public ones (44% → 86%). So the repairs fix the migration instead of fitting the feedback.

Full report: [`results/report.html`](results/report.html) · failure analysis per version: [`docs/analysis.md`](docs/analysis.md)

## How it works

- **Agent:** file tools only (`Read`, `Write`, `Edit`, `Glob`, `Grep`), with no shell or web access. It sees the Java sources and the benchmark's `project_structure.txt`, and a hook blocks writes outside `src/`. Limits are 40 turns per attempt and $5 per project.
- **Evaluation:** the generated `src/` is copied into an untouched copy of the official project, and its `run_tests.sh` is run there. The score comes from the JUnit XML output, never from the agent. Changed test IDs, skipped tests or modified protected files make a run invalid, and an invalid suite scores 0.
- **Public vs. hidden tests:** v3 only gets feedback from `public_tests/` (52 tests) and stops once they all pass. The 58 hidden tests are run for scoring only; their results never reach the agent.
- **Failure categories:** [`failures.py`](failures.py) groups every failure by its pytest error (missing name, missing module, wrong call signature, wrong result …). These counts decide what the next version changes.

**One v3 attempt, step by step.** The agent never runs code or tests; every test run happens in the harness between attempts.

1. The agent writes or repairs `src/`.
2. The harness copies `src/` into a sealed copy of the official project.
3. It runs `run_tests.sh` over all tests. This is the score; it goes into `report.json` only.
4. It runs `pytest public_tests/` separately.
5. If all public tests pass, or 5 attempts or $5 are used, it stops and the last attempt's score counts. Otherwise the public pytest output goes into the repair prompt, and the agent starts again at step 1.

v0 and v1 run steps 1–3 once. v2 runs them 5 times with the same prompt and no test output.

**Benchmark projects:**

| Project | Java LOC | Tests | Public |
|---|---|---|---|
| aventrix_jnanoid | 134 | 23 | 9 |
| agarciadom_xeger | 217 | 40 | 21 |
| editorconfig_editorconfig-jetbrains | 557 | 12 | 6 |
| txusballesteros_fit-chart | 674 | 16 | 8 |
| googlearchive_android-BluetoothLeGatt | 938 | 19 | 8 |

## Quick start

Requirements: Docker and an Anthropic API key.

```sh
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env && chmod 600 .env
docker build -t repotransbench-migration:experiment-v1 .

mkdir -p results
docker run --rm --init --read-only --cap-drop=ALL \
  --security-opt=no-new-privileges --pids-limit=128 --memory=3g --cpus=4 \
  --tmpfs /scratch:rw,size=1g,mode=1777 --tmpfs /tmp:rw,size=256m,mode=1777 \
  --user "$(id -u):$(id -g)" --env HOME=/scratch --env-file .env \
  --mount "type=bind,src=$(pwd)/results,dst=/results" \
  repotransbench-migration:experiment-v1
```

This runs 3 new runs of every version (about $28, several hours) and rebuilds the report from all stored runs. Existing runs are never overwritten. For a single v3 run, add this to the end of the `docker run` command:

```sh
python /app/run.py --dataset /dataset --work-dir /scratch/work --results-dir /results --variants v3 --repeats 1
```

Without Docker or API calls, from the stored runs:

```sh
python3 run.py --dataset . --repeats 0                  # rebuild summary.json and report.html
python3 failures.py results/runs/v3/run-*/report.json   # failure categories per project
```

The Docker image pins the base image, the dataset checksum and all Python packages. The API key is passed only at run time and is not stored in the image or in Git.

## Project structure

| Path | Content |
|---|---|
| `repotransbench.py` | Harness: workspace setup, agent call, sandbox hook, evaluation, repair loop, version definitions |
| `run.py` | Runs versions repeatedly; builds `results/summary.json` and `results/report.html` |
| `failures.py` | Failure categories from the stored reports |
| `prompts/` | System, task and repair prompts per version |
| `results/runs/` | Raw report of every run (scores, failures, attempts, tokens, cost) |
| `docs/analysis.md` | Detailed failure analysis, attribution and test accounting |

## Limitations

- **Small sample:** 5 projects, 6 runs per version, one model. Single runs vary a lot (the same setup scored 0–69% on its first attempt), so v1's score gain is not conclusive. The vanished error types are the clearer evidence.
- **The five v1 rules were tested as one bundle.** The Java naming rule fixed two projects but broke editorconfig, whose tests expect Python-style names.
- **No held-out projects:** the iterations were designed on the same benchmark.

Next steps: keep the best attempt instead of the last one, check that every module imports before accepting "done", and resume the agent session between repairs. Details are in [`docs/analysis.md`](docs/analysis.md#next-steps).
