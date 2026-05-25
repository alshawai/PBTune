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

    return cs


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
