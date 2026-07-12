"""
PBT strategy package
=====================

Population-Based Training on top of the shared ``src.tuners`` framework. This
package holds the PBT-specific optimizer core: the worker that carries
evolutionary state, the population and evolution operators that drive the PBT
loop, and the PBT hyperparameter configuration.

Everything here depends *downward* on ``src.tuners.engine`` (the strategy-
agnostic evaluation machinery) and never the reverse.
"""

from src.tuners.pbt.worker import PBTWorker
from src.tuners.pbt.evolution import (
    truncation_selection,
    execute_exploit_explore,
    get_elite_workers,
    get_poor_workers,
    get_best_worker,
    get_population_statistics,
    check_convergence,
)
from src.tuners.pbt.population import (
    Population,
    PopulationConfig,
    GenerationResult,
)
from src.tuners.pbt.config import (
    PBTConfig,
    RAPID_CONFIG,
    STANDARD_CONFIG,
    THOROUGH_CONFIG,
    RESEARCH_CONFIG,
)
from src.tuners.pbt.tuner import PBTTuner

__all__ = [
    "PBTTuner",
    "PBTWorker",
    "Population",
    "PopulationConfig",
    "GenerationResult",
    "PBTConfig",
    "RAPID_CONFIG",
    "STANDARD_CONFIG",
    "THOROUGH_CONFIG",
    "RESEARCH_CONFIG",
    "truncation_selection",
    "execute_exploit_explore",
    "get_elite_workers",
    "get_poor_workers",
    "get_best_worker",
    "get_population_statistics",
    "check_convergence",
]
