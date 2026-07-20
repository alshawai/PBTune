"""
BO strategy package
===================

Bayesian Optimization baseline on top of the shared ``src.tuners`` framework.
This package holds the BO-specific optimizer core: the SMAC3 facade wiring, the
``KnobSpace`` <-> ``ConfigSpace`` translation, the SMAC-compatible objective,
the co-tenant load controller that reproduces PBT's parallel contention on a
strictly sequential optimiser, and the BO hyperparameter configuration.

Everything here depends *downward* on ``src.tuners.engine`` (the strategy-
agnostic evaluation machinery) and never the reverse.
"""

from src.tuners.bo.config import (
    BOConfig,
    BO_CONFIG_PRESETS,
    RAPID_BO_CONFIG,
    STANDARD_BO_CONFIG,
    THOROUGH_BO_CONFIG,
    RESEARCH_BO_CONFIG,
    EXTREME_BO_CONFIG,
)
from src.tuners.bo.search_space import (
    build_configspace,
    configspace_to_knobs,
    knobs_to_configspace,
    get_config_drift,
    build_env_context,
)
from src.tuners.bo.objective import evaluate_config
from src.tuners.bo.cotenant import CoTenantLoadController
from src.tuners.bo.tuner import BOTuner

__all__ = [
    "BOTuner",
    "BOConfig",
    "BO_CONFIG_PRESETS",
    "RAPID_BO_CONFIG",
    "STANDARD_BO_CONFIG",
    "THOROUGH_BO_CONFIG",
    "RESEARCH_BO_CONFIG",
    "EXTREME_BO_CONFIG",
    "build_configspace",
    "configspace_to_knobs",
    "knobs_to_configspace",
    "get_config_drift",
    "build_env_context",
    "evaluate_config",
    "CoTenantLoadController",
]
