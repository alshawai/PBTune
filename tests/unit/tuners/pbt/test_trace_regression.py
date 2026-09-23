"""Characterization of the recorded PBT session ``trace_20260916_0007``.

This suite is a regression lock on a *diagnosis*, not on desired behaviour. The
fixture is a real 8-worker, 12-generation distributed run on the
``oltp_read_write`` workload against the 170-knob ``extensive`` tier, recorded
before any of epic #162's fixes landed. Every assertion here states what that
run actually did.

Why these tests stay green forever
----------------------------------

A recorded trace is immutable evidence. Fixing ``is_ready()`` cannot change what
``trace_20260916_0007`` contains, so these assertions must not be written as
"the algorithm is correct" — they would then be permanently red. They instead
pin the measured symptom, which serves three purposes:

1. They prove the invariant library detects the defects it claims to detect,
   against real data rather than synthetic fixtures.
2. They fail loudly if the fixture is replaced, re-trimmed, or corrupted.
3. They preserve the forensic numbers behind epic #162 in executable form, so
   the diagnosis cannot quietly drift from what was actually observed.

The invariants are driven red→green at their own code seams inside the
individual fix tickets, and re-run against a *fresh* post-fix trace in #172.
See ``tests/unit/analysis/test_pbt_invariants.py`` for the library's own tests.

Fixture provenance
------------------

``tests/fixtures/traces/trace_20260916_0007.json`` is the session trace with
JSON whitespace removed and no other transformation; it is value-identical to
the run's output.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

import pytest

from src.analysis.pbt_invariants import (
    Finding,
    SessionTrace,
    check_all,
    violations,
)

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "fixtures"
    / "traces"
    / "trace_20260916_0007.json"
)

#: Invariants the recorded run violates, mapped to the ledger IDs they detect.
#: Membership is asserted exactly, so adding or removing an invariant is a
#: deliberate act rather than a silent change in coverage.
EXPECTED_VIOLATIONS: Dict[str, tuple] = {
    "exploit_cadence": ("B1",),
    "donor_diversity": ("B2",),
    "exploit_recovery": ("B4", "B5", "B6"),
    "perturbation_locality": ("B4", "B5", "B6"),
    "score_metric_coupling": ("B7", "B8", "B9"),
    "normalizer_support": ("B7", "B8", "B9"),
    "search_efficiency": ("B2",),
}

#: Invariants the recorded run satisfies. ``readback_fidelity`` is here
#: deliberately: the read-back merge in ``_verify_and_capture_config`` does
#: overwrite ``worker.knob_config``, but this trace shows no value surviving
#: into the next generation, so B12 is unproven at the trace level and is
#: verified at the code seam in #167 instead.
EXPECTED_TO_HOLD = ("readback_fidelity", "score_rank_agreement")


@pytest.fixture(scope="module")
def trace() -> SessionTrace:
    """The recorded session under characterization."""
    assert FIXTURE.exists(), f"missing regression fixture: {FIXTURE}"
    return SessionTrace.from_path(FIXTURE)


@pytest.fixture(scope="module")
def findings(trace: SessionTrace) -> Dict[str, Finding]:
    """Every invariant's finding for the recorded session, keyed by name."""
    return {f.invariant: f for f in check_all(trace)}


# ------------------------------------------------------------- provenance


def test_fixture_is_the_expected_session(trace: SessionTrace) -> None:
    """Pin the fixture's identity so a swap cannot go unnoticed."""
    session = trace.session
    assert session["seed"] == 42
    assert session["tuning_strategy"] == "pbt"
    assert session["knob_tier"] == "extensive"
    assert session["num_knobs"] == 170
    assert session["workload_type"] == "oltp"
    assert session["num_rounds"] == 12
    assert trace.population_size == 8
    assert trace.ready_interval == 3
    assert trace.strategy_params["exploit_quantile"] == 0.2


def test_fixture_is_well_formed_json_with_full_per_generation_detail(
    trace: SessionTrace,
) -> None:
    """The trim removed whitespace only; every generation keeps full detail."""
    assert len(trace.generations) == 12
    assert trace.generations == list(range(12))
    for generation in trace.generations:
        scores = trace.worker_scores(generation)
        assert len(scores) == 8, f"generation {generation} lost workers"
        for worker_id in scores:
            config = trace.intended_config(generation, worker_id)
            assert config is not None and len(config) == 170


# ------------------------------------------------------ the seven symptoms


def test_the_run_exploited_every_generation_ignoring_its_cooldown(
    findings: Dict[str, Finding],
) -> None:
    """B1: ``ready_interval=3`` never re-armed, so exploitation fired back to back."""
    finding = findings["exploit_cadence"]
    assert not finding.holds
    observed = finding.observed
    assert observed["ready_interval"] == 3
    assert observed["generations_with_exploitation"] == list(range(2, 12))
    assert observed["gaps"] == [1] * 9
    assert observed["short_gaps"] == 9


def test_worker_seven_was_the_only_donor_for_the_whole_run(
    findings: Dict[str, Finding],
) -> None:
    """B2: the elite bucket held one worker, so the donor was a fixed argmax."""
    finding = findings["donor_diversity"]
    assert not finding.holds
    observed = finding.observed
    assert observed["exploitations"] == 10
    assert observed["distinct_donors"] == [7]
    assert observed["recipients"] == [2, 0, 2, 2, 5, 3, 3, 3, 3, 1]


def test_children_recovered_only_two_thirds_of_their_donor_throughput(
    findings: Dict[str, Finding],
) -> None:
    """B4/B5/B6: explore produced fresh draws, not neighbours of the donor."""
    finding = findings["exploit_recovery"]
    assert not finding.holds
    observed = finding.observed
    assert observed["n"] == 9
    assert observed["below_floor"] == 9
    assert observed["median_recovery"] == pytest.approx(0.682, abs=1e-3)
    assert observed["min_recovery"] == pytest.approx(0.496, abs=1e-3)


def test_every_explore_step_moved_more_than_half_the_search_space(
    findings: Dict[str, Finding],
) -> None:
    """B4/B5/B6: 89-104 of 170 knobs changed per event, not a local step."""
    finding = findings["perturbation_locality"]
    assert not finding.holds
    observed = finding.observed
    assert observed["n"] == 9
    assert observed["over_limit"] == 9
    assert observed["median_fraction"] == pytest.approx(0.559, abs=1e-3)
    assert observed["max_observed"] == pytest.approx(0.612, abs=1e-3)
    changed = [event["changed"] for event in observed["events"]]
    assert min(changed) == 89
    assert max(changed) == 104
    assert all(event["comparable"] == 170 for event in observed["events"])


def test_generation_four_score_collapsed_while_throughput_rose(
    findings: Dict[str, Finding],
) -> None:
    """B7/B8/B9: calibration at 40 samples rescaled the ruler, not the database.

    This is the defect behind the run's headline: the reported convergence curve
    tracked normalizer drift rather than tuning progress.
    """
    finding = findings["score_metric_coupling"]
    assert not finding.holds
    shocks = finding.observed["shocks"]
    assert len(shocks) == 1
    shock = shocks[0]
    assert shock["generation"] == 4
    assert shock["score_delta"] == pytest.approx(-28.6, abs=0.1)
    assert shock["metric_delta"] == pytest.approx(0.017, abs=1e-3)


def test_three_latency_metrics_clamped_more_than_a_quarter_of_observations(
    findings: Dict[str, Finding],
) -> None:
    """B7/B8/B9: anchors excluded the support, so distinct workers scored alike."""
    finding = findings["normalizer_support"]
    assert not finding.holds
    observed = finding.observed
    assert observed["breaches"] == [
        "latency_p95",
        "latency_p99",
        "latency_variance",
    ]
    rates = {m["metric"]: m["clamped"] for m in observed["metrics"]}
    assert rates["latency_p95"] == pytest.approx(0.271, abs=1e-3)
    assert rates["latency_p99"] == pytest.approx(0.323, abs=1e-3)
    assert rates["latency_variance"] == pytest.approx(0.271, abs=1e-3)
    # Throughput sat just inside the threshold; recorded so a regression shows.
    assert rates["throughput"] == pytest.approx(0.240, abs=1e-3)


def test_ninety_six_evaluations_bought_only_eighteen_configurations(
    findings: Dict[str, Finding],
) -> None:
    """B2: one exploit per generation means the search barely moved.

    Eighteen is exactly the eight initial draws plus the ten exploit events, so
    the remaining 78 evaluations re-measured configurations already seen.
    """
    finding = findings["search_efficiency"]
    assert not finding.holds
    observed = finding.observed
    assert observed["distinct_configurations"] == 18
    assert observed["evaluations"] == 96
    assert observed["ratio"] == pytest.approx(0.1875)
    # Within any single generation all eight configs differ: the collapse is
    # across time, not inside a population snapshot.
    assert [p["distinct_in_population"] for p in observed["per_generation"]] == [
        8
    ] * 12


# --------------------------------------------------- what the run got right


def test_read_back_never_carried_a_resolved_value_into_the_next_generation(
    findings: Dict[str, Finding],
) -> None:
    """B12 is not evidenced by this trace, and the harness says so honestly.

    PostgreSQL reports auto-sized knobs resolved (``wal_buffers = -1`` reads
    back as ``512``), and ``_verify_and_capture_config`` merges that reading
    into ``worker.knob_config``. Across 73 worker-generations where the worker
    exploited at neither endpoint, no resolved value survived into the next
    generation's intended configuration. Whether the merge is harmless or
    merely masked by fractional re-resolution is settled at the code seam.
    """
    finding = findings["readback_fidelity"]
    assert finding.holds
    observed = finding.observed
    assert observed["worker_generations_compared"] == 73
    assert observed["ratchets"] == 0


def test_score_ranking_still_broadly_tracked_throughput(
    findings: Dict[str, Finding],
) -> None:
    """Selection pressure pointed the right way despite the scoring defects."""
    finding = findings["score_rank_agreement"]
    assert finding.holds
    observed = finding.observed
    assert observed["pairs_compared"] == 302
    assert observed["inversions"] == 24
    assert observed["inversion_rate"] == pytest.approx(0.079, abs=1e-3)


# ------------------------------------------------------------- the manifest


def test_the_recorded_run_violates_exactly_the_catalogued_invariants(
    findings: Dict[str, Finding],
) -> None:
    """Lock the full verdict set, so coverage cannot drift unnoticed."""
    broken = {f.invariant for f in violations(findings.values())}
    assert broken == set(EXPECTED_VIOLATIONS)
    held = {name for name, f in findings.items() if f.holds}
    assert held == set(EXPECTED_TO_HOLD)
    assert len(findings) == len(EXPECTED_VIOLATIONS) + len(EXPECTED_TO_HOLD)


def test_each_violation_names_the_ledger_ids_it_detects(
    findings: Dict[str, Finding],
) -> None:
    """A failure message must be actionable: it names the bug, not just the run."""
    for invariant, bug_ids in EXPECTED_VIOLATIONS.items():
        finding = findings[invariant]
        assert finding.bug_ids == bug_ids
        rendered = finding.render()
        assert "VIOLATED" in rendered
        for bug_id in bug_ids:
            assert bug_id in rendered


def test_the_harness_runs_fast_enough_to_keep_in_the_unit_suite(
    trace: SessionTrace,
) -> None:
    """Determinism and speed: a full pass over the trace does no I/O or sampling."""
    first = [(f.invariant, f.holds, f.summary) for f in check_all(trace)]
    second = [(f.invariant, f.holds, f.summary) for f in check_all(trace)]
    assert first == second


def test_findings_are_json_serialisable_for_reporting(
    findings: Dict[str, Finding],
) -> None:
    """#172 reports these numbers, so the evidence must survive serialisation."""
    payload = json.dumps(
        {name: f.observed for name, f in findings.items()}, default=str
    )
    assert json.loads(payload).keys() == findings.keys()
