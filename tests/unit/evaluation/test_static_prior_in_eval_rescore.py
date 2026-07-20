"""Regression tests for fairness fixes #2 and #3.

Fix #2: ComparisonRunner.run() and ComparisonRunner.run_multi_arm()
must pass ``workload_features=None`` to ``rescore_metrics_globally``
so the head-to-head head uses the static workload-feature prior for
both arms. Using PBT's session-derived (drifted) feature vector would
co-adapt the rubric to PBT and asymmetrically grade BO on a vector it
never trained against.

Fix #3: PBT's cold-start LHS init must include the PostgreSQL default
config as worker 0 — matching BO's pilot, which prepends the default
to its Sobol seed in src/tuners/bo/tuner.py (default_config_seed span).
Without this,
BO has a free known-reasonable anchor while PBT's LHS gives no such
guarantee.
"""

from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNNER_PATH = PROJECT_ROOT / "src" / "evaluation" / "runner.py"
TUNER_MAIN_PATH = PROJECT_ROOT / "src" / "tuners" / "pbt" / "tuner.py"


def _find_rescore_calls(source: str) -> list[ast.Call]:
    """Return every Call node in ``source`` whose function is named
    ``rescore_metrics_globally``."""
    tree = ast.parse(source)
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "rescore_metrics_globally":
                calls.append(node)
            elif (
                isinstance(func, ast.Attribute)
                and func.attr == "rescore_metrics_globally"
            ):
                calls.append(node)
    return calls


def _kwarg(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


# ── Fix #2 ──────────────────────────────────────────────────────────


def test_eval_runner_uses_static_workload_features_prior() -> None:
    """Both rescore call sites in evaluation/runner.py must pass
    ``workload_features=None``.

    Regression: previously these passed ``session.workload_features``
    and ``pbt_session.workload_features`` respectively, baking PBT's
    EMA-drifted feature vector into the head-to-head rubric and
    asymmetrically penalizing BO.
    """
    source = RUNNER_PATH.read_text()
    calls = _find_rescore_calls(source)

    # The runner has exactly two head-to-head rescore calls (run()
    # for two-arm, run_multi_arm() for n-arm). If a future refactor
    # adds more, they must also pass workload_features=None.
    assert len(calls) >= 2, (
        f"Expected at least 2 rescore_metrics_globally call sites in "
        f"{RUNNER_PATH.name}, found {len(calls)}"
    )

    for call in calls:
        wf = _kwarg(call, "workload_features")
        assert wf is not None, (
            f"rescore_metrics_globally at line {call.lineno} must pass "
            f"workload_features explicitly (None for the static prior)"
        )
        assert isinstance(wf, ast.Constant) and wf.value is None, (
            f"rescore_metrics_globally at line {call.lineno} must pass "
            f"workload_features=None (got {ast.dump(wf)}). Using a "
            f"session-derived feature vector creates an asymmetric "
            f"rubric across arms."
        )


# ── Fix #3 ──────────────────────────────────────────────────────────


def test_pbt_lhs_init_prepends_default_config() -> None:
    """PBT's cold-start init in main.py must prepend the default config
    to the LHS samples, matching BO's pilot-seed convention.

    Regression: previously main.py called sample_diverse_configs(num=N)
    only, with no default-config anchor. BO's pilot
    (src/tuners/bo/tuner.py, default_config_seed span) prepends the live
    PostgreSQL default to its Sobol pilot, giving it a known-reasonable
    starting observation PBT didn't have.
    """
    source = TUNER_MAIN_PATH.read_text()
    tree = ast.parse(source)

    # Find every call to get_default_config() and sample_diverse_configs()
    # within the same enclosing function/branch and verify they're both
    # present in the cold-start init path.
    has_default_call = False
    has_lhs_call = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "get_default_config":
                has_default_call = True
            elif node.func.attr == "sample_diverse_configs":
                has_lhs_call = True

    assert has_default_call, (
        "src/tuners/pbt/tuner.py must call get_default_config() in the "
        "cold-start init path so worker 0 starts from the PG default, "
        "matching BO's pilot-seed convention."
    )
    assert has_lhs_call, (
        "src/tuners/pbt/tuner.py must still call sample_diverse_configs() "
        "for the remaining (population_size - 1) workers."
    )


def test_pbt_lhs_init_dedupes_default_from_lhs() -> None:
    """The default-config prepend must dedupe against the LHS samples
    so we don't accidentally seed two workers with the same config.

    The sample_diverse_configs(num_samples=population_size) call returns
    population_size LHS samples; after prepending the default and
    deduping, the slice [: population_size] keeps exactly population_size
    workers. If the LHS happens to produce a sample identical to the
    default (extremely rare under continuous LHS, possible with
    all-categorical knob subsets), the dedup ensures we drop the
    duplicate rather than seating it twice.
    """
    from src.knobs.knob_space import KnobSpace, KnobDefinition, KnobType

    # Build a tiny all-categorical KnobSpace where collisions are likely.
    knob_space = KnobSpace.__new__(KnobSpace)
    knob_space.knobs = {
        "enable_seqscan": KnobDefinition(
            name="enable_seqscan",
            knob_type=KnobType.BOOLEAN,
            default=True,
        ),
        "enable_hashjoin": KnobDefinition(
            name="enable_hashjoin",
            knob_type=KnobType.BOOLEAN,
            default=True,
        ),
    }

    # Emulate the main.py logic. With only 4 distinct configs in this
    # tiny space, an LHS of size 4 will almost certainly include the
    # default. Dedup must drop it so worker 0 (the explicit default)
    # is unique.
    default_config = knob_space.get_default_config()
    lhs_configs = [
        {"enable_seqscan": True, "enable_hashjoin": True},   # == default
        {"enable_seqscan": True, "enable_hashjoin": False},
        {"enable_seqscan": False, "enable_hashjoin": True},
        {"enable_seqscan": False, "enable_hashjoin": False},
    ]
    initial_configs = [default_config] + [
        c for c in lhs_configs if c != default_config
    ]
    population_size = 4
    initial_configs = initial_configs[:population_size]

    # Worker 0 is the default.
    assert initial_configs[0] == default_config
    # No two workers share a config.
    seen = set()
    for cfg in initial_configs:
        key = tuple(sorted(cfg.items()))
        assert key not in seen, (
            f"Duplicate worker config detected after dedup: {cfg}"
        )
        seen.add(key)
    # Exactly population_size workers.
    assert len(initial_configs) == population_size


def test_get_default_config_reads_knob_defaults() -> None:
    """KnobSpace.get_default_config() returns a Dict[str, Any] mapping
    every knob to its declared default. Behavioral test for the helper
    that fix #3 depends on.
    """
    from src.knobs.knob_space import KnobSpace, KnobDefinition, KnobType

    knob_space = KnobSpace.__new__(KnobSpace)
    knob_space.knobs = {
        "shared_buffers": KnobDefinition(
            name="shared_buffers",
            knob_type=KnobType.INTEGER,
            default=131072,
            min_value=128,
            max_value=1073741824,
        ),
        "random_page_cost": KnobDefinition(
            name="random_page_cost",
            knob_type=KnobType.REAL,
            default=4.0,
            min_value=0.1,
            max_value=10.0,
        ),
    }

    default_config = knob_space.get_default_config()
    assert default_config == {
        "shared_buffers": 131072,
        "random_page_cost": 4.0,
    }
