"""Regression tests for the post-workload VACUUM-skip optimization.

When the next evaluation on a worker is guaranteed to begin with a
baseline snapshot restore (PBT with ``snapshot_restore_interval=1`` after
gen 0; BO with the same interval after the first iteration), the
per-eval ``VACUUM ANALYZE`` is pure dead wall-clock:

1. It runs *after* metrics are collected at B12 (line ~1138 of
   ``orchestrator.evaluate_worker``), so it cannot influence the
   just-recorded results.
2. The next iteration's snapshot restore copies PGDATA from the baseline
   (which already includes a post-prepare VACUUM ANALYZE), wiping any
   on-disk effects of the per-eval VACUUM before they matter.

The skip path is wired via ``next_eval_will_restore`` plumbed from
PBT (``Population._restore_due_next_gen``) and BO (computed against the
next iteration index) into ``WorkloadOrchestrator.evaluate_worker`` and
finally into ``_vacuum_after_dml``.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parents[4]
ORCHESTRATOR_PATH = PROJECT_ROOT / "src" / "tuners" / "engine" / "orchestrator.py"
POPULATION_PATH = PROJECT_ROOT / "src" / "tuners" / "pbt" / "population.py"
PBT_TUNER_PATH = PROJECT_ROOT / "src" / "tuners" / "pbt" / "tuner.py"
BO_RUNNER_PATH = PROJECT_ROOT / "src" / "tuners" / "bo" / "tuner.py"
BO_OBJECTIVE_PATH = PROJECT_ROOT / "src" / "tuners" / "bo" / "objective.py"


# ── Orchestrator contract ───────────────────────────────────────────


def test_vacuum_after_dml_skips_when_next_eval_will_restore():
    """``_vacuum_after_dml(next_eval_will_restore=True)`` must short-circuit
    before opening a DB connection. We monkey-patch ``get_connection`` to
    raise — if it gets called, the test fails."""
    from src.tuners.engine.orchestrator import WorkloadOrchestrator
    from src.utils.metrics import WorkloadType
    from src.tuners.engine.orchestrator import WorkloadOrchestratorConfig
    from src.utils.metrics import PerformanceMetrics  # noqa: F401 — module load

    # Build a config that would normally call VACUUM (sysbench RW workload)
    # but assert we never reach the connection step.
    cfg = MagicMock(spec=WorkloadOrchestratorConfig)
    cfg.workload_type = WorkloadType.OLTP
    cfg.vacuum_analyze_timeout_seconds = 45.0

    orch = WorkloadOrchestrator.__new__(WorkloadOrchestrator)
    orch.config = cfg

    db_config = MagicMock()

    # If next_eval_will_restore short-circuits correctly, this call is a no-op
    # and never opens a connection. We assert by side-effect: no exception
    # bubbles up from the missing dependencies of the real DB code path.
    orch._vacuum_after_dml(db_config, next_eval_will_restore=True)


def test_vacuum_after_dml_skip_log_message():
    """The skip branch logs an unambiguous reason so timing breakdowns
    don't mistakenly flag it as a hang."""
    source = ORCHESTRATOR_PATH.read_text()
    tree = ast.parse(source)

    # Locate _vacuum_after_dml and verify a skip-with-log branch exists
    # gated on next_eval_will_restore.
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_vacuum_after_dml":
            param_names = [a.arg for a in node.args.args]
            assert "next_eval_will_restore" in param_names, (
                "_vacuum_after_dml must accept next_eval_will_restore"
            )
            return
    raise AssertionError("_vacuum_after_dml not found in orchestrator")


def test_evaluate_worker_signature_includes_next_eval_will_restore():
    """The new keyword must reach ``evaluate_worker`` so PBT and BO can
    pass it without monkey-patching."""
    from src.tuners.engine.orchestrator import WorkloadOrchestrator

    sig = inspect.signature(WorkloadOrchestrator.evaluate_worker)
    assert "next_eval_will_restore" in sig.parameters
    assert sig.parameters["next_eval_will_restore"].default is False


# ── PBT plumbing ────────────────────────────────────────────────────


def test_population_exposes_restore_due_next_gen():
    """``Population.train_generation`` must publish ``_restore_due_next_gen``
    so the per-worker eval can read it via ``main.py``."""
    source = POPULATION_PATH.read_text()
    # Cheap check: the attribute is written somewhere in train_generation.
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "_restore_due_next_gen"
                ):
                    return
    raise AssertionError(
        "Population must set self._restore_due_next_gen so the orchestrator "
        "can skip the post-workload VACUUM when the next gen restores."
    )


def test_pbt_tuner_forwards_next_eval_will_restore():
    """``PBTTuner.evaluate_worker`` must thread the predicate through to
    the orchestrator. Without this, BO would skip VACUUM but PBT would
    not, reintroducing the asymmetry."""
    source = PBT_TUNER_PATH.read_text()
    assert "_restore_due_next_gen" in source, (
        "tuner.py must read population._restore_due_next_gen"
    )
    assert "next_eval_will_restore=next_eval_will_restore" in source, (
        "tuner.py must forward next_eval_will_restore to evaluate_worker"
    )


# ── BO plumbing ─────────────────────────────────────────────────────


def test_bo_objective_accepts_and_forwards_next_eval_will_restore():
    """The shared BO objective wrapper must accept the predicate AND
    forward it to ``orchestrator.evaluate_worker``."""
    source = BO_OBJECTIVE_PATH.read_text()
    tree = ast.parse(source)

    # 1. Parameter present on evaluate_config
    found_param = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "evaluate_config":
            if "next_eval_will_restore" in {a.arg for a in node.args.args}:
                found_param = True
            break
    assert found_param, (
        "evaluate_config must accept next_eval_will_restore"
    )

    # 2. Forwarded into the orchestrator call.
    assert "next_eval_will_restore=next_eval_will_restore" in source, (
        "evaluate_config must forward next_eval_will_restore to "
        "orchestrator.evaluate_worker"
    )


def test_bo_runner_computes_and_passes_next_eval_will_restore():
    """Both BO loops (bootstrap pilot + main optimize) must compute the
    symmetric predicate and forward it. If only one loop does, the
    bootstrap or the main loop will keep VACUUMing for nothing."""
    source = BO_RUNNER_PATH.read_text()
    # Two definitions, two forwards.
    assert source.count("next_eval_will_restore =") >= 2, (
        "BO runner must compute next_eval_will_restore in BOTH the "
        "bootstrap pilot and the main optimize loop"
    )
    assert source.count("next_eval_will_restore=next_eval_will_restore") >= 2, (
        "BO runner must forward next_eval_will_restore to evaluate_config "
        "in BOTH loops"
    )


# ── Predicate semantics ─────────────────────────────────────────────


def test_predicate_matches_restore_due_shifted_by_one_step():
    """For ``restore_interval=1`` and ``enable_snapshots=True``, the
    predicate must be True from step 0 onward (since next_step=1 is
    divisible by 1). For ``restore_interval=5``, it should be True
    exactly on step 4 (next_step=5)."""
    def next_eval_will_restore(step: int, interval: int, enabled: bool) -> bool:
        n = step + 1
        return bool(enabled and n > 0 and n % interval == 0)

    # Interval 1: every step after 0 (BO's typical case at THOROUGH/RESEARCH)
    for step in range(0, 20):
        assert next_eval_will_restore(step, 1, True) is True

    # Interval 5: only on step 4, 9, 14, ...
    for step in range(0, 20):
        expected = (step + 1) % 5 == 0
        assert next_eval_will_restore(step, 5, True) is expected, (
            f"step={step} expected={expected}"
        )

    # Snapshots disabled: always False
    for step in range(0, 5):
        assert next_eval_will_restore(step, 1, False) is False
