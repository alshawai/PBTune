"""#172 capstone: the corrected invariants clear a fresh post-fix trace.

Ticket #172, AC #1 ("the full regression harness passes with no remaining known
symptoms"). The immutable ``test_trace_regression.py`` pins the *pre-fix* trace's
symptoms under the original invariant definitions and can never turn green (a
recorded trace is immutable). This suite is its counterpart: a *fresh* trace
produced after every epic-#162 fix landed, audited against the corrected
invariant set (``check_corrected`` — per-worker cadence, per-knob magnitude
locality, median recovery), which must report **zero gating violations**.

Fixture provenance
------------------
``tests/fixtures/traces/trace_postfix_20260926_1033.json`` is a local 8-worker,
20-generation run on the ``oltp_read_write`` / 170-knob ``extensive`` tier at
seed 42, produced on ``main`` after PRs #173-#179 merged. The heavy
per-worker ``score_breakdown``/``timing`` blocks (not read by any invariant)
were stripped; all fields the invariants consume are value-identical to the run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.analysis.pbt_invariants import (
    SessionTrace,
    check_corrected,
    gating_violations,
)

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "traces"
POSTFIX = _FIXTURES / "trace_postfix_20260926_1033.json"
PREFIX = _FIXTURES / "trace_20260916_0007.json"

#: Invariants reported but not gated on (see INFORMATIONAL_INVARIANTS).
_INFORMATIONAL = {"score_rank_agreement"}


@pytest.fixture(scope="module")
def postfix() -> SessionTrace:
    assert POSTFIX.exists(), f"missing post-fix fixture: {POSTFIX}"
    return SessionTrace.from_path(POSTFIX)


def test_corrected_invariants_clear_the_postfix_trace(postfix: SessionTrace) -> None:
    """AC #1: no remaining known symptoms on the corrected run."""
    findings = check_corrected(postfix)
    gating = gating_violations(findings)
    assert gating == [], "unexpected gating violations: " + ", ".join(
        f"{f.invariant} ({f.summary})" for f in gating
    )
    # Every defect-signalling invariant holds; the informational one may not.
    for finding in findings:
        if finding.invariant not in _INFORMATIONAL:
            assert finding.holds, f"{finding.invariant}: {finding.summary}"


def test_postfix_trace_confirms_the_headline_fixes(postfix: SessionTrace) -> None:
    """The fixes this epic shipped are observable on real fresh data."""
    by_name = {f.invariant: f for f in check_corrected(postfix)}
    # #164: each worker honours the readiness cooldown.
    assert by_name["exploit_cadence_per_worker"].holds
    # #165: donors vary rather than collapsing to one lineage.
    assert by_name["donor_diversity"].holds
    assert len(by_name["donor_diversity"].observed["distinct_donors"]) > 1
    # #170: the elite is no longer clamped, so scoring tracks metrics.
    assert by_name["normalizer_support"].holds
    assert by_name["score_metric_coupling"].holds


def test_corrected_invariants_still_flag_the_prefix_trace() -> None:
    """The corrected set is not vacuous: it still detects the pre-fix symptoms
    (per-worker cooldown breach and the systematic recovery collapse) on the
    immutable old fixture, while the pre-fix run was magnitude-local."""
    findings = {f.invariant: f for f in check_corrected(SessionTrace.from_path(PREFIX))}
    assert not findings["exploit_cadence_per_worker"].holds  # B1 per-worker
    assert not findings["exploit_recovery_median"].holds  # B4/5/6 collapse
    assert not findings["donor_diversity"].holds  # B2 monoculture
    # The old run's defect was recovery, not per-knob magnitude:
    assert findings["perturbation_magnitude"].holds
