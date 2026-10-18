# Repository Migration Harness · Java → Python

A small, reproducible harness that specializes a coding agent (Claude Agent SDK, `claude-haiku-4-5-20251001`) for migrating whole Java repositories to Python. It is evaluated on five fixed [RepoTransBench v1.0](https://github.com/DeepSoftwareAnalytics/RepoTransBench/releases/tag/v1.0) projects with their official test suites (110 tests). Starting from a minimal baseline, the variants test an instruction bundle, repeated attempts, and public-test feedback. Every version was run 6 times.

| Version | Change | Tests passed (mean ± SD) | Projects fully migrated | Cost per run |
|---|---|---|---|---|
| **v0** | Minimal prompt, one attempt | 22.6% ± 7.5 | 0.3 of 5 | $1.49 |
| **v1** | + interface rules in the system prompt | 32.7% ± 14.3 | 0.3 of 5 | $0.84 |
| **v2** | v1 with 5 attempts, no feedback (control) | 20.4% ± 18.2 | 0.7 of 5 | $4.37 |
| **v3** | v1 + up to 4 repairs with **public**-test feedback | **84.1% ± 16.6** | **2.3 of 5** | $2.28 |

- **Tests passed:** share of the 110 official tests that pass, averaged over 6 runs. All tests in an invalid suite receive zero credit. SD is the sample standard deviation over the six runs.
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
- **6 runs each:** single runs of an identical setup differ by far more than 10 points.
- **Failure categories:** [`failures.py`](failures.py) groups recorded failure entries (including collection errors) by their pytest error line: missing name, missing module, wrong call signature, missing dependency, wrong import path, wrong result, other. These counts decide what the next version changes.

## Iterations

### v0 · Baseline

A minimal prompt: *"Translate the complete Java repository in `_java_reference/` to working Python code under `src/`. Preserve observable behavior."* One attempt.

**Failure analysis** (373 recorded failure entries over 6 runs; these include collection errors and do not count every blocked test):

- **About 75% are interface mismatches, not wrong logic:** 202 missing names, 30 missing modules and 45 wrong call signatures. The agent translated `randomNanoId` as `random_nano_id` and `getRandom` as `get_random`, changed signatures and invented module paths. Python's own hint shows the pattern: *"has no attribute 'randomNanoId'. Did you mean: 'random_nano_id'?"*
- **22 missing dependencies:** the agent used third-party packages (`exrex`, `greenery`) that aren't installed.
- **The turn limit was hit in 18 of 30 project runs.** The agent spent turns on a README, a QUICKSTART, examples, its own tests and a `run_tests.sh`. None of these count, and without a shell it can't even run them.
- Only 51 failure records (14%) are wrong results.

### v1 · Interface rules

**Change:** the v0 system prompt plus five rules, each aimed at one counted category ([`prompts/v1_system.txt`](prompts/v1_system.txt)). The task message stays identical.

v1, v2 and v3 all start with this same first attempt, so there are 18 runs of it. The right-hand column uses all 18 (90 project runs), compared with v0's 30 project runs:

| Rule | Targets | v0 (30 project runs) | v1 setup (90 project runs) |
|---|---|---|---|
| Create exactly the modules listed in `project_structure.txt` | missing module | 30 failure records | **0** |
| Use the Python standard library only | missing dependency | 22 failure records | **0** |
| Write only implementation `.py` files under `src/` | turn limit | 18 of 30 (60%) | **6 of 90 (7%)** |
| Keep parameter order; merge overloads into one method | wrong call signature | 45 failure records | 31 (≈ 10 per 30) |
| Keep Java names exactly (`getValue`, not `get_value`); public stays public | missing name | 202 failure records | 376 (≈ 125 per 30) |

**Result:**
- **Missing-module and missing-dependency records disappear in the sampled first attempts,** and cost per run fell 44%.
- **The overall score is noisy.** Tests passed rose from 23% to 33% in the 6 v1 runs. Across all 18 first attempts of the v1 setup it is 31% ± 20, with single runs anywhere between 0% and 69%. The observed difference does not establish a reliable overall gain with this sample.

**Failure analysis:**

- **The naming rule backfired on editorconfig.** It scored 0% in all 6 v1 runs, against 83% in 3 of 6 v0 runs. Its tests expect Python-style names (`config_value_for_key`), while jnanoid's and xeger's tests expect Java names. The benchmark's naming is inconsistent, even within a single project, so no fixed rule can be right.
- **New interface errors:**
  - The agent's modules imported each other without the `src.` prefix (*wrong import path*).
  - Exceptions differed from what the tests expect (*other exception*), e.g. jnanoid raising its own `IllegalArgumentError` class.
- **Unknowable test expectations:** fit-chart's tests import `FitChartValue`, `AnimationMode` and `Renderer` from `src.fitchart.fake_classes`, and no file the agent sees reveals that mapping.

What remains are details of the expected interface that a prompt can't know. The tests reveal them.

### v2 · More attempts, no feedback (control)

**Change:** v1, run 5 times in a row on the same workspace, with the same task message each time and no test results. Each attempt is a fresh session that sees the previous attempt's files. The loop never stops early on test results, because that alone would leak information. This version exists to separate "more attempts" from "feedback" in v3.

**Result:** 20% tests passed, 0.7 of 5 projects fully migrated, at almost twice v3's cost.
- **Repeated attempts produced little improvement.** In only 2 of 120 attempt-to-attempt steps did the score change (jnanoid: 0 → 22 and 0 → 3 tests). Within the same runs, v2 went from 17% on the first attempt to 20% at the end; v3 went from 44% to 84%.
- **Why v2 is lower than v1:** v2's first attempts (0, 20, 0, 17, 9 and 54%; same setup and code path as v1) were weaker on average, and the extra attempts couldn't recover them. Most suites failed to load at all, through a wrong import path in fit-chart and editorconfig or a missing `FailedRandomWalkException` in xeger.
- **Attempts are not idle:** they cost about as much as a v1 attempt ($0.18 vs $0.17) and sometimes rewrite code. In editorconfig, attempts 3–5 turned a suite that ran (0 of 12 passing) into one that no longer loads. Without a signal of what is wrong, the agent can't tell whether its rewrites help.

This control supports the usefulness of the public-test repair strategy over repeated task prompts. It does not isolate test feedback alone: v3 also changes the repair instructions and stopping rule, and the variants started from different, unpaired first attempts.

### v3 · Repair loop with public-test feedback

**Change:** v1, then up to 4 repairs. After each attempt the harness runs **only `public_tests/`** (52 tests) and sends that pytest output to the agent ([`prompts/v3_repair.txt`](prompts/v3_repair.txt)).
- **Hidden tests stay hidden:** the 58 tests in `tests/` never appear in feedback.
- **Early stop:** the loop stops as soon as all public tests pass, so a further repair request can't hint that hidden tests still fail.
- **Scoring:** the score always counts all 110 tests.

**Result:** 84% tests passed (runs: 99 / 98 / 65 / 76 / 66 / 99%), 14 of 30 project runs fully migrated, with 555 of 660 tests credited as passes. The remaining 105 comprise 32 failed tests in valid suites and 73 tests receiving zero credit in four invalid suites. Of the 40 diagnostic failure records, the largest group is now wrong results (22): once the interface is right, what remains is mostly logic.

**Do the repairs carry over to tests the agent never saw?** Yes:

| | First attempt (= v1 setup) | After repairs |
|---|---|---|
| Public tests (feedback source), 52 × 6 | 136 (44%) | 269 (86%) |
| **Hidden tests (never seen)**, 58 × 6 | 152 (44%) | **286 (82%)** |

The hidden tests improve almost as much as the public ones (+38 vs. +42 points), supporting generalization within these five projects. This does not establish performance on unseen repositories.

**Remaining failures:**

- **jnanoid fails the same hidden test in all 6 runs.** It calls `is_url_friendly` (Python style), while the public tests only use Java names (`randomNanoId`, `DEFAULT_ALPHABET`), so no public feedback touches it. This exposes a mismatch between the naming instruction and the benchmark interface, which public feedback did not resolve.
- **xeger's hidden tests expect exactly the sequences of Java's seeded `java.util.Random`.** This is real behavioural equivalence and the hardest kind of error. In one run, even public tests still failed after all 4 repairs (generated strings too short).
- **The repair budget can run out.** In two runs, editorconfig's `config_value_for_key` kept returning `''`; the public tests showed it, but 4 repairs did not fix it (67%).
- **Repairs can regress.** In fit-chart run 3, the last repair introduced a circular import, and a suite at 37.5% became one that no longer loads. The harness keeps the last attempt, not the best.
- **Public tests are an incomplete signal.** In 3 of 6 BluetoothLeGatt runs, all public tests passed, so the loop stopped. But a hidden test file imports something the agent wired up wrongly (a module path once, the class `DeviceScanActivity` twice), and pytest aborts the whole suite on a collection error (0 of 19 counted). This, together with the fit-chart regression and the editorconfig/xeger logic errors, explains the weaker runs (65–76%).

## Test accounting

Counts across six runs per variant (660 official tests). Failure records are diagnostic entries, including collection errors; they are not an additional category of tests.

| Variant | Passed | Failed in valid suites | Tests in invalid suites | Diagnostic failure records |
|---|---:|---:|---:|---:|
| v0 | 149 | 259 | 252 | 373 |
| v1 | 216 | 139 | 305 | 211 |
| v2 | 135 | 90 | 435 | 180 |
| v3 | 555 | 32 | 73 | 40 |

## Attribution

These are associations with the instruction bundle and attempt strategies. The five v1 rules were not ablated individually, so their separate causal effects are not established.

| Change | Targeted failure (measured before) | Measured effect |
|---|---|---|
| v1: layout rule | 30 missing modules | → 0 (in 90 project runs) |
| v1: standard library only | 22 missing dependencies | → 0 |
| v1: scope rule (implementation files only) | turn limit in 60% of projects | → 7%; cost −44% |
| v1: signature rule | 45 wrong signatures (30 project runs) | → 31 in 90 project runs |
| v1: Java naming rule | 202 missing names | fixes jnanoid/xeger, **breaks editorconfig** (0% in all v1 runs) |
| v2: more attempts, no feedback | (control) | limited improvement: 17% → 20%; 2 of 120 attempt steps changed the score |
| v3: public-test feedback | interface details unknowable from the sources | first → final attempt 44% → 84%; hidden tests 44% → 82% |

## Limitations

- **Small sample:** 5 projects, 6 runs per version and one model (Haiku 4.5). Identical first attempts range from 0% to 69%, so differences in the *total* score between versions need large effects. The v3 first-to-final gain is measured within runs, but six runs still provide limited evidence about its stability. The v0 → v1 gain is not conclusive; category changes are descriptive evidence for the bundle.
- **Tuned on the benchmark:** the iterations were designed by looking at failures on the same 5 projects. The prompts contain no project-specific hints, but there's no held-out project set.
- **Heuristic failure categories:** `failures.py` uses rules on pytest's error lines. One mislabel (an internal import counted as a missing dependency) was found and fixed during the analysis. The categories haven't been checked systematically against a manual sample.
- **Harness revisions:** v0 and v1 runs 1–3 were produced before the per-folder pass counts were added to the report, so the report shows no public/hidden split for them. The agent setup is unchanged.

## Next steps

The remaining v3 failures suggest three small changes:
1. **Keep the best attempt**, selected by public tests only, instead of the last one. This addresses the fit-chart regression.
2. **Deterministic backpressure:** before accepting "done", import every module under `src/` and send import errors back to the agent. This needs no test information and addresses wrong import paths such as the one in BluetoothLeGatt run 3, the fit-chart circular import, and the many suites that don't load at all in v1 and v2.
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

This runs the full experiment: 3 new runs each of v0–v3 (about $28, several hours). Then it rebuilds `results/summary.json` and `results/report.html` from **all** stored runs. New runs are added as `results/runs/<version>/run-<k>/report.json` and never overwrite earlier ones. Results vary between runs, so compare averages.

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
