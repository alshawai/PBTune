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


def get_config_drift(expected: Dict[str, Any], actual: Dict[str, Any]) -> Dict[str, tuple]:
    """
    Compare two configurations and return the keys that differ,
    ignoring microscopic floating-point rounding errors.

    Returns
    -------
    Dict[str, tuple]
        Mapping of parameter names to (expected_val, actual_val)
    """
    import math
    drift = {}
    for k, v_exp in expected.items():
        if k not in actual:
            continue
        v_act = actual[k]

        if isinstance(v_exp, float) and isinstance(v_act, (float, int)):
            abs_tolerance = max(1e-6, abs(v_exp) * 1e-6)
            if not math.isclose(v_exp, v_act, rel_tol=1e-6, abs_tol=abs_tolerance):
                drift[k] = (v_exp, v_act)
        elif v_exp != v_act:
            drift[k] = (v_exp, v_act)

    return drift


def build_configspace(knob_space: KnobSpace, seed: int = 42) -> ConfigurationSpace:
    """
    Translate KnobSpace into a ConfigSpace ConfigurationSpace.

    All knobs are represented directly with their hardware-resolved absolute
    ranges.  There are no synthetic fraction parameters.

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

    auto_zero_knobs = knob_space.non_zero_knobs if hasattr(knob_space, "non_zero_knobs") else set()

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

            # Ensure default is within range
            default = None
            if knob_def.default is not None:
                default = int(knob_def.default)
                if name in auto_zero_knobs and default == 0:
                    default = 1
                if default < min_val or default > max_val:
                    default = None

            if knob_def.step and knob_def.step > 1:
                # Map discrete steps into continuous integer index representation
                step = knob_def.step

                # We shift indices by +1 to guarantee a minimum bound of 1.
                # This is mathematically required because ConfigSpace crashes
                # if you pass a 0 lower bound to a log-scaled distribution.
                cs_min = 1
                cs_max = ((max_val - min_val) // step) + 1

                cs_default = None
                if default is not None:
                    cs_default = ((default - min_val) // step) + 1
                    cs_default = max(cs_min, min(cs_max, cs_default))

                param = Integer(
                    name,
                    bounds=(cs_min, cs_max),
                    log=(knob_def.scale == KnobScale.LOG),
                    default=cs_default,
                )
                cs.add(param)
            else:
                # For log scale, ensure min > 0
                if knob_def.scale == KnobScale.LOG:
                    min_val = max(min_val, 1)

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
            # Handle booleans as true boolean values, not strings
            param = Categorical(
                name,
                [True, False],
                default=bool(knob_def.default),
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

    _add_configspace_constraints(cs, knob_space)

    return cs


def _add_configspace_constraints(cs: ConfigurationSpace, knob_space: KnobSpace) -> None:
    """
    Add logical constraints to the ConfigSpace dynamically from KnobSpace properties.

    These prevent SMAC from sampling invalid or conflicting configurations
    that would otherwise just be repaired, reducing wasted evaluations.
    """
    if not hasattr(knob_space, "configspace_constraints"):
        return

    for constraint in knob_space.configspace_constraints:
        ctype = constraint.get("type")
        if ctype == "not_equals":
            child, parent, val = constraint["child"], constraint["parent"], constraint["value"]
            if child in cs and parent in cs:
                cs.add(NotEqualsCondition(cs[child], cs[parent], val))
        elif ctype == "forbidden_and_in_equals":
            k1, v1, k2, v2 = constraint["knob1"], constraint["values1"], constraint["knob2"], constraint["value2"]
            if k1 in cs and k2 in cs:
                cs.add(ForbiddenAndConjunction(
                    ForbiddenInClause(cs[k1], v1),
                    ForbiddenEqualsClause(cs[k2], v2),
                ))
        elif ctype == "forbidden_less_than":
            left, right = constraint["left"], constraint["right"]
            if left in cs and right in cs:
                cs.add(ForbiddenLessThanRelation(cs[left], cs[right]))
        elif ctype == "forbidden_greater_than":
            left, right = constraint["left"], constraint["right"]
            if left in cs and right in cs:
                cs.add(ForbiddenGreaterThanRelation(cs[left], cs[right]))
        elif ctype == "forbidden_equals":
            knob, val = constraint["knob"], constraint["value"]
            if knob in cs:
                cs.add(ForbiddenEqualsClause(cs[knob], val))


def configspace_to_knobs(
    cs_config: Configuration,
    knob_space: KnobSpace,
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
        Knob configuration dictionary with proper Python types.
    """
    config_dict: Dict[str, Any] = {}

    auto_zero_knobs = knob_space.non_zero_knobs if hasattr(knob_space, "non_zero_knobs") else set()

    for knob_def in knob_space.knobs.values():
        name = knob_def.name

        if name not in cs_config:
            continue

        value = cs_config[name]

        # Reconstruct integer values from indices if a step > 1 was used
        if knob_def.knob_type == KnobType.INTEGER and knob_def.step and knob_def.step > 1:
            base = int(knob_def.min_value) if knob_def.min_value is not None else 0
            if name in auto_zero_knobs and base == 0:
                base = 1
            # Subtract the 1-index shift before multiplying by step
            value = base + (int(value) - 1) * knob_def.step

        # Convert numpy types to Python types
        if isinstance(value, np.integer):
            config_dict[name] = int(value)
        elif isinstance(value, np.floating):
            config_dict[name] = float(value)
        elif isinstance(value, (bool, np.bool_)):
            config_dict[name] = bool(value)
        elif isinstance(value, (int, float, str)):
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
    auto_zero_knobs = knob_space.non_zero_knobs if hasattr(knob_space, "non_zero_knobs") else set()

    for hp in list(configspace.values()):
        name = hp.name
        if name not in knob_config:
            continue

        val = knob_config[name]
        knob_def = knob_space.knobs.get(name)

        if knob_def and knob_def.knob_type == KnobType.INTEGER and knob_def.step and knob_def.step > 1:
            base = int(knob_def.min_value) if knob_def.min_value is not None else 0
            if name in auto_zero_knobs and base == 0:
                base = 1
            # Convert physical value back to categorical index representation, including the +1 shift
            val = ((int(val) - base) // knob_def.step) + 1

        # Ensure booleans are typed correctly for the CS categorical choices
        if knob_def and knob_def.knob_type == KnobType.BOOLEAN:
            # We already fixed boolean parsing in KnobDefinition.normalize_value,
            # so bool() here guarantees True/False type match
            val = bool(val)

        # Clamp to CS bounds for numeric types
        if isinstance(hp, IntegerHyperparameter):
            val = max(hp.lower, min(hp.upper, int(val)))
        elif isinstance(hp, FloatHyperparameter):
            val = max(hp.lower, min(hp.upper, float(val)))
        elif isinstance(hp, Constant):
            val = hp.value
        elif isinstance(hp, CategoricalHyperparameter):
            if val not in hp.choices:
                LOGGER.error(
                    f"Injection mismatch: Value '{val}' (type: {type(val).__name__}) "
                    f"not in valid choices {hp.choices} for categorical knob '{name}'."
                )
                raise ValueError(
                    f"Injection mismatch: Value '{val}' not in valid choices "
                    f"{hp.choices} for categorical knob '{name}'. Cannot warm-start."
                )

        values[name] = val

    # allow_inactive_with_values=True handles conditional HPs gracefully
    return Configuration(configspace, values=values, allow_inactive_with_values=True)


def build_env_context(knob_space: KnobSpace) -> Dict[str, Any]:
    """Build environment context dict from the knob space's hardware resources.

    Returns
    -------
    dict
        Keys: ``worker_ram_bytes``, ``worker_cpu_cores``,
        ``max_connections`` (from knob default or 100).
    """
    ctx: Dict[str, Any] = {}
    if knob_space.worker_resources:
        ctx["worker_ram_bytes"] = knob_space.worker_resources.ram_bytes
        ctx["worker_cpu_cores"] = knob_space.worker_resources.cpu_cores
    if "max_connections" in knob_space.knobs:
        ctx["max_connections"] = knob_space.knobs["max_connections"].default or 100
    return ctx
