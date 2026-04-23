"""
Bayesian Optimization Backend
==============================

Provides the BO optimizer abstraction and ConfigSpace translation utilities.

This module bridges the PBT project's KnobSpace representation with SMAC3's
ConfigSpace-based optimization loop, preserving log-scale semantics,
integer step constraints, and categorical/boolean knob types.

Design Decision:
    SMAC3 was chosen as the BO backend for the following reasons:
    - Mature ConfigSpace support with native integer, float, categorical types
    - Log-scale hyperparameter support (critical for DB knobs like work_mem)
    - Proven track record in algorithm configuration (AutoML, hyperparameter
      optimization benchmarks)
    - Active maintenance (Lindauer et al., JMLR 2022)

    OpenBox was considered but SMAC3's tighter ConfigSpace integration and
    broader adoption in the hyperparameter optimization literature made it
    the more defensible choice for academic comparison.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

try:
    import ConfigSpace as CS
    from ConfigSpace import (
        ConfigurationSpace,
        Configuration,
        Float,
        Integer,
        Categorical,
    )
except ImportError:
    raise ImportError(
        "ConfigSpace is required for BO comparison. "
        "Install it with: pip install 'ConfigSpace>=0.6.1'"
    )

try:
    from smac import BlackBoxFacade, Scenario
    from smac.initial_design.sobol_design import SobolInitialDesign

    SMAC_AVAILABLE = True
except ImportError:
    SMAC_AVAILABLE = False

from src.tuner.config.knob_space import KnobSpace, KnobDefinition, KnobType, KnobScale
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class BOConfig:
    """
    Configuration for Bayesian Optimization.

    Attributes:
        optimizer_backend: BO library to use ('smac').
        max_evaluations: Maximum number of configurations to evaluate.
        initial_design_size: Number of random initial evaluations before
            the BO surrogate model is trained. If None, defaults to
            max(5, num_hyperparameters).
        acquisition_function: Acquisition function name ('EI', 'LCB', 'PI').
    """

    optimizer_backend: str = "smac"
    max_evaluations: int = 30
    initial_design_size: Optional[int] = None
    acquisition_function: str = "EI"


@dataclass
class BOResult:
    """
    Result of a single BO evaluation.

    Attributes:
        config: The knob configuration evaluated.
        score: Performance score (higher = better, [0, 100] scale).
        cost: Negated score for minimization-based BO.
        wall_time: Wall-clock time for this evaluation.
    """

    config: Dict[str, Any]
    score: float
    cost: float
    wall_time: float


def build_configspace_from_knob_space(knob_space: KnobSpace) -> ConfigurationSpace:
    """
    Translate PBT KnobSpace into a SMAC-compatible ConfigurationSpace.

    Mapping rules:
    - INTEGER knobs → Integer hyperparameter (with log-scale if KnobScale.LOG)
    - REAL knobs → Float hyperparameter (with log-scale if KnobScale.LOG)
    - BOOLEAN knobs → Categorical hyperparameter with ["true", "false"]
    - ENUM knobs → Categorical hyperparameter with enum_values

    Args:
        knob_space: The PBT knob space definition.

    Returns:
        ConfigurationSpace with hyperparameters matching knob_space.

    Raises:
        ValueError: If a knob type is unrecognized or has invalid bounds.
    """
    cs = ConfigurationSpace(seed=42)
    hyperparameters = []

    for name, knob in knob_space.knobs.items():
        hp = _knob_to_hyperparameter(knob)
        if hp is not None:
            hyperparameters.append(hp)

    cs.add(hyperparameters)

    logger.debug(
        "Built ConfigSpace: %d hyperparameters from %d knobs",
        len(hyperparameters),
        len(knob_space.knobs),
    )

    return cs


def _knob_to_hyperparameter(
    knob: KnobDefinition,
) -> Optional[CS.hyperparameters.Hyperparameter]:
    """
    Convert a single KnobDefinition to a ConfigSpace hyperparameter.

    Preserves log-scale semantics for knobs marked as KnobScale.LOG.
    Integer knobs with step > 1 are still represented as Integer HPs
    (ConfigSpace handles discrete grids internally).

    Args:
        knob: The knob definition to convert.

    Returns:
        A ConfigSpace hyperparameter, or None if conversion failed.
    """
    if knob.knob_type == KnobType.INTEGER:
        if knob.min_value is None or knob.max_value is None:
            logger.warning(
                "Skipping knob '%s': INTEGER knob without bounds", knob.name
            )
            return None

        lower = int(knob.min_value)
        upper = int(knob.max_value)

        if lower >= upper:
            logger.warning(
                "Skipping knob '%s': lower (%d) >= upper (%d)",
                knob.name,
                lower,
                upper,
            )
            return None

        use_log = knob.scale == KnobScale.LOG and lower > 0

        # Clamp default into resolved bounds (hardware-aware resolution can
        # shift bounds away from the original pg_settings default).
        default = None
        if knob.default is not None:
            default = max(lower, min(upper, int(knob.default)))

        return Integer(
            name=knob.name,
            bounds=(lower, upper),
            log=use_log,
            default=default,
        )

    elif knob.knob_type == KnobType.REAL:
        if knob.min_value is None or knob.max_value is None:
            logger.warning(
                "Skipping knob '%s': REAL knob without bounds", knob.name
            )
            return None

        lower = float(knob.min_value)
        upper = float(knob.max_value)

        if lower >= upper:
            logger.warning(
                "Skipping knob '%s': lower (%.4f) >= upper (%.4f)",
                knob.name,
                lower,
                upper,
            )
            return None

        use_log = knob.scale == KnobScale.LOG and lower > 0

        # Clamp default into resolved bounds.
        default = None
        if knob.default is not None:
            default = max(lower, min(upper, float(knob.default)))

        return Float(
            name=knob.name,
            bounds=(lower, upper),
            log=use_log,
            default=default,
        )

    elif knob.knob_type == KnobType.BOOLEAN:
        return Categorical(
            name=knob.name,
            items=["true", "false"],
            default=str(knob.default).lower()
            if knob.default is not None
            else "false",
        )

    elif knob.knob_type == KnobType.ENUM:
        if not knob.enum_values:
            logger.warning(
                "Skipping knob '%s': ENUM knob without values", knob.name
            )
            return None

        return Categorical(
            name=knob.name,
            items=knob.enum_values,
            default=str(knob.default) if knob.default is not None else None,
        )

    else:
        logger.warning(
            "Skipping knob '%s': unrecognized type %s", knob.name, knob.knob_type
        )
        return None


def configspace_sample_to_knob_config(
    cs_config: Configuration, knob_space: KnobSpace
) -> Dict[str, Any]:
    """
    Convert a ConfigSpace Configuration back to a PBT knob config dict.

    Handles type coercion:
    - Integer HPs → int
    - Float HPs → float
    - Boolean categorical ("true"/"false") → bool
    - Enum categorical → str
    - Values are normalized via KnobDefinition.normalize_value()

    Args:
        cs_config: A SMAC Configuration object.
        knob_space: The PBT knob space for type information and normalization.

    Returns:
        Dict mapping knob names to their properly typed values.
    """
    knob_config: Dict[str, Any] = {}
    config_dict = dict(cs_config)

    for name, value in config_dict.items():
        if name not in knob_space.knobs:
            continue

        knob = knob_space.knobs[name]

        if knob.knob_type == KnobType.BOOLEAN:
            # ConfigSpace stores booleans as categorical strings
            if isinstance(value, str):
                value = value.lower() == "true"
            else:
                value = bool(value)

        elif knob.knob_type == KnobType.INTEGER:
            value = int(round(value))

        elif knob.knob_type == KnobType.REAL:
            value = float(value)

        elif knob.knob_type == KnobType.ENUM:
            value = str(value)

        # Normalize to valid domain (clamp, step-align, etc.)
        knob_config[name] = knob.normalize_value(value)

    return knob_config


class BOOptimizer:
    """
    Wrapper around SMAC3's BlackBoxFacade for sequential BO.

    Provides a simple suggest/report interface for the comparison runner.
    Internally manages the SMAC Scenario, initial design, and surrogate model.

    The optimizer operates in a fully sequential mode (one evaluation at a time),
    which is standard BO behavior and provides the fairest comparison against
    PBT's parallel approach.

    Args:
        config_space: ConfigSpace defining the search space.
        bo_config: BO configuration parameters.
        seed: Random seed for reproducibility.

    Raises:
        ImportError: If SMAC3 is not installed.
    """

    def __init__(
        self,
        config_space: ConfigurationSpace,
        bo_config: BOConfig,
        seed: int = 42,
    ) -> None:
        if not SMAC_AVAILABLE:
            raise ImportError(
                "SMAC3 is required for Bayesian Optimization comparison. "
                "Install it with: pip install 'smac>=2.0.0'"
            )

        self.config_space = config_space
        self.bo_config = bo_config
        self.seed = seed

        # Determine initial design size
        n_hyperparams = len(config_space)
        if bo_config.initial_design_size is not None:
            initial_design_size = bo_config.initial_design_size
        else:
            initial_design_size = max(5, n_hyperparams)

        # Ensure initial design doesn't exceed max evaluations
        initial_design_size = min(
            initial_design_size, bo_config.max_evaluations
        )

        # Build SMAC Scenario
        self.scenario = Scenario(
            configspace=config_space,
            n_trials=bo_config.max_evaluations,
            seed=seed,
            deterministic=True,
        )

        # Build initial design
        initial_design = SobolInitialDesign(
            scenario=self.scenario,
            n_configs=initial_design_size,
        )

        # Create SMAC facade (BlackBoxFacade uses Random Forest surrogate + EI)
        self.smac = BlackBoxFacade(
            scenario=self.scenario,
            target_function=self._dummy_target,  # We use ask/tell interface
            initial_design=initial_design,
            overwrite=True,
        )

        # State tracking
        self._pending_config: Optional[Configuration] = None
        self._pending_trial_info: Optional[Any] = None
        self._eval_count: int = 0

        logger.info(
            "Initialized SMAC3 BO: %d hyperparameters, %d initial design, "
            "%d max evaluations, seed=%d",
            n_hyperparams,
            initial_design_size,
            bo_config.max_evaluations,
            seed,
        )

    @staticmethod
    def _dummy_target(config: Configuration, seed: int = 0) -> float:
        """Placeholder target function (we use ask/tell interface instead)."""
        return 0.0

    def suggest(self) -> Configuration:
        """
        Get the next configuration to evaluate from SMAC.

        SMAC's ask() returns a TrialInfo object. We store it so that
        report() can pass it back to tell() with the evaluation result.

        Returns:
            A ConfigSpace Configuration suggested by the BO model.
        """
        self._pending_trial_info = self.smac.ask()
        self._pending_config = self._pending_trial_info.config
        return self._pending_trial_info.config

    def report(self, config: Configuration, cost: float) -> None:
        """
        Report the result of evaluating a configuration.

        Uses SMAC's tell(TrialInfo, TrialValue) interface. The TrialInfo
        comes from the preceding ask() call, ensuring consistency.

        Args:
            config: The configuration that was evaluated (must match last suggest).
            cost: The cost (negated score) — SMAC minimizes this.
        """
        from smac.runhistory import TrialValue
        from smac.runner.abstract_runner import StatusType

        if self._pending_trial_info is None:
            raise RuntimeError(
                "report() called without a preceding suggest(). "
                "Always call suggest() before report()."
            )

        trial_value = TrialValue(cost=cost, time=0.0, status=StatusType.SUCCESS)
        self.smac.tell(self._pending_trial_info, trial_value)

        self._pending_trial_info = None
        self._eval_count += 1
        logger.debug(
            "Reported evaluation %d to SMAC: cost=%.4f",
            self._eval_count,
            cost,
        )
