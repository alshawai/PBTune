"""
Output-path resolution shared across tuning strategies.

Every strategy emits results under a single workload-first layout::

    {output_dir}/sessions/{workload}/{strategy}/{tier_slug}/
                                                 ├── traces/
                                                 ├── best_configs/
                                                 └── logs/

The ``workload`` segment is a *single* granular key — e.g.
``oltp_read_write`` for sysbench OLTP mixes, ``olap`` for TPC-H, or the
raw ``workload_type`` for custom workloads. The two-level nesting that
older layouts used for sysbench (``oltp/oltp_read_write/``) is gone; the
caller resolves the key before calling :func:`resolve_tuner_output_root`.

Strategy-specific sub-directories (``tuning_sessions/`` →
``traces/``, ``best_configs/``, ``logs/``) are created by the leaf
writers (:mod:`src.tuners.utils.session_writer`, the per-strategy CLI).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

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
    workload: str,
    knob_tier: str,
    knob_source: str = "expert",
    ablation_variable: Optional[str] = None,
    ablation_value: Optional[str] = None,
) -> Path:
    """
    Resolve the base output directory for a tuning run.

    Parameters
    ----------
    output_dir
        Base results directory (e.g. ``Path("results")``).
    strategy
        Optimization strategy; selects the strategy path segment.
    workload
        Granular workload key — ``"oltp_read_write"`` for sysbench OLTP
        mixes, ``"olap"`` for TPC-H, or the raw ``workload_type`` for
        custom workloads. The caller derives this from benchmark +
        workload_type + sysbench_workload.
    knob_tier
        Knob tier slug.
    knob_source
        'expert' or 'data_driven' (the latter gets the ``@scalpel-v1`` suffix).
    ablation_variable
        Optional ablation study variable name (e.g. 'population_size').
    ablation_value
        Optional ablation study variable value (e.g. '4').

    Returns
    -------
    Path
        The strategy/tier-scoped output root (not yet created on disk).
    """
    strategy = TuningStrategy.from_value(strategy)
    tier = _tier_slug(knob_tier, knob_source)
    path = Path(output_dir) / "sessions" / workload / strategy.value / tier

    if ablation_variable and ablation_value is not None:
        path = path / "ablations" / str(ablation_variable) / str(ablation_value)

    return path
