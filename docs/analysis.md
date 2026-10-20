# Detailed analysis

Failure analysis and attribution for the four harness versions. Setup, results overview and how to run it are in the [README](../README.md). All numbers are over 6 runs per version and come from [`results/summary.json`](../results/summary.json) and the stored run reports; the full tables are in [`results/report.html`](../results/report.html).

Failures are counted with [`failures.py`](../failures.py), which groups every recorded failure entry of the final attempt (including collection errors) by its pytest error line: missing name, missing module, wrong call signature, missing dependency, wrong import path, wrong result, other. These counts decided what the next version changes.

## Iterations

### v0 · Baseline

A minimal prompt: *"Translate the complete Java repository in `_java_reference/` to working Python code under `src/`. Preserve observable behavior."* One attempt.

**Failure analysis** (373 recorded failure entries over 6 runs; these include collection errors and do not count every blocked test):

- **About 75% are interface mismatches, not wrong logic:** 202 missing names, 30 missing modules and 45 wrong call signatures. The agent translated `randomNanoId` as `random_nano_id` and `getRandom` as `get_random`, changed signatures and invented module paths. Python's own hint shows the pattern: *"has no attribute 'randomNanoId'. Did you mean: 'random_nano_id'?"*
- **22 missing dependencies:** the agent used third-party packages (`exrex`, `greenery`) that aren't installed.
- **The turn limit was hit in 18 of 30 project runs.** The agent spent turns on a README, a QUICKSTART, examples, its own tests and a `run_tests.sh`. None of these count, and without a shell it can't even run them.
- Only 51 failure records (14%) are wrong results.

### v1 · Interface rules

**Change:** the v0 system prompt plus five rules, each aimed at one counted category ([`prompts/v1_system.txt`](../prompts/v1_system.txt)). The task message stays identical.

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

**Change:** v1, then up to 4 repairs. After each attempt the harness runs **only `public_tests/`** (52 tests) and sends that pytest output to the agent ([`prompts/v3_repair.txt`](../prompts/v3_repair.txt)).
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
