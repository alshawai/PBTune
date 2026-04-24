"""Interface wrapper for utilizing BO algorithms seamlessly against the PBT pipeline."""

from typing import Any, Dict
import importlib

import numpy as np

from src.tuner.config import KnobDefinition, KnobScale, KnobType, KnobSpace
from src.tuner.core.worker import Worker
from src.tuner.evaluator.evaluator import Evaluator


def convert_numpy_types(obj: Any) -> Any:
    """Recursively convert numpy values for uniform representation with PBT JSON formats."""
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, (np.integer, np.int64, np.int32, np.int16, np.int8)):
        return int(obj)
    if isinstance(obj, (np.floating, np.float64, np.float32, np.float16)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, dict):
        return {key: convert_numpy_types(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [convert_numpy_types(item) for item in obj]
    return obj


def _load_configspace_symbols() -> tuple[Any, Any, Any, Any]:
    """Load ConfigSpace symbols lazily."""
    configspace_module = importlib.import_module("ConfigSpace")
    hyperparameters_module = importlib.import_module("ConfigSpace.hyperparameters")

    return (
        configspace_module.ConfigurationSpace,
        hyperparameters_module.UniformIntegerHyperparameter,
        hyperparameters_module.UniformFloatHyperparameter,
        hyperparameters_module.CategoricalHyperparameter,
    )


def knob_to_hyperparameter(knob: KnobDefinition) -> Any:
    """Converts a standard KnobDefinition to a BO-compatible hyperparameter."""
    _, IntegerHyperparameter, FloatHyperparameter, CategoricalHyperparameter = _load_configspace_symbols()

    if knob.knob_type == KnobType.INTEGER:
        if knob.min_value is None or knob.max_value is None:
            raise ValueError(f"Integer knob '{knob.name}' is missing min/max bounds.")

        return IntegerHyperparameter(
            name=knob.name,
            lower=int(knob.min_value),
            upper=int(knob.max_value),
            log=(knob.scale == KnobScale.LOG and float(knob.min_value) > 0),
        )

    if knob.knob_type == KnobType.REAL:
        if knob.min_value is None or knob.max_value is None:
            raise ValueError(f"Real knob '{knob.name}' is missing min/max bounds.")

        return FloatHyperparameter(
            name=knob.name,
            lower=float(knob.min_value),
            upper=float(knob.max_value),
            log=(knob.scale == KnobScale.LOG and float(knob.min_value) > 0),
        )

    if knob.knob_type == KnobType.ENUM:
        if not knob.enum_values:
            raise ValueError(f"Enum knob '{knob.name}' is missing enum values.")
        return CategoricalHyperparameter(name=knob.name, choices=list(knob.enum_values))

    if knob.knob_type == KnobType.BOOLEAN:
        choices = list(knob.enum_values) if knob.enum_values else [True, False]
        return CategoricalHyperparameter(name=knob.name, choices=choices)

    raise ValueError(f"Unsupported knob type '{knob.knob_type}' for knob '{knob.name}'.")


def build_configspace(knob_space: KnobSpace, seed: int | None = None) -> Any:
    """Creates a BO configuration space defining standard inputs equal to PBT environments."""
    ConfigurationSpace, _, _, _ = _load_configspace_symbols()
    config_space = ConfigurationSpace(seed=seed)
    
    hyperparameters = []
    for knob_name in knob_space.get_knob_names():
        hyperparameters.append(knob_to_hyperparameter(knob_space[knob_name]))

    config_space.add_hyperparameters(hyperparameters)
    return config_space


class PBTObjectiveAdapter:
    """
    Adapter converting BO parameters back to generic project models 
    and yielding comparable cost functions mirroring PBT behavior securely.
    """
    def __init__(self, evaluator: Evaluator, worker: Worker, logger: Any):
        self.evaluator = evaluator
        self.worker = worker
        self.logger = logger
        self.evaluations_count = 0

    def evaluate(self, config_dict: Dict[str, Any]) -> tuple[Any, float]:
        self.worker.knob_config = config_dict
        if hasattr(self.evaluator, "evaluate_worker"):
            self.logger.info("Evaluating configuration #%d", self.evaluations_count + 1)
            metrics, score, _ = self.evaluator.evaluate_worker(self.worker, apply_config=True)
            self.evaluations_count += 1
            if metrics:
                self.logger.info(f"Score received: {score:.4f}, metrics: {metrics}")
            return metrics, score
        else:
            result = self.evaluator.evaluate(config_dict)
            self.evaluations_count += 1
            return result[0], float(result[1])

    def bo_objective_function(self, configuration: Any, seed: int = 0) -> float:
        """Target cost function conforming exactly strictly to SMAC BO needs (cost negated)."""
        if hasattr(configuration, "get_dictionary"):
            config_dict = dict(configuration.get_dictionary())
        else:
            config_dict = dict(configuration)

        _, score = self.evaluate(config_dict)
        return -float(score)
