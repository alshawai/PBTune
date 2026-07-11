"""
Tuner Execution Engine
======================

Tuner-agnostic execution primitives shared across all tuning strategies:

- Worker: per-instance configuration + performance state
- Lockstep generation barriers (B1–B17 synchronisation)
- TuningMode-driven restart policy

These modules are consumed by every tuner (PBT, BO, LHS) and by the
per-worker orchestration pipeline. They depend only on ``src.utils`` — never
on a specific tuning strategy — so they sit below the individual tuners in the
dependency graph.
"""

from src.tuners.engine.barriers import GenerationBarrier, BARRIER_NAMES
from src.tuners.engine.restart_policy import should_restart
from src.tuners.engine.worker import Worker

__all__ = [
    "GenerationBarrier",
    "BARRIER_NAMES",
    "should_restart",
    "Worker",
]
