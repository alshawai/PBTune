"""Unit tests for the BO co-tenant load controller.

These validate the controller's wiring — degree gating, background worker
construction, barrier party count, per-round apply semantics, and metadata —
with the environment and orchestrator mocked so no containers are launched.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.config.database import DatabaseConfig
from src.knobs import get_knob_space
from src.tuners.engine.barriers import GenerationBarrier
from src.scripts.bo_baseline.cotenant import CoTenantLoadController


def _make_controller(degree: int) -> CoTenantLoadController:
    knob_space = get_knob_space("minimal")
    env = MagicMock()
    env.get_db_config.side_effect = lambda wid: DatabaseConfig(
        host="127.0.0.1", port=5440 + wid, dbname="t", user="u", password="p"
    )
    orchestrator = MagicMock()
    base_db = DatabaseConfig(
        host="127.0.0.1", port=5440, dbname="t", user="u", password="p"
    )
    return CoTenantLoadController(
        degree=degree,
        env=env,
        orchestrator=orchestrator,
        knob_space=knob_space,
        base_db_config=base_db,
        seed=42,
    )


def test_disabled_when_degree_one():
    ctrl = _make_controller(degree=1)
    assert ctrl.enabled is False
    assert ctrl.make_barrier() is None
    # No background workers, start_round is a no-op returning no futures.
    assert ctrl.start_round(None) == []
    ctrl.shutdown()


def test_enabled_builds_degree_minus_one_background_workers():
    ctrl = _make_controller(degree=4)
    assert ctrl.enabled is True
    assert ctrl._bg_worker_ids == [1, 2, 3]
    assert len(ctrl._bg_workers) == 3
    # Each background worker is bound to its own port and carries a config.
    ports = {w.db_config.port for w in ctrl._bg_workers}
    assert ports == {5441, 5442, 5443}
    for w in ctrl._bg_workers:
        assert w.knob_config  # non-empty LHS config
        assert w.force_restart_next_eval is True
    ctrl.shutdown()


def test_barrier_parties_is_degree():
    ctrl = _make_controller(degree=8)
    barrier = ctrl.make_barrier()
    assert isinstance(barrier, GenerationBarrier)
    # foreground (1) + background (degree-1) == degree parties
    assert ctrl.barrier_parties == 8
    ctrl.shutdown()


def test_background_configs_are_deterministic_for_seed():
    a = _make_controller(degree=4)
    b = _make_controller(degree=4)
    cfgs_a = [w.knob_config for w in a._bg_workers]
    cfgs_b = [w.knob_config for w in b._bg_workers]
    assert cfgs_a == cfgs_b  # same seed → identical load configs
    a.shutdown()
    b.shutdown()


def test_start_round_always_applies_config():
    ctrl = _make_controller(degree=3)
    barrier = ctrl.make_barrier()

    # First round.
    futures1 = ctrl.start_round(barrier)
    ctrl.finish_round(futures1)
    # Second round.
    futures2 = ctrl.start_round(ctrl.make_barrier())
    ctrl.finish_round(futures2)

    calls = ctrl.orchestrator.evaluate_worker.call_args_list
    # 2 background workers × 2 rounds = 4 calls.
    assert len(calls) == 4
    apply_flags = [c.kwargs["apply_config"] for c in calls]
    # apply_config=True on EVERY round — required so background loaders
    # traverse the same B2/B5 barriers as the foreground (deadlock fix).
    assert apply_flags == [True, True, True, True]
    ctrl.shutdown()


def test_metadata_records_degree_and_worker_ids():
    ctrl = _make_controller(degree=4)
    meta = ctrl.to_metadata()
    assert meta["enabled"] is True
    assert meta["degree"] == 4
    assert meta["background_worker_ids"] == [1, 2, 3]
    assert meta["foreground_worker_id"] == 0
    assert meta["load_config_seed"] == 42
    ctrl.shutdown()
