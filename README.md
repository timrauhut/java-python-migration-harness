# Repository Migration Harness · Java → Python

A small, reproducible harness that specializes a coding agent (Claude Agent SDK, `claude-haiku-4-5-20251001`) for migrating whole Java repositories to Python. It is evaluated on five fixed [RepoTransBench v1.0](https://github.com/DeepSoftwareAnalytics/RepoTransBench/releases/tag/v1.0) projects with their official test suites (110 tests). Starting from a minimal baseline, the variants test an instruction bundle, repeated attempts, and public-test feedback. Every version was run 3 times.

| Version | Change | Tests passed (mean ± SD) | Projects fully migrated | Cost per run |
|---|---|---|---|---|
| **v0** | Minimal prompt, one attempt | 23.3% ± 11.0 | 0.3 of 5 | $1.44 |
| **v1** | + interface rules in the system prompt | 41.5% ± 11.3 | 0.3 of 5 | $0.82 |
| **v2** | v1 with 5 attempts, no feedback (control) | 13.3% ± 11.6 | 0.3 of 5 | $4.63 |
| **v3** | v1 + up to 4 repairs with **public**-test feedback | **87.6% ± 19.2** | **2.7 of 5** | $1.69 |

- **Tests passed:** share of the 110 official tests that pass, averaged over 3 runs. All tests in an invalid suite receive zero credit. SD is the sample standard deviation over the three runs.
- **Projects fully migrated:** projects in which every official test passes, out of 5 per run.

**Observed results:**
- The instruction bundle (v1) is associated with fewer interface errors.
- Repeated attempts without feedback (v2) produced little improvement in these runs.
- The public-test repair strategy (v3) improved both public and hidden test scores on these projects.

The full report is [`results/report.html`](results/report.html) and the numbers are in [`results/summary.json`](results/summary.json).

## Setup

**Agent.** The agent uses the Claude Agent SDK with `Read`, `Write`, `Edit`, `Glob` and `Grep` only: no shell, web or subagents. It works in a separate workspace that holds the non-test Java sources and the benchmark's `project_structure.txt` under `_java_reference/` (read-only). A hook rejects writes outside `src/`. Limits are 40 turns per attempt, $5 per project and 180 s per test run. Every attempt is a fresh agent session; attempts share only the files in the workspace.

**Evaluation.** The harness copies only the generated `src/**/*.py` into an untouched copy of the official target project. It runs that project's `run_tests.sh` (pytest) there, and the JUnit XML output decides the score, not the agent. A run is *invalid* in three cases:
- the collected or executed test IDs differ from the frozen official IDs;
- a test is skipped;
- a protected file (tests, runner, Java reference) changed.

Invalid suites contribute zero to final, first-attempt, and public/hidden scores. Raw diagnostic counts are retained in the reports.

The Docker image pins the base image digest, the dataset archive checksum, the source and test tree hashes, and all Python packages.

**Projects** (Java production lines · official tests · of which public):

| Project | Java LOC | Tests | Public |
|---|---|---|---|
| aventrix_jnanoid | 134 | 23 | 9 |
| agarciadom_xeger | 217 | 40 | 21 |
| editorconfig_editorconfig-jetbrains | 557 | 12 | 6 |
| txusballesteros_fit-chart | 674 | 16 | 8 |
| googlearchive_android-BluetoothLeGatt | 938 | 19 | 8 |

**Method.**
- **Controlled variants:** v1 adds five instruction rules as a bundle; v2 adds repeated attempts; v3 adds feedback, repair instructions and early stopping (see `VARIANTS` in `repotransbench.py`).
- **3 runs each:** single runs of an identical setup differ by far more than 10 points.
- **Failure categories:** [`failures.py`](failures.py) groups recorded failure entries (including collection errors) by their pytest error line: missing name, missing module, wrong call signature, missing dependency, wrong import path, wrong result, other. These counts decide what the next version changes.

## Iterations

### v0 · Baseline

A minimal prompt: *"Translate the complete Java repository in `_java_reference/` to working Python code under `src/`. Preserve observable behavior."* One attempt.

**Failure analysis** (173 recorded failure entries over 3 runs; these include collection errors and do not count every blocked test):

- **80% are interface mismatches, not wrong logic:** 101 missing names, 20 missing modules and 18 wrong call signatures. The agent translated `randomNanoId` as `random_nano_id` and `getRandom` as `get_random`, changed signatures and invented module paths. Python's own hint shows the pattern: *"has no attribute 'randomNanoId'. Did you mean: 'random_nano_id'?"*
- **15 missing dependencies:** the agent used third-party packages (`exrex`, `greenery`) that aren't installed.
- **The turn limit was hit in 9 of 15 projects.** The agent spent turns on a README, a QUICKSTART, examples, its own tests and a `run_tests.sh`. None of these count, and without a shell it can't even run them.
- Only 14 tests (8%) failed with a wrong result.

### v1 · Interface rules

**Change:** the v0 system prompt plus five rules, each aimed at one counted category ([`prompts/v1_system.txt`](prompts/v1_system.txt)). The task message stays identical.

v1, v2 and v3 all start with this same first attempt, so there are 9 runs of it. The right-hand column uses all 9 (45 project runs), compared with v0's 15 project runs:

| Rule | Targets | v0 (15 project runs) | v1 setup (45 project runs) |
|---|---|---|---|
| Create exactly the modules listed in `project_structure.txt` | missing module | 20 failure records | **0** |
| Use the Python standard library only | missing dependency | 15 failure records | **0** |
| Write only implementation `.py` files under `src/` | turn limit | 9 of 15 (60%) | **4 of 45 (9%)** |
| Keep parameter order; merge overloads into one method | wrong call signature | 18 failure records | 7 |
| Keep Java names exactly (`getValue`, not `get_value`); public stays public | missing name | 101 failure records | 177 (≈ 59 per 15) |

**Result:**
- **Missing-module and missing-dependency records disappear in the sampled first attempts,** and cost per run fell 43%.
- **The overall score is noisy.** Tests passed rose from 23% to 42% in the 3 v1 runs. Across all 9 first attempts of the v1 setup it is 33% ± 23, with single runs anywhere between 0% and 69%. The observed difference does not establish a reliable overall gain with this sample.

**Failure analysis:**

- **The naming rule backfired on editorconfig.** It scored 0% in all v1 runs, against 83% once in v0. Its tests expect Python-style names (`config_value_for_key`), while jnanoid's and xeger's tests expect Java names. The benchmark's naming is inconsistent, even within a single project, so no fixed rule can be right.
- **New interface errors:**
  - The agent's modules imported each other without the `src.` prefix (*wrong import path*).
  - Exceptions differed from what the tests expect (*other exception*), e.g. jnanoid raising its own `IllegalArgumentError` class.
- **Unknowable test expectations:** fit-chart's tests import `FitChartValue`, `AnimationMode` and `Renderer` from `src.fitchart.fake_classes`, and no file the agent sees reveals that mapping.

What remains are details of the expected interface that a prompt can't know. The tests reveal them.

### v2 · More attempts, no feedback (control)

**Change:** v1, run 5 times in a row on the same workspace, with the same task message each time and no test results. Each attempt is a fresh session that sees the previous attempt's files. The loop never stops early on test results, because that alone would leak information. This version exists to separate "more attempts" from "feedback" in v3.

**Result:** 13% tests passed, 0.3 of 5 projects fully migrated, at almost three times v3's cost.
- **Repeated attempts produced little improvement.** In only 1 of 60 attempt-to-attempt steps did the score change (jnanoid run 3: 0 → 22 tests). Within the same run, v2 went from 7% on the first attempt to 13% at the end; v3 went from 52% to 88%.
- **Why v2 is even lower than v1:** v2 started with unlucky first attempts (0%, 20% and 0%; same setup as v1, same code path), and the extra attempts couldn't recover them. Most suites failed to load at all, through a wrong import path in fit-chart and editorconfig or a missing `FailedRandomWalkException` in xeger.
- **Attempts are not idle:** they cost about as much as a v1 attempt ($0.19 vs $0.16) and sometimes rewrite code. In editorconfig, attempts 3–5 turned a suite that ran (0 of 12 passing) into one that no longer loads. Without a signal of what is wrong, the agent can't tell whether its rewrites help.

This control supports the usefulness of the public-test repair strategy over repeated task prompts. It does not isolate test feedback alone: v3 also changes the repair instructions and stopping rule, and the variants started from different, unpaired first attempts.

### v3 · Repair loop with public-test feedback

**Change:** v1, then up to 4 repairs. After each attempt the harness runs **only `public_tests/`** (52 tests) and sends that pytest output to the agent ([`prompts/v3_repair.txt`](prompts/v3_repair.txt)).
- **Hidden tests stay hidden:** the 58 tests in `tests/` never appear in feedback.
- **Early stop:** the loop stops as soon as all public tests pass, so a further repair request can't hint that hidden tests still fail.
- **Scoring:** the score always counts all 110 tests.

**Result:** 88% tests passed (runs: 99 / 98 / 66%), 8 of 15 project runs fully migrated, with 289 of 330 tests credited as passes. The remaining 41 comprise 6 failed tests in valid suites and 35 tests receiving zero credit in two invalid suites. There are 12 diagnostic failure records: 6 test failures and 6 collection errors.

**Do the repairs carry over to tests the agent never saw?** Yes:

| | First attempt (= v1 setup) | After repairs |
|---|---|---|
| Public tests (feedback source), 52 × 3 | 82 (53%) | 140 (90%) |
| **Hidden tests (never seen)**, 58 × 3 | 88 (51%) | **149 (86%)** |

The hidden tests improve almost as much as the public ones (+35 vs. +37 points), supporting generalization within these five projects. This does not establish performance on unseen repositories.

**Remaining failures:**

- **jnanoid fails the same hidden test in all 3 runs.** It calls `is_url_friendly` (Python style), while the public tests only use Java names (`randomNanoId`, `DEFAULT_ALPHABET`), so no public feedback touches it. This exposes a mismatch between the naming instruction and the benchmark interface, which public feedback did not resolve.
- **xeger's hidden tests expect exactly the sequences of Java's seeded `java.util.Random`.** This is real behavioural equivalence and the hardest kind of error.
- **Repairs can regress.** In fit-chart run 3, the last repair introduced a circular import, and a suite at 37.5% became one that no longer loads. The harness keeps the last attempt, not the best.
- **Public tests are an incomplete signal.** In BluetoothLeGatt run 3, all public tests passed at once, so the loop stopped. But one hidden test file imports a module the agent wired up wrongly, and pytest aborts the whole suite on a collection error (0 of 19 counted). Together with the fit-chart regression, this explains the weak run 3 (66%).

## Test accounting

Counts across three runs per variant (330 official tests). Failure records are diagnostic entries, including collection errors; they are not an additional category of tests.

| Variant | Passed | Failed in valid suites | Tests in invalid suites | Diagnostic failure records |
|---|---:|---:|---:|---:|
| v0 | 77 | 127 | 126 | 173 |
| v1 | 137 | 77 | 116 | 110 |
| v2 | 44 | 33 | 253 | 80 |
| v3 | 289 | 6 | 35 | 12 |

## Attribution

These are associations with the instruction bundle and attempt strategies. The five v1 rules were not ablated individually, so their separate causal effects are not established.

| Change | Targeted failure (measured before) | Measured effect |
|---|---|---|
| v1: layout rule | 20 missing modules | → 0 (in 45 project runs) |
| v1: standard library only | 15 missing dependencies | → 0 |
| v1: scope rule (implementation files only) | turn limit in 60% of projects | → 9%; cost −43% |
| v1: signature rule | 18 wrong signatures | → 7 in 45 project runs |
| v1: Java naming rule | 101 missing names | fixes jnanoid/xeger, **breaks editorconfig** (0% in all v1 runs) |
| v2: more attempts, no feedback | (control) | limited improvement: 7% → 13%; 1 of 60 attempt steps changed the score |
| v3: public-test feedback | interface details unknowable from the sources | first → final attempt 52% → 88%; hidden tests 51% → 86% |

## Limitations

- **Small sample:** 5 projects, 3 runs per version and one model (Haiku 4.5). Identical first attempts range from 0% to 69%, so differences in the *total* score between versions need large effects. The v3 first-to-final gain is measured within runs, but three runs still provide limited evidence about its stability. The v0 → v1 gain is not conclusive; category changes are descriptive evidence for the bundle.
- **Tuned on the benchmark:** the iterations were designed by looking at failures on the same 5 projects. The prompts contain no project-specific hints, but there's no held-out project set.
- **Heuristic failure categories:** `failures.py` uses rules on pytest's error lines. One mislabel (an internal import counted as a missing dependency) was found and fixed during the analysis. The categories haven't been checked systematically against a manual sample.
- **Harness revisions:** the v0 runs were produced before the per-folder pass counts were added to the report, so v0 and v1 have no public/hidden split. The agent setup for v0 is unchanged.

## Next steps

The remaining v3 failures suggest three small changes:
1. **Keep the best attempt**, selected by public tests only, instead of the last one. This addresses the fit-chart regression.
2. **Deterministic backpressure:** before accepting "done", import every module under `src/` and send import errors back to the agent. This needs no test information and addresses the BluetoothLeGatt collection error, and the many suites that don't load at all in v1 and v2.
3. **Resume the session between repairs** instead of starting fresh. This saves the re-orientation turns, but the agent may anchor on its earlier mistakes. It would be measured as its own version against v3.

## Reproduce

Requirements: Docker, network access during the build, and an Anthropic API key in `.env` (`ANTHROPIC_API_KEY=...`). The key is passed only to the container at run time and is excluded from Git and the image.

```sh
chmod 600 .env
docker build -t repotransbench-migration:experiment-v1 .
mkdir -p results
docker run --rm --init --read-only --cap-drop=ALL \
  --security-opt=no-new-privileges --pids-limit=128 --memory=3g --cpus=4 \
  --tmpfs /scratch:rw,size=1g,mode=1777 --tmpfs /tmp:rw,size=256m,mode=1777 \
  --user "$(id -u):$(id -g)" --env HOME=/scratch --env-file .env \
  --mount "type=bind,src=$(pwd)/results,dst=/results" \
  repotransbench-migration:experiment-v1
```

This runs the full experiment: 3 new runs each of v0–v3 (about $26, several hours). Then it rebuilds `results/summary.json` and `results/report.html` from **all** stored runs. New runs are added as `results/runs/<version>/run-<k>/report.json` and never overwrite earlier ones. Results vary between runs, so compare averages.

To run something narrower, append a command to the `docker run` line above. For example, one v3 run:

```sh
  ... repotransbench-migration:experiment-v1 \
  python /app/run.py --dataset /dataset --work-dir /scratch/work --results-dir /results --variants v3 --repeats 1
```

From the stored reports, without API calls or Docker:

```sh
python3 run.py --dataset . --repeats 0                    # rebuild summary.json and report.html
python3 failures.py results/runs/v1/run-*/report.json     # failure categories per project and in total
```

## Files

| Path | Content |
|---|---|
| `repotransbench.py` | Harness: project setup, agent call, sandbox hook, official evaluation, repair loop, `VARIANTS` |
| `run.py` | Runs versions repeatedly; builds `summary.json` and `report.html` |
| `failures.py` | Diagnostic failure categories from the stored reports |
| `prompts/` | System, task and repair prompts per version |
| `results/runs/` | Raw report of every run (scores, failures, attempts, tokens, cost) |
| `results/environment.txt` | Package versions of the measured runtime |
