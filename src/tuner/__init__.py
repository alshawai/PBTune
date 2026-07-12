"""
Population Based Training for PostgreSQL Configuration Tuning
==============================================================

This package implements Population Based Training (PBT), an evolutionary
hyperparameter optimization technique, for automatic tuning of PostgreSQL
configuration parameters (knobs).

PBT Concept:
-----------
PBT maintains a population of workers, each with different knob configurations.
Workers are periodically evaluated, and poor performers are replaced by
mutations of better performers, combining parallel exploration with
evolutionary selection pressure.

Package Structure:
-----------------
- The PBT optimizer core (Population, PBTWorker, Evolution, PBTConfig) now lives
  under ``src.tuners.pbt``; this package retains the ``main`` CLI entry point and
  re-exports the primary types for backward compatibility.

Author: Data-Vanta
License: MIT
"""

__version__ = "0.1.0"
__author__ = "Data-Vanta"

from src.tuners.pbt.worker import PBTWorker

from src.tuners.pbt.config import PBTConfig
from src.knobs.knob_space import KnobSpace
from src.tuners.pbt import GenerationResult
from src.knobs.knob_loader import get_knob_space

__all__ = [
    "PBTWorker",
    "PBTConfig",
    "KnobSpace",
    "GenerationResult",
    "get_knob_space",
]
