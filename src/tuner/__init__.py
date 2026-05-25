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
- core: PBT algorithm implementation (Population, Worker, Evolution)
- evaluator: Workload execution and performance measurement
- config: Knob space definition and PBT hyperparameters
- utils: Helper utilities (knob application, logging)

Author: Data-Vanta
License: MIT
"""

__version__ = "0.1.0"
__author__ = "Data-Vanta"

from src.tuner.core.worker import Worker

from src.tuner.config.tuner_config import PBTConfig
from src.tuner.config.knob_space import KnobSpace
from src.tuner.core import GenerationResult
from src.tuner.config.knob_loader import get_knob_space

__all__ = [
    "Worker",
    "PBTConfig",
    "KnobSpace",
    "GenerationResult",
    "get_knob_space",
]
