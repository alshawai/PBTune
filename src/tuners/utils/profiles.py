"""
Execution-profile registry for the unified tuners package.

PBT treats the run *profile* (``rapid`` / ``standard`` / ``thorough`` /
``research``) as the base that supplies defaults at every level — execution
scalars (population, generations, worker count) *and* a matched
:class:`~src.utils.types.BenchmarkConfig` — which individual CLI flags then
override (see ``src/tuner/config/tuner_config.py`` and the two-layer resolution
in ``src/tuner/main.py``). The unified CLI reproduces that profile→override
model in a strategy-agnostic way.

A :class:`TunerProfile` captures only the cross-cutting layers every strategy
shares: the default worker count and the matched ``BenchmarkConfig``. Strategy-
specific per-profile scalars (PBT population/generations, LHS design size, ...)
are NOT held here — each strategy CLI owns its own ``{profile: scalar}`` map and
resolves its own default. This keeps the registry reusable across strategies
without leaking any one strategy's hyperparameters into the shared layer.

The ``BenchmarkConfig`` values are reused verbatim from the existing
``*_BENCHMARK_CONFIG`` constants in ``src/utils/types.py`` so the profile
defaults stay numerically identical to PBT — no new numbers are invented here.

``extreme`` is intentionally omitted: it is a PBT population-scale profile with
no meaningful analogue for the strategy-agnostic surface.

See ``docs/architecture/adr/ADR-006-unified-tuners-package.md`` for the
copy-not-refactor boundary this registry respects.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.utils.types import (
    BenchmarkConfig,
    RAPID_BENCHMARK_CONFIG,
    RESEARCH_BENCHMARK_CONFIG,
    STANDARD_BENCHMARK_CONFIG,
    THOROUGH_BENCHMARK_CONFIG,
)


@dataclass(frozen=True)
class TunerProfile:
    """A strategy-agnostic execution profile.

    Attributes
    ----------
    name
        Profile identifier ('rapid' | 'standard' | 'thorough' | 'research').
    num_parallel_workers
        Default number of PostgreSQL instances evaluated concurrently. The
        ``--parallel-workers`` flag overrides this when supplied.
    benchmark_config
        The matched :class:`~src.utils.types.BenchmarkConfig` whose execution
        scalars (duration, warmup, scale factor, table sizing) seed the
        per-flag override layer in ``build_benchmark_config``.
    """

    name: str
    num_parallel_workers: int
    benchmark_config: BenchmarkConfig


# Worker counts mirror the matched PBT profiles in
# ``src/tuner/config/tuner_config.py`` (RAPID=2, STANDARD=4, THOROUGH=8,
# RESEARCH=12). The benchmark configs are reused verbatim from src/utils/types.
PROFILES: dict[str, TunerProfile] = {
    "rapid": TunerProfile("rapid", 2, RAPID_BENCHMARK_CONFIG),
    "standard": TunerProfile("standard", 4, STANDARD_BENCHMARK_CONFIG),
    "thorough": TunerProfile("thorough", 8, THOROUGH_BENCHMARK_CONFIG),
    "research": TunerProfile("research", 12, RESEARCH_BENCHMARK_CONFIG),
}
