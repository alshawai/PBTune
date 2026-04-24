"""Bayesian Optimization engine using SMAC as a blackbox."""

from typing import Any, Callable, Dict
import importlib

def _load_smac_symbols() -> tuple[Any, Any]:
    """Load SMAC symbols lazily to avoid static import failures."""
    smac_module = importlib.import_module("smac")
    return smac_module.HyperparameterOptimizationFacade, smac_module.Scenario

def _configuration_to_dict(configuration: Any) -> Dict[str, Any]:
    """Convert ConfigSpace/SMAC configuration objects to a plain dictionary."""
    if configuration is None:
        return {}

    if hasattr(configuration, "keys") and hasattr(configuration, "__getitem__"):
        try:
            return {key: configuration[key] for key in configuration.keys()}
        except Exception:
            pass

    if hasattr(configuration, "get_dictionary"):
        return dict(configuration.get_dictionary())

    if hasattr(configuration, "get"):
        maybe_items = configuration.get("items")
        if maybe_items is not None:
            return dict(configuration)

    if isinstance(configuration, dict):
        return dict(configuration)

    return dict(configuration)

def _extract_runhistory_entries(runhistory: Any) -> list[dict[str, Any]]:
    """Extract evaluation history entries from SMAC runhistory in a robust way."""
    history: list[dict[str, Any]] = []
    ids_config = getattr(runhistory, "ids_config", {})

    items: list[tuple[Any, Any]] = []
    if hasattr(runhistory, "items"):
        try:
            items = list(runhistory.items())
        except Exception:
            items = []

    # Backward-compatible fallback for alternative runhistory layouts.
    if not items:
        data = getattr(runhistory, "data", {})
        if hasattr(data, "items"):
            items = list(data.items())

    for index, (run_key, run_value) in enumerate(items, start=1):
        config_id = getattr(run_key, "config_id", None)
        configuration = ids_config.get(config_id)
        config_dict = _configuration_to_dict(configuration) if configuration is not None else {}

        cost = float(getattr(run_value, "cost", float("nan")))
        score = -cost
        eval_time = float(getattr(run_value, "time", 0.0))
        start_time = float(getattr(run_value, "starttime", 0.0))
        end_time = float(getattr(run_value, "endtime", 0.0))
        status = str(getattr(run_value, "status", "UNKNOWN"))
        additional_info = getattr(run_value, "additional_info", None)

        history.append(
            {
                "iteration": index,
                "config": config_dict,
                "cost": cost,
                "score": score,
                "evaluation_time_seconds": eval_time,
                "start_time_seconds": start_time,
                "end_time_seconds": end_time,
                "status": status,
                "additional_info": additional_info,
            }
        )

    return history


class BOEngine:
    """
    A blackbox Bayesian Optimization engine wrapping SMAC.
    """
    def __init__(
        self,
        config_space: Any,
        objective_function: Callable[[Any, int], float],
        max_evaluations: int,
        initial_design_size: int,
        seed: int = 42,
    ):
        self.config_space = config_space
        self.objective_function = objective_function
        self.max_evaluations = max_evaluations
        self.initial_design_size = initial_design_size
        self.seed = seed

    def optimize(self) -> tuple[Dict[str, Any], float, list[dict[str, Any]]]:
        """
        Runs the optimization loop.
        Returns:
            - The best configuration as a plain dictionary.
            - The best cost (score negation) found.
            - The list of evaluation histories.
        """
        HyperparameterOptimizationFacade, Scenario = _load_smac_symbols()

        scenario = Scenario(
            configspace=self.config_space,
            deterministic=True,
            n_trials=self.max_evaluations,
            seed=self.seed,
        )

        initial_design = HyperparameterOptimizationFacade.get_initial_design(
            scenario=scenario,
            n_configs=self.initial_design_size,
        )

        smac = HyperparameterOptimizationFacade(
            scenario=scenario,
            target_function=self.objective_function,
            initial_design=initial_design,
            overwrite=True,
        )

        incumbent = smac.optimize()
        runhistory = smac.runhistory
        history = _extract_runhistory_entries(runhistory)

        incumbent_dict = _configuration_to_dict(incumbent)
        best_cost = None
        if hasattr(runhistory, "get_cost"):
            try:
                best_cost = float(runhistory.get_cost(incumbent))
            except Exception:
                best_cost = None
                
        if best_cost is None and history:
            best_cost = min(entry["cost"] for entry in history)

        return incumbent_dict, (best_cost if best_cost is not None else float("inf")), history
