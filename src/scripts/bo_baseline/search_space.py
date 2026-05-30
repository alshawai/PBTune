"""Search space translation between KnobSpace and ConfigSpace."""

from typing import Dict, Any
import numpy as np
from ConfigSpace import (
    ConfigurationSpace,
    Integer,
    Float,
    Categorical,
    Constant,
    Configuration,
    NotEqualsCondition,
    ForbiddenAndConjunction,
    ForbiddenEqualsClause,
    ForbiddenInClause,
    ForbiddenLessThanRelation,
    ForbiddenGreaterThanRelation,
)
from ConfigSpace.hyperparameters import (
    IntegerHyperparameter,
    FloatHyperparameter,
    CategoricalHyperparameter,
)

from src.tuner.config.knob_space import KnobSpace, KnobType, KnobScale
from src.utils.logger import get_logger

LOGGER = get_logger("SearchSpace")


def build_configspace(knob_space: KnobSpace, seed: int = 42) -> ConfigurationSpace:
    """
    Translate KnobSpace into a ConfigSpace ConfigurationSpace.

    Parameters
    ----------
    knob_space : KnobSpace
        The knob space to translate
    seed : int
        Random seed for reproducibility

    Returns
    -------
    ConfigurationSpace
        ConfigSpace representation of the knob space
    """
    cs = ConfigurationSpace(seed=seed)

    auto_zero_knobs = {
        "commit_timestamp_buffers",
        "subtransaction_buffers",
        "transaction_buffers",
    }

    for knob_def in knob_space.knobs.values():
        name = knob_def.name

        # Handle degenerate ranges (min == max)
        if (
            knob_def.min_value is not None
            and knob_def.max_value is not None
            and knob_def.min_value == knob_def.max_value
        ):
            cs.add(Constant(name, knob_def.min_value))
            continue

        elif knob_def.knob_type == KnobType.INTEGER:
            min_val = int(knob_def.min_value) if knob_def.min_value is not None else 0
            max_val = (
                int(knob_def.max_value) if knob_def.max_value is not None else 2**31 - 1
            )

            if name in auto_zero_knobs and min_val == 0:
                min_val = 1

            # For log scale, ensure min > 0
            if knob_def.scale == KnobScale.LOG:
                min_val = max(min_val, 1)

            # Ensure default is within range
            default = None
            if knob_def.default is not None:
                default = int(knob_def.default)
                if name in auto_zero_knobs and default == 0:
                    default = 1
                if default < min_val or default > max_val:
                    default = None

            param = Integer(
                name,
                bounds=(min_val, max_val),
                log=(knob_def.scale == KnobScale.LOG),
                default=default,
            )
            cs.add(param)

        elif knob_def.knob_type == KnobType.REAL:
            min_val_f: float = (
                float(knob_def.min_value) if knob_def.min_value is not None else 0.0
            )
            max_val_f: float = (
                float(knob_def.max_value) if knob_def.max_value is not None else 1.0
            )

            # For log scale, ensure min > 0
            if knob_def.scale == KnobScale.LOG:
                min_val_f = max(min_val_f, 1e-9)

            # Ensure default is within range
            default_f: float | None = None
            if knob_def.default is not None:
                default_val_f = float(knob_def.default)
                if default_val_f < min_val_f or default_val_f > max_val_f:
                    default_f = None
                else:
                    default_f = default_val_f

            param_f = Float(
                name,
                bounds=(min_val_f, max_val_f),
                log=(knob_def.scale == KnobScale.LOG),
                default=default_f,
            )
            cs.add(param_f)

        elif knob_def.knob_type == KnobType.BOOLEAN:
            param = Categorical(
                name,
                ["on", "off"],
                default="on" if knob_def.default else "off",
            )
            cs.add(param)

        elif knob_def.knob_type == KnobType.ENUM:
            if knob_def.enum_values is None:
                LOGGER.warning(f"Enum knob {name} has no enum_values, skipping")
                continue

            default = None
            if (
                knob_def.default is not None
                and knob_def.default in knob_def.enum_values
            ):
                default = knob_def.default

            if default is None:
                param = Categorical(
                    name,
                    knob_def.enum_values,
                )
            else:
                param = Categorical(
                    name,
                    knob_def.enum_values,
                    default=default,
                )
            cs.add(param)

    _add_configspace_constraints(cs)

    return cs


def _add_configspace_constraints(cs: ConfigurationSpace) -> None:
    """
    Add logical constraints to the ConfigSpace.
    
    These prevent SMAC from sampling invalid or conflicting configurations
    that would otherwise just be repaired, reducing wasted evaluations.
    """
    # 1. wal_level=minimal deactivates certain WAL features
    if "wal_level" in cs:
        if "archive_mode" in cs:
            cs.add(NotEqualsCondition(cs["archive_mode"], cs["wal_level"], "minimal"))
        if "max_wal_senders" in cs:
            cs.add(NotEqualsCondition(cs["max_wal_senders"], cs["wal_level"], "minimal"))
        if "summarize_wal" in cs:
            cs.add(NotEqualsCondition(cs["summarize_wal"], cs["wal_level"], "minimal"))

    # 2. huge_pages=on|try is incompatible with shared_memory_type=sysv
    if "huge_pages" in cs and "shared_memory_type" in cs:
        cs.add(ForbiddenAndConjunction(
            ForbiddenInClause(cs["huge_pages"], ["on", "try"]),
            ForbiddenEqualsClause(cs["shared_memory_type"], "sysv"),
        ))

    # 3. max_worker_processes must be >= max_parallel_workers
    if "max_worker_processes" in cs and "max_parallel_workers" in cs:
        cs.add(ForbiddenLessThanRelation(cs["max_worker_processes"], cs["max_parallel_workers"]))

    # 4. min_wal_size must be <= max_wal_size
    if "min_wal_size" in cs and "max_wal_size" in cs:
        cs.add(ForbiddenGreaterThanRelation(cs["min_wal_size"], cs["max_wal_size"]))


def configspace_to_knobs(
    cs_config: Configuration, knob_space: KnobSpace
) -> Dict[str, Any]:
    """
    Convert a ConfigSpace Configuration back to a knob config dict.

    Parameters
    ----------
    cs_config : Configuration
        ConfigSpace configuration object
    knob_space : KnobSpace
        The knob space for type conversion

    Returns
    -------
    Dict[str, Any]
        Knob configuration dictionary with proper Python types
    """
    config_dict: Dict[str, Any] = {}

    for knob_def in knob_space.knobs.values():
        name = knob_def.name

        if name not in cs_config:
            continue

        value = cs_config[name]

        # Convert numpy types to Python types
        if isinstance(value, np.integer):
            config_dict[name] = int(value)
        elif isinstance(value, np.floating):
            config_dict[name] = float(value)
        elif isinstance(value, (int, float, str, bool)):
            config_dict[name] = value
        else:
            config_dict[name] = value

    return config_dict


def knobs_to_configspace(
    knob_config: Dict[str, Any],
    knob_space: KnobSpace,
    configspace: ConfigurationSpace,
) -> Configuration:
    """
    Convert a knob config dict back to a ConfigSpace Configuration.
    
    Values are clamped to ConfigSpace bounds and inactive hyperparameters
    (those deactivated by Conditions) are omitted, so the Configuration
    is always valid w.r.t. the defined constraints.

    Parameters
    ----------
    knob_config : Dict[str, Any]
        Repaired/quantized knob configuration dictionary
    knob_space : KnobSpace
        The knob space
    configspace : ConfigurationSpace
        The ConfigSpace definition to validate against

    Returns
    -------
    Configuration
        Valid ConfigSpace configuration
    """
    values = {}
    for hp in list(configspace.values()):
        name = hp.name
        if name not in knob_config:
            continue
        val = knob_config[name]
        
        # Clamp to CS bounds for numeric types
        if isinstance(hp, IntegerHyperparameter):
            val = int(max(hp.lower, min(hp.upper, int(val))))
        elif isinstance(hp, FloatHyperparameter):
            val = float(max(hp.lower, min(hp.upper, float(val))))
        elif isinstance(hp, CategoricalHyperparameter):
            if val not in hp.choices:
                val = hp.default_value  # fallback
        values[name] = val

    # allow_inactive_with_values=True handles conditional HPs gracefully
    return Configuration(configspace, values=values, allow_inactive_with_values=True)
