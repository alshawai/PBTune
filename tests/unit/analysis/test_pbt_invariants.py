"""Unit tests for the PBT trace-invariant library.

The invariants in :mod:`src.analysis.pbt_invariants` are the instrument the rest
of this epic measures with, so each one is exercised against a synthetic trace
in *both* directions: a run that satisfies the property and a run that breaks
it. A check that always returned "violated" would make the characterization
suite in ``test_trace_regression.py`` pass vacuously, so proving each check can
say HOLDS is as important as proving it can say VIOLATED.

Traces here are hand-built minimal payloads, not recordings. They exist to pin
the detection logic, not to describe any real run.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import pytest

from src.analysis.pbt_invariants import (
    SessionTrace,
    check_all,
    check_donor_diversity,
    check_exploit_cadence,
    check_exploit_recovery,
    check_normalizer_support,
    check_perturbation_locality,
    check_readback_fidelity,
    check_score_metric_coupling,
    check_score_rank_agreement,
    check_search_efficiency,
    violations,
)

# ---------------------------------------------------------------- builders


def _metrics(throughput: float = 1000.0, **overrides: float) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "throughput": throughput,
        "latency_p95": 8.0,
        "error_rate": 0.0,
        "failure_type": None,
    }
    base.update(overrides)
    return base


def _generation(
    index: int,
    *,
    scores: Dict[int, float],
    throughputs: Optional[Dict[int, float]] = None,
    configs: Optional[Dict[int, Dict[str, Any]]] = None,
    actual_configs: Optional[Dict[int, Dict[str, Any]]] = None,
    exploitations: Sequence[Dict[str, int]] = (),
    mean_score: Optional[float] = None,
) -> Dict[str, Any]:
    """Build one generation record with the fields the invariants read."""
    throughputs = throughputs or {w: 1000.0 for w in scores}
    worker_scores: List[Dict[str, Any]] = []
    for worker_id, score in scores.items():
        entry: Dict[str, Any] = {
            "worker_id": worker_id,
            "score": score,
            "metrics": _metrics(throughputs[worker_id]),
        }
        if actual_configs and worker_id in actual_configs:
            entry["actual_config"] = actual_configs[worker_id]
        worker_scores.append(entry)
    worker_configs = [
        {"worker_id": worker_id, "config": config}
        for worker_id, config in (configs or {}).items()
    ]
    values = list(scores.values())
    return {
        "generation": index,
        "best_score": max(values),
        "mean_score": mean_score if mean_score is not None else sum(values)
        / len(values),
        "std_score": 0.0,
        "num_exploited": len(exploitations),
        "worker_scores": worker_scores,
        "worker_configs": worker_configs,
        "exploitations": list(exploitations),
    }


def _trace(
    history: Sequence[Dict[str, Any]],
    *,
    ready_interval: int = 3,
    ranges: Optional[Dict[str, Dict[str, float]]] = None,
) -> SessionTrace:
    return SessionTrace.from_dict(
        {
            "tuning_session": {
                "strategy_params": {
                    "ready_interval": ready_interval,
                    "exploit_quantile": 0.2,
                },
                "scoring": {"normalization_metadata": {"ranges": ranges or {}}},
                "num_parallel_workers": 2,
            },
            "history": list(history),
        }
    )


# ------------------------------------------------------------ exploit cadence


def test_cadence_holds_when_gaps_meet_the_ready_interval() -> None:
    """Exploiting every third generation satisfies a ready_interval of 3."""
    history = [
        _generation(
            g,
            scores={0: 10.0, 1: 20.0},
            exploitations=[{"elite_worker_id": 1, "poor_worker_id": 0}]
            if g in (0, 3, 6)
            else [],
        )
        for g in range(7)
    ]
    finding = check_exploit_cadence(_trace(history, ready_interval=3))
    assert finding.holds
    assert finding.observed["gaps"] == [3, 3]


def test_cadence_violated_when_exploiting_every_generation() -> None:
    """Back-to-back exploitation means the cooldown never re-arms."""
    history = [
        _generation(
            g,
            scores={0: 10.0, 1: 20.0},
            exploitations=[{"elite_worker_id": 1, "poor_worker_id": 0}],
        )
        for g in range(5)
    ]
    finding = check_exploit_cadence(_trace(history, ready_interval=3))
    assert not finding.holds
    assert finding.observed["min_gap"] == 1
    assert finding.observed["short_gaps"] == 4
    assert "B1" in finding.bug_ids


def test_cadence_is_undecidable_with_a_single_exploit_event() -> None:
    """One event yields no gap, so the invariant abstains rather than passing."""
    history = [
        _generation(
            0,
            scores={0: 10.0, 1: 20.0},
            exploitations=[{"elite_worker_id": 1, "poor_worker_id": 0}],
        ),
        _generation(1, scores={0: 10.0, 1: 20.0}),
    ]
    finding = check_exploit_cadence(_trace(history))
    assert finding.holds
    assert finding.observed["gaps"] == []


# ----------------------------------------------------------- donor diversity


def test_donor_diversity_holds_when_donors_rotate() -> None:
    """Two different elites donating is enough to clear the invariant."""
    history = [
        _generation(
            0,
            scores={0: 10.0, 1: 20.0, 2: 30.0},
            exploitations=[{"elite_worker_id": 1, "poor_worker_id": 0}],
        ),
        _generation(
            1,
            scores={0: 10.0, 1: 20.0, 2: 30.0},
            exploitations=[{"elite_worker_id": 2, "poor_worker_id": 0}],
        ),
    ]
    finding = check_donor_diversity(_trace(history))
    assert finding.holds
    assert finding.observed["distinct_donors"] == [1, 2]


def test_donor_diversity_violated_when_one_worker_donates_every_time() -> None:
    """A single donor across the run is the monoculture signature."""
    history = [
        _generation(
            g,
            scores={0: 10.0, 1: 20.0, 2: 30.0},
            exploitations=[{"elite_worker_id": 2, "poor_worker_id": 0}],
        )
        for g in range(4)
    ]
    finding = check_donor_diversity(_trace(history))
    assert not finding.holds
    assert finding.observed["distinct_donors"] == [2]
    assert finding.observed["exploitations"] == 4


# ---------------------------------------------------------- exploit recovery


def test_recovery_holds_when_the_child_lands_near_its_donor() -> None:
    """A local perturbation leaves the child close to the donor's throughput."""
    history = [
        _generation(
            0,
            scores={0: 10.0, 1: 20.0},
            throughputs={0: 500.0, 1: 1000.0},
            exploitations=[{"elite_worker_id": 1, "poor_worker_id": 0}],
        ),
        _generation(1, scores={0: 19.0, 1: 20.0}, throughputs={0: 950.0, 1: 1000.0}),
    ]
    finding = check_exploit_recovery(_trace(history))
    assert finding.holds
    assert finding.observed["min_recovery"] == pytest.approx(0.95)


def test_recovery_violated_when_the_child_lands_far_below_its_donor() -> None:
    """Recovering half the donor's throughput means explore is not local."""
    history = [
        _generation(
            0,
            scores={0: 10.0, 1: 20.0},
            throughputs={0: 500.0, 1: 1000.0},
            exploitations=[{"elite_worker_id": 1, "poor_worker_id": 0}],
        ),
        _generation(1, scores={0: 5.0, 1: 20.0}, throughputs={0: 500.0, 1: 1000.0}),
    ]
    finding = check_exploit_recovery(_trace(history))
    assert not finding.holds
    assert finding.observed["median_recovery"] == pytest.approx(0.5)
    assert finding.observed["below_floor"] == 1


# ----------------------------------------------------- perturbation locality


def test_locality_holds_when_explore_moves_one_knob() -> None:
    """Changing a single dimension of four is a local step."""
    parent = {"a": 1, "b": 2, "c": 3, "d": 4}
    child = {"a": 1, "b": 2, "c": 3, "d": 5}
    history = [
        _generation(
            0,
            scores={0: 10.0, 1: 20.0},
            configs={0: {"a": 9, "b": 9, "c": 9, "d": 9}, 1: parent},
            exploitations=[{"elite_worker_id": 1, "poor_worker_id": 0}],
        ),
        _generation(1, scores={0: 15.0, 1: 20.0}, configs={0: child, 1: parent}),
    ]
    finding = check_perturbation_locality(_trace(history))
    assert finding.holds
    assert finding.observed["max_observed"] == pytest.approx(0.25)


def test_locality_violated_when_explore_moves_most_of_the_space() -> None:
    """Moving three knobs of four makes the child an independent draw."""
    parent = {"a": 1, "b": 2, "c": 3, "d": 4}
    child = {"a": 7, "b": 8, "c": 9, "d": 4}
    history = [
        _generation(
            0,
            scores={0: 10.0, 1: 20.0},
            configs={0: {"a": 9, "b": 9, "c": 9, "d": 9}, 1: parent},
            exploitations=[{"elite_worker_id": 1, "poor_worker_id": 0}],
        ),
        _generation(1, scores={0: 15.0, 1: 20.0}, configs={0: child, 1: parent}),
    ]
    finding = check_perturbation_locality(_trace(history))
    assert not finding.holds
    assert finding.observed["max_observed"] == pytest.approx(0.75)


# --------------------------------------------------- score / metric coupling


def test_coupling_holds_when_score_tracks_throughput() -> None:
    """Score and throughput moving together is the healthy case."""
    history = [
        _generation(0, scores={0: 50.0, 1: 50.0}, throughputs={0: 1000.0, 1: 1000.0}),
        _generation(1, scores={0: 60.0, 1: 60.0}, throughputs={0: 1200.0, 1: 1200.0}),
    ]
    finding = check_score_metric_coupling(_trace(history))
    assert finding.holds


def test_coupling_violated_when_the_ruler_is_rescaled_mid_run() -> None:
    """A 28-point score drop on flat throughput is a renormalization shock."""
    history = [
        _generation(0, scores={0: 84.0, 1: 84.0}, throughputs={0: 1000.0, 1: 1000.0}),
        _generation(1, scores={0: 56.0, 1: 56.0}, throughputs={0: 1010.0, 1: 1010.0}),
    ]
    finding = check_score_metric_coupling(_trace(history))
    assert not finding.holds
    shock = finding.observed["shocks"][0]
    assert shock["generation"] == 1
    assert shock["score_delta"] == pytest.approx(-28.0)
    assert shock["metric_delta"] == pytest.approx(0.01)


# --------------------------------------------------------- normalizer support


def test_support_holds_when_anchors_bracket_the_observations() -> None:
    """Anchors wider than the observed spread leave nothing clamped."""
    history = [
        _generation(g, scores={0: 10.0, 1: 20.0}, throughputs={0: 900.0, 1: 1100.0})
        for g in range(3)
    ]
    ranges = {"throughput": {"low": 500.0, "high": 1500.0, "direction": 1.0}}
    finding = check_normalizer_support(_trace(history, ranges=ranges))
    assert finding.holds
    assert finding.observed["metrics"][0]["clamped"] == pytest.approx(0.0)


def test_support_violated_when_most_observations_fall_outside_anchors() -> None:
    """Every worker outside the anchors scores identically, killing selection."""
    history = [
        _generation(g, scores={0: 10.0, 1: 20.0}, throughputs={0: 100.0, 1: 5000.0})
        for g in range(3)
    ]
    ranges = {"throughput": {"low": 900.0, "high": 1100.0, "direction": 1.0}}
    finding = check_normalizer_support(_trace(history, ranges=ranges))
    assert not finding.holds
    assert finding.observed["metrics"][0]["clamped"] == pytest.approx(1.0)
    assert finding.observed["breaches"] == ["throughput"]


# -------------------------------------------------------- read-back fidelity


def test_readback_holds_when_the_intended_value_survives() -> None:
    """A sentinel that stays a sentinel next generation is correct behaviour."""
    history = [
        _generation(
            0,
            scores={0: 10.0, 1: 20.0},
            configs={0: {"wal_buffers": -1}, 1: {"wal_buffers": -1}},
            actual_configs={0: {"wal_buffers": 512}, 1: {"wal_buffers": 512}},
        ),
        _generation(
            1,
            scores={0: 10.0, 1: 20.0},
            configs={0: {"wal_buffers": -1}, 1: {"wal_buffers": -1}},
            actual_configs={0: {"wal_buffers": 512}, 1: {"wal_buffers": 512}},
        ),
    ]
    finding = check_readback_fidelity(_trace(history))
    assert finding.holds
    assert finding.observed["worker_generations_compared"] == 2


def test_readback_violated_when_the_resolved_value_is_carried_forward() -> None:
    """Persisting the read-back destroys the sentinel irreversibly."""
    history = [
        _generation(
            0,
            scores={0: 10.0, 1: 20.0},
            configs={0: {"wal_buffers": -1}, 1: {"wal_buffers": -1}},
            actual_configs={0: {"wal_buffers": 512}, 1: {"wal_buffers": 512}},
        ),
        _generation(
            1,
            scores={0: 10.0, 1: 20.0},
            configs={0: {"wal_buffers": 512}, 1: {"wal_buffers": 512}},
            actual_configs={0: {"wal_buffers": 512}, 1: {"wal_buffers": 512}},
        ),
    ]
    finding = check_readback_fidelity(_trace(history))
    assert not finding.holds
    assert finding.observed["sentinel_losses"] == 2
    assert finding.observed["knobs_affected"] == ["wal_buffers"]


def test_readback_ignores_workers_that_exploited_at_either_endpoint() -> None:
    """An inherited donor value must not be mistaken for a carried read-back."""
    history = [
        _generation(
            0,
            scores={0: 10.0, 1: 20.0},
            configs={0: {"wal_buffers": -1}, 1: {"wal_buffers": 512}},
            actual_configs={0: {"wal_buffers": 512}, 1: {"wal_buffers": 512}},
        ),
        _generation(
            1,
            scores={0: 10.0, 1: 20.0},
            configs={0: {"wal_buffers": 512}, 1: {"wal_buffers": 512}},
            actual_configs={0: {"wal_buffers": 512}, 1: {"wal_buffers": 512}},
            exploitations=[{"elite_worker_id": 1, "poor_worker_id": 0}],
        ),
    ]
    finding = check_readback_fidelity(_trace(history))
    assert finding.holds, "worker 0 inherited 512 from its donor, not from read-back"


def test_readback_skips_traces_without_recorded_read_back() -> None:
    """Older traces carry no actual_config; the check abstains, not fails."""
    history = [
        _generation(g, scores={0: 10.0, 1: 20.0}, configs={0: {"a": 1}, 1: {"a": 1}})
        for g in range(2)
    ]
    finding = check_readback_fidelity(_trace(history))
    assert finding.holds
    assert finding.observed["worker_generations_compared"] == 0


# --------------------------------------------------------- search efficiency


def test_search_efficiency_holds_when_the_population_keeps_moving() -> None:
    """Distinct configurations every generation is a healthy search."""
    history = [
        _generation(
            g,
            scores={0: 10.0, 1: 20.0},
            configs={0: {"a": g}, 1: {"a": g + 100}},
        )
        for g in range(3)
    ]
    finding = check_search_efficiency(_trace(history))
    assert finding.holds
    assert finding.observed["distinct_configurations"] == 6
    assert finding.observed["evaluations"] == 6


def test_search_efficiency_violated_when_configurations_are_frozen() -> None:
    """Re-measuring two frozen configurations buys noise, not information."""
    history = [
        _generation(
            g,
            scores={0: 10.0, 1: 20.0},
            configs={0: {"a": 1}, 1: {"a": 2}},
        )
        for g in range(6)
    ]
    finding = check_search_efficiency(_trace(history))
    assert not finding.holds
    assert finding.observed["distinct_configurations"] == 2
    assert finding.observed["evaluations"] == 12
    assert finding.observed["ratio"] == pytest.approx(1 / 6)


# ------------------------------------------------------ score rank agreement


def test_rank_agreement_holds_when_score_follows_throughput() -> None:
    """Concordant ordering yields no inversions."""
    history = [
        _generation(0, scores={0: 10.0, 1: 20.0}, throughputs={0: 500.0, 1: 1000.0})
    ]
    finding = check_score_rank_agreement(_trace(history))
    assert finding.holds
    assert finding.observed["inversions"] == 0


def test_rank_agreement_violated_when_score_opposes_throughput() -> None:
    """A fully discordant ordering is the pathological case."""
    history = [
        _generation(g, scores={0: 90.0, 1: 10.0}, throughputs={0: 500.0, 1: 1000.0})
        for g in range(3)
    ]
    finding = check_score_rank_agreement(_trace(history))
    assert not finding.holds
    assert finding.observed["inversion_rate"] == pytest.approx(1.0)


def test_rank_agreement_ignores_pairs_inside_the_tie_band() -> None:
    """Throughputs within 2% are noise, not a ranking the score must match."""
    history = [
        _generation(0, scores={0: 90.0, 1: 10.0}, throughputs={0: 1000.0, 1: 1005.0})
    ]
    finding = check_score_rank_agreement(_trace(history))
    assert finding.observed["pairs_compared"] == 0


# ---------------------------------------------------------------- aggregate


def test_check_all_reports_every_invariant_once() -> None:
    """The aggregate runner covers the catalogue with no duplicates."""
    history = [
        _generation(g, scores={0: 10.0, 1: 20.0}, configs={0: {"a": g}, 1: {"a": g}})
        for g in range(2)
    ]
    findings = check_all(_trace(history))
    names = [f.invariant for f in findings]
    assert len(names) == len(set(names))
    assert "exploit_cadence" in names
    assert "readback_fidelity" in names


def test_violations_filters_to_the_broken_invariants() -> None:
    """``violations`` is the shorthand a failure report is built from."""
    history = [
        _generation(
            g,
            scores={0: 10.0, 1: 20.0},
            configs={0: {"a": 1}, 1: {"a": 2}},
        )
        for g in range(6)
    ]
    findings = check_all(_trace(history))
    broken = violations(findings)
    assert all(not f.holds for f in broken)
    assert "search_efficiency" in {f.invariant for f in broken}


def test_finding_renders_bug_ids_for_a_violation() -> None:
    """The rendered line names the ledger IDs so a failure is actionable."""
    history = [
        _generation(
            g,
            scores={0: 10.0, 1: 20.0},
            exploitations=[{"elite_worker_id": 1, "poor_worker_id": 0}],
        )
        for g in range(3)
    ]
    rendered = check_exploit_cadence(_trace(history, ready_interval=3)).render()
    assert "VIOLATED" in rendered
    assert "B1" in rendered
    assert "exploit_cadence" in rendered
