"""End-to-end equivalence: new ``PBTTuner(BaseTuner)`` vs legacy ``main.PBTTuner``.

This is the 2d behavioral-parity gate. Both tuners share the *same*
``Population``/evolution/orchestrator/scorer machinery; only the scaffolding
(setup, generation loop, session serialization) was rewritten onto ``BaseTuner``.
So if the three genuinely-transcribed pieces — the initial-config draw, the
``evaluate_worker`` failure ladder, and the Population wiring — are faithful,
both tuners must trace an *identical* optimization trajectory under the same
seed and the same deterministic scorer.

The test mocks only the DB-touching seams (environment, per-worker evaluation,
resource detection, runtime knob pruning, system-info) and uses a **real**
minimal knob space and a **real** ``Population``, so the config sampling and the
exploit/explore evolution run for real. A deterministic scorer maps each knob
config to a fixed score, making the whole run reproducible.

The two session JSONs are then reduced to their behavioral core (best config,
best score, per-generation trajectory, knob/pop/seed identity) — tolerating the
deliberate flat→nested schema change 2a′/2d introduced — and asserted equal.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple
from unittest.mock import MagicMock, patch


import pytest

from src.config.database import DatabaseConfig
from src.utils.hardware_info import WorkerResources
from src.utils.metrics import PerformanceMetrics, WorkloadType
from src.utils.timing import TimingRecorder


WORKLOAD_FILE = "workloads/oltp.json"
POP_SIZE = 4
NUM_GENERATIONS = 3
SEED = 42


# ---------------------------------------------------------------------------
# Deterministic evaluation harness
# ---------------------------------------------------------------------------
@dataclass
class _FakeInstance:
    """Stand-in for an environment InstanceConfig (only ``port`` is read)."""

    port: int


def _deterministic_score(knob_config: Dict[str, Any]) -> float:
    """Map a knob config to a stable score (pure function of its values).

    Deterministic so identical configs always score identically — the property
    that lets two independent tuner instances trace the same trajectory.
    """
    total = 0.0
    for key in sorted(knob_config):
        value = knob_config[key]
        if isinstance(value, bool):
            total += 1.0 if value else 0.0
        elif isinstance(value, (int, float)):
            total += float(value)
        else:
            total += float(len(str(value)))
    # Squash into a bounded, score-like range.
    return 10.0 + (total % 90.0)


def _fake_metrics(score: float) -> PerformanceMetrics:
    return PerformanceMetrics(
        latency_p50=100.0 - score,
        latency_p95=120.0 - score,
        latency_p99=150.0 - score,
        throughput=score * 10.0,
        memory_utilization=0.5,
        io_read_mb=1.0,
        io_write_mb=1.0,
        cache_hit_ratio=0.99,
        error_rate=0.0,
        total_queries=1000,
        total_time=30.0,
    )


def _make_fake_env(num_instances: int) -> MagicMock:
    """A MagicMock env exposing exactly the surface both tuners call."""
    env = MagicMock()
    env.setup_instances.return_value = [
        _FakeInstance(port=5440 + i) for i in range(num_instances)
    ]
    env.verify_instances.return_value = None
    env.get_db_config.side_effect = lambda wid: DatabaseConfig(
        host="127.0.0.1",
        port=5440 + wid,
        dbname="test",
        user="postgres",
        password="postgres",
    )
    env.pg_server_version = "16.0"
    env.docker_version = "24.0.0"
    env.get_resource_allocations.return_value = []
    env.recover_instance.return_value = True
    env.stop_all.return_value = None
    env.cleanup.return_value = None
    return env


class _DeterministicEval:
    """A stand-in for ``orchestrator.evaluate_worker`` with fixed scoring.

    Mirrors the real orchestrator contract: returns
    ``(metrics, score, restart_occurred, db_config, timing)`` and never mutates
    the worker (only the Population does that bookkeeping).
    """

    def __call__(
        self,
        worker: Any,
        *,
        apply_config: bool = True,
        generation: int = 0,
        barriers: Any = None,
        restore_due: bool = False,
        next_eval_will_restore: bool = False,
    ) -> Tuple[PerformanceMetrics, float, bool, Dict[str, Any], TimingRecorder]:
        # Drain barriers so lockstep evaluation doesn't deadlock the pool.
        if barriers is not None:
            for name in ("connected", "applied", "measured", "scored"):
                try:
                    barriers.wait(name, worker_id=worker.worker_id)
                except Exception:  # noqa: BLE001 - best-effort in the fake
                    pass
        score = _deterministic_score(worker.knob_config or {})
        metrics = _fake_metrics(score)
        timing = TimingRecorder()
        return metrics, score, False, {}, timing


def _canonical(results: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce a session dict to its schema-independent behavioral core.

    Tolerates the deliberate flat→nested envelope change: reads shared axes
    (num_rounds/total_evaluations/converged) and best-config fields from
    whichever location the strategy used, so the incumbent (flat) and the new
    (nested) sessions compare on *behavior*, not layout.
    """
    session = results.get("tuning_session", {})
    best = results.get("best_configuration", {})

    # Per-generation trajectory: (best_score, sorted worker scores) per gen.
    trajectory: List[Tuple[float, Tuple[float, ...]]] = []
    for gen in results.get("generation_history", []):
        best_score = round(float(gen.get("best_score", 0.0)), 6)
        worker_scores = tuple(
            sorted(
                round(float(ws["score"]), 6)
                for ws in gen.get("worker_scores", [])
                if ws.get("score") is not None
            )
        )
        trajectory.append((best_score, worker_scores))

    # Rounds axis: incumbent used total_generations; new uses num_rounds.
    rounds = session.get("num_rounds", session.get("total_generations"))

    return {
        "best_score": round(float(best.get("score", 0.0)), 6),
        "best_knobs": {
            k: round(float(v), 6) if isinstance(v, (int, float)) else v
            for k, v in sorted((best.get("knobs") or {}).items())
        },
        "num_knobs": session.get("num_knobs"),
        "population_size": _read_pop_size(session),
        "seed": session.get("seed"),
        "knob_tier": session.get("knob_tier"),
        "rounds": rounds,
        "trajectory": trajectory,
    }


def _read_pop_size(session: Dict[str, Any]) -> Any:
    """Population size lives flat in the incumbent, in strategy_params in new."""
    if "population_size" in session:
        return session["population_size"]
    return session.get("strategy_params", {}).get("population_size")



# ---------------------------------------------------------------------------
# Drivers
# ---------------------------------------------------------------------------
def _run_new_tuner(tmp_path: Path) -> Dict[str, Any]:
    from src.tuners.pbt.tuner import PBTTuner
    from src.tuners.pbt.config import STANDARD_CONFIG
    from src.tuners.utils.types import TunerLifecycleConfig, TuningStrategy
    from src.utils.types import clone_benchmark_config
    from dataclasses import replace

    pbt_config = replace(
        STANDARD_CONFIG,
        population_size=POP_SIZE,
        num_generations=NUM_GENERATIONS,
        num_parallel_workers=POP_SIZE,
        benchmark_config=clone_benchmark_config(STANDARD_CONFIG.benchmark_config),
        enable_snapshots=False,
    )
    lifecycle = TunerLifecycleConfig(
        strategy=TuningStrategy.PBT,
        knob_tier="minimal",
        num_parallel_workers=POP_SIZE,
        random_seed=SEED,
        enable_snapshots=False,
        use_docker=True,
    )
    tuner = PBTTuner(
        lifecycle,
        pbt_config=pbt_config,
        benchmark=None,
        benchmark_config=pbt_config.benchmark_config,
        timestamp="20260712_1200",
        output_root=tmp_path / "new",
        workload_file=WORKLOAD_FILE,
        data_root=tmp_path / "new_data",
    )

    fake_env = _make_fake_env(POP_SIZE)
    with _common_patches("src.tuners", fake_env, tuner):
        return tuner.run()


def _run_legacy_tuner(tmp_path: Path) -> Dict[str, Any]:
    from src.tuner.main import PBTTuner as LegacyPBTTuner
    from src.tuners.pbt.config import STANDARD_CONFIG
    from src.utils.types import clone_benchmark_config
    from dataclasses import replace

    pbt_config = replace(
        STANDARD_CONFIG,
        population_size=POP_SIZE,
        num_generations=NUM_GENERATIONS,
        num_parallel_workers=POP_SIZE,
        benchmark_config=clone_benchmark_config(STANDARD_CONFIG.benchmark_config),
        enable_snapshots=False,
    )

    fake_env = _make_fake_env(POP_SIZE)
    # The legacy tuner builds env in __init__ (via EnvironmentFactory.create),
    # so patch it around construction *and* run.
    with patch(
        "src.tuner.main.EnvironmentFactory.create", return_value=fake_env
    ), patch(
        "src.tuner.main.detect_worker_resources",
        return_value=WorkerResources(ram_bytes=4 << 30, cpu_cores=4, disk_type="SSD"),
    ), patch(
        "src.tuner.main.get_system_info", return_value={"system": "test"}
    ):
        tuner = LegacyPBTTuner(
            knob_tier="minimal",
            pbt_config=pbt_config,
            benchmark=None,
            workload_type=WorkloadType.OLTP,
            workload_file=WORKLOAD_FILE,
            random_seed=SEED,
            output_dir=str(tmp_path / "legacy"),
            timestamp="20260712_1200",
            data_root=tmp_path / "legacy_data",
            no_docker=False,
        )
        tuner.env = fake_env
        tuner.orchestrator.evaluate_worker = _DeterministicEval()
        tuner._prune_unsupported_runtime_knobs = MagicMock()
        return tuner.run()


def _common_patches(pkg: str, fake_env: MagicMock, tuner: Any):
    """Context manager stacking the patches the new tuner's run() needs."""
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(
        patch(
            "src.tuners.base.EnvironmentFactory.create", return_value=fake_env
        )
    )
    stack.enter_context(
        patch(
            "src.tuners.base.resolve_worker_resources",
            return_value=WorkerResources(
                ram_bytes=4 << 30, cpu_cores=4, disk_type="SSD"
            ),
        )
    )
    stack.enter_context(
        patch("src.tuners.base.get_system_info", return_value={"system": "test"})
    )
    stack.enter_context(
        patch.object(
            type(tuner), "_prune_unsupported_runtime_knobs", lambda self: None
        )
    )
    # Deterministic per-worker evaluation on the real orchestrator instance,
    # applied once setup() has built it. We patch WorkloadOrchestrator.evaluate_worker
    # at the class level so the instance the base builds picks it up.
    stack.enter_context(
        patch(
            "src.tuners.engine.orchestrator.WorkloadOrchestrator.evaluate_worker",
            _DeterministicEval(),
        )
    )
    return stack


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------
@pytest.mark.filterwarnings("ignore")
def test_new_pbt_tuner_matches_legacy_behaviour(tmp_path):
    legacy = _run_legacy_tuner(tmp_path)
    new = _run_new_tuner(tmp_path)

    legacy_core = _canonical(legacy)
    new_core = _canonical(new)

    # Identity fields.
    assert new_core["num_knobs"] == legacy_core["num_knobs"]
    assert new_core["population_size"] == legacy_core["population_size"] == POP_SIZE
    assert new_core["seed"] == legacy_core["seed"] == SEED
    assert new_core["knob_tier"] == legacy_core["knob_tier"] == "minimal"
    assert new_core["rounds"] == legacy_core["rounds"]

    # Behavioral core: same winner, same score, same trajectory.
    assert new_core["best_score"] == legacy_core["best_score"]
    assert new_core["best_knobs"] == legacy_core["best_knobs"]
    assert new_core["trajectory"] == legacy_core["trajectory"]
