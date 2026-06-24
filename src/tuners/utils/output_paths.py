"""
Output-path resolution shared across tuning strategies.

Both PBT (``PBTTuner._build_output_dir``) and BO (``resolve_bo_output_root``)
independently derive a results directory of the form::

    {output_dir}/{workload_type}/[{sysbench_workload}/]{strategy}_runs/{tier_slug}/

This module lifts that convention into a single strategy-parameterized helper
so the LHS-design tuner produces a layout consistent with its siblings. The
incumbent functions are left untouched (copy-not-refactor); this is the
canonical implementation new strategies build on.
"""

from __future__ import annotations

from pathlib import Path

from src.tuners.utils.types import TuningStrategy


def _tier_slug(knob_tier: str, knob_source: str) -> str:
    """
    Suffix data-driven tiers with ``@scalpel-v1``.

    Mirrors the slugging in both incumbent writers: post-SCALPEL artifacts
    must not collide with pre-SCALPEL Jenks-era results that carried a
    different knob set under the same canonical tier name.
    """
    if str(knob_source) == "data_driven":
        return f"{knob_tier}@scalpel-v1"
    return knob_tier


def resolve_tuner_output_root(
    output_dir: Path | str,
    *,
    strategy: TuningStrategy | str,
    workload_type: str,
    benchmark: str,
    sysbench_workload: str,
    knob_tier: str,
    knob_source: str = "expert",
) -> Path:
    """
    Resolve the base output directory for a tuning run.

    Parameters
    ----------
    output_dir
        Base results directory (e.g. ``Path("results")``).
    strategy
        Optimization strategy; selects the ``{strategy}_runs`` segment.
    workload_type
        Workload flavor ('oltp' | 'olap' | 'mixed' | ...).
    benchmark
        Benchmark driver name ('sysbench' | 'tpch' | custom).
    sysbench_workload
        Sysbench script name. Only consulted when ``benchmark == 'sysbench'``,
        where it inserts an extra path segment to separate read-only /
        read-write / write-only sysbench variants.
    knob_tier
        Knob tier slug.
    knob_source
        'expert' or 'data_driven' (the latter gets the ``@scalpel-v1`` suffix).

    Returns
    -------
    Path
        The strategy/tier-scoped output root (not yet created on disk).
    """
    strategy = TuningStrategy.from_value(strategy)
    runs_segment = f"{strategy.value}_runs"
    tier_slug = _tier_slug(knob_tier, knob_source)
    base = Path(output_dir)

    if benchmark == "sysbench":
        return base / workload_type / sysbench_workload / runs_segment / tier_slug
    return base / workload_type / runs_segment / tier_slug
