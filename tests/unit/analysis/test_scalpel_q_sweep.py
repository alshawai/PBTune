"""Tests for the SCALPEL q-sensitivity sweep.

The sweep partitions a single set of BORUTA hit counts at four FDR
thresholds (q ∈ {0.05, 0.10, 0.20, 0.30}). Confirmed sets must be
monotone-nested: a knob confirmed at q=q_low MUST also be confirmed at
every q_high > q_low because BH-adjusted p-values are q-independent.
"""

from __future__ import annotations

import numpy as np

from src.analysis.scalpel_significance import (
    BorutaResult,
    partition_boruta_hits,
)


def test_partition_boruta_hits_returns_valid_result_at_each_q():
    """At any q ∈ (0, 1) the partition is exhaustive and disjoint."""
    rng = np.random.default_rng(0)
    knobs = [f"knob_{i}" for i in range(20)]
    # Mix of clearly-significant (high hits), clearly-insignificant (low hits),
    # and borderline (around half) knobs.
    hit_counts = np.concatenate(
        [
            rng.integers(80, 100, size=5),  # clearly significant
            rng.integers(0, 20, size=5),    # clearly rejected
            rng.integers(40, 60, size=10),  # borderline / tentative
        ]
    ).astype(np.int64)

    for q in (0.05, 0.10, 0.20, 0.30):
        result = partition_boruta_hits(hit_counts, knobs, n_iterations=100, fdr_q=q)
        assert isinstance(result, BorutaResult)
        union = set(result.confirmed) | set(result.tentative) | set(result.rejected)
        assert union == set(knobs)
        # Disjoint
        assert not (set(result.confirmed) & set(result.tentative))
        assert not (set(result.confirmed) & set(result.rejected))
        assert not (set(result.tentative) & set(result.rejected))


def test_partition_boruta_hits_confirmed_sets_are_monotone_nested():
    """confirmed(q=0.05) ⊆ confirmed(q=0.10) ⊆ confirmed(q=0.20) ⊆ confirmed(q=0.30)."""
    rng = np.random.default_rng(42)
    knobs = [f"k{i:02d}" for i in range(30)]
    # Spread hit counts across the full [0, 100] range to exercise the
    # BH cutoff at multiple q values.
    hit_counts = rng.integers(0, 101, size=30).astype(np.int64)

    confirmed_sets: list[set[str]] = []
    for q in (0.05, 0.10, 0.20, 0.30):
        result = partition_boruta_hits(hit_counts, knobs, n_iterations=100, fdr_q=q)
        confirmed_sets.append(set(result.confirmed))

    for low, high in zip(confirmed_sets[:-1], confirmed_sets[1:]):
        assert low <= high, f"monotonicity broken: {low - high}"


def test_partition_boruta_hits_p_values_are_q_independent():
    """Adjusted p-values do not depend on the q threshold (only the verdict does)."""
    rng = np.random.default_rng(7)
    knobs = ["a", "b", "c", "d"]
    hit_counts = rng.integers(0, 101, size=4).astype(np.int64)

    r_low = partition_boruta_hits(hit_counts, knobs, n_iterations=100, fdr_q=0.05)
    r_high = partition_boruta_hits(hit_counts, knobs, n_iterations=100, fdr_q=0.30)

    for k in knobs:
        assert r_low.p_values[k] == r_high.p_values[k]
        assert r_low.p_values_bh[k] == r_high.p_values_bh[k]
        assert r_low.hit_counts[k] == r_high.hit_counts[k]


def test_partition_boruta_hits_empty_input():
    result = partition_boruta_hits(
        np.array([], dtype=np.int64), [], n_iterations=0, fdr_q=0.10
    )
    assert result.confirmed == []
    assert result.tentative == []
    assert result.rejected == []
    assert result.n_iterations == 0


def test_partition_boruta_hits_classification_rules():
    """BH-adjusted p ≤ q AND hits > n/2 → confirmed; BH-adjusted p ≤ q AND hits ≤ n/2 → rejected; otherwise tentative."""
    knobs = ["high_hits", "low_hits", "borderline"]
    # 100 iterations, half = 50.
    hit_counts = np.array([95, 5, 50], dtype=np.int64)
    result = partition_boruta_hits(hit_counts, knobs, n_iterations=100, fdr_q=0.10)
    # 95 hits → far above null mean → significant + above half → confirmed.
    assert "high_hits" in result.confirmed
    # 5 hits → far below null mean → significant but below half → rejected.
    assert "low_hits" in result.rejected
    # 50 hits → at the null mean → tentative.
    assert "borderline" in result.tentative
