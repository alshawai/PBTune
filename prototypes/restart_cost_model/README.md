# RestartCostModel — Archived

This class was extracted from `src/utils/restart_manager.py` during the
tuning-mode refactoring.

## Why archived?

The `RestartCostModel` applied a penalty factor to evaluation scores
based on whether a restart occurred. This created coupling between
the scoring function and the restart mechanism, and the penalty was
a heuristic approximation rather than an empirical measurement.

The new architecture replaces this with an explicit `RestartPolicy`
(`src/tuner/benchmark/restart_policy.py`) that makes restart decisions
based on the `TuningMode` (ONLINE / OFFLINE / ADAPTIVE), leaving the
scoring function clean and unbiased.
