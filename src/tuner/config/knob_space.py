"""
Knob Space Definition for PostgreSQL Configuration Tuning
=========================================================

This module defines the search space for PostgreSQL configuration parameters.
Each knob has:
- Type (integer, real, boolean, enum)
- Valid range or values
- Scale (linear, log, categorical)
- Default value
- Unit (for display and conversion)

The knob space is used by PBT to:
1. Sample initial configurations
2. Validate configurations
3. Perturb configurations during exploration
4. Normalize values for optimization

Predefined Knob Sets:
--------------------
- MINIMAL_KNOBS: 5 most impactful knobs (for rapid prototyping)
- CORE_KNOBS: 13 critical knobs (standard tuning)
- STANDARD_KNOBS: ~30 knobs (comprehensive tuning)
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union, Tuple
from enum import Enum
import numpy as np


class KnobType(Enum):
    """Types of PostgreSQL configuration parameters"""
    INTEGER = "integer"
    REAL = "real"
    BOOLEAN = "boolean"
    ENUM = "enum"


class KnobScale(Enum):
    """Scale types for numerical knobs"""
    LINEAR = "linear"
    LOG = "log"
    CATEGORICAL = "categorical"


@dataclass
class KnobDefinition:
    """
    Definition of a single PostgreSQL configuration knob.
    
    Attributes
    ----------
    name : str
        PostgreSQL parameter name (e.g., 'shared_buffers')
    knob_type : KnobType
        Data type of the knob
    min_value : Optional[Union[int, float]]
        Minimum value (for numeric types)
    max_value : Optional[Union[int, float]]
        Maximum value (for numeric types)
    scale : KnobScale
        Distribution scale (linear or log)
    default : Any
        Default value from PostgreSQL
    unit : Optional[str]
        Unit for display (kB, MB, ms, etc.)
    enum_values : Optional[List[str]]
        Valid values for ENUM type
    description : str
        Human-readable description
    category : str
        Functional category (memory, planner, etc.)
    restart_required : bool
        Whether changing requires PostgreSQL restart
    """

    name: str
    knob_type: KnobType
    min_value: Optional[Union[int, float]] = None
    max_value: Optional[Union[int, float]] = None
    scale: KnobScale = KnobScale.LINEAR
    default: Any = None
    unit: Optional[str] = None
    enum_values: Optional[List[str]] = None
    description: str = ""
    category: str = "other"
    restart_required: bool = False

    def validate_value(self, value: Any) -> bool:
        """
        Validate if a value is valid for this knob.
        
        Parameters
        ----------
        value : Any
            Value to validate
            
        Returns
        -------
        bool
            True if valid, False otherwise
        """
        if self.knob_type == KnobType.INTEGER:
            if not isinstance(value, (int, np.integer)):
                return False
            if self.min_value is not None and value < self.min_value:
                return False
            if self.max_value is not None and value > self.max_value:
                return False
            return True

        elif self.knob_type == KnobType.REAL:
            if not isinstance(value, (int, float, np.number)):
                return False
            if self.min_value is not None and value < self.min_value:
                return False
            if self.max_value is not None and value > self.max_value:
                return False
            return True

        elif self.knob_type == KnobType.BOOLEAN:
            return isinstance(value, bool)

        elif self.knob_type == KnobType.ENUM:
            if self.enum_values is None:
                return False
            return value in self.enum_values

        return False

    def sample_random_value(self, rng: Optional[np.random.Generator] = None) -> Any:
        """
        Sample a random valid value for this knob.
        
        Parameters
        ----------
        rng : Optional[np.random.Generator]
            Random number generator (for reproducibility)
            
        Returns
        -------
        Any
            Random valid value
        """
        if rng is None:
            rng = np.random.default_rng()

        if self.knob_type == KnobType.INTEGER:
            if self.scale == KnobScale.LOG:
                log_min = np.log(self.min_value)  # type: ignore
                log_max = np.log(self.max_value)  # type: ignore
                log_value = rng.uniform(log_min, log_max)
                return int(np.exp(log_value))
            else:
                return rng.integers(self.min_value, self.max_value + 1)  # type: ignore

        elif self.knob_type == KnobType.REAL:
            if self.scale == KnobScale.LOG:
                log_min = np.log(self.min_value)  # type: ignore
                log_max = np.log(self.max_value)  # type: ignore
                log_value = rng.uniform(log_min, log_max)
                return float(np.exp(log_value))
            else:
                return float(rng.uniform(self.min_value, self.max_value))  # type: ignore

        elif self.knob_type == KnobType.BOOLEAN:
            return bool(rng.choice([True, False]))

        elif self.knob_type == KnobType.ENUM:
            return rng.choice(self.enum_values)  # type: ignore

        return self.default


class KnobSpace:
    """
    Defines the search space for PostgreSQL knobs.
    
    This class manages the collection of knobs that will be tuned,
    provides sampling and validation utilities, and handles normalization.
    
    Attributes
    ----------
    knobs : Dict[str, KnobDefinition]
        Dictionary mapping knob names to their definitions
    """

    def __init__(self, knob_definitions: List[KnobDefinition]):
        """
        Initialize knob space.
        
        Parameters
        ----------
        knob_definitions : List[KnobDefinition]
            List of knob definitions
        """
        self.knobs = {knob.name: knob for knob in knob_definitions}

    def __len__(self) -> int:
        """Return number of knobs in the space"""
        return len(self.knobs)

    def __contains__(self, knob_name: str) -> bool:
        """Check if a knob is in the space"""
        return knob_name in self.knobs

    def __getitem__(self, knob_name: str) -> KnobDefinition:
        """Get knob definition by name"""
        return self.knobs[knob_name]

    def get_restart_required_knobs(self) -> List[str]:
        """
        Get list of knobs that require PostgreSQL restart.
        
        Returns
        -------
        List[str]
            List of knob names requiring restart
        """
        return [name for name, defn in self.knobs.items() if defn.restart_required]

    def get_runtime_modifiable_knobs(self) -> List[str]:
        """
        Get list of knobs that can be modified at runtime.
        
        Returns
        -------
        List[str]
            List of knob names that can be changed without restart
        """
        return [name for name, defn in self.knobs.items() if not defn.restart_required]

    def split_config_by_restart_requirement(
        self, config: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Split configuration into restart-required and runtime-modifiable parts.
        
        Parameters
        ----------
        config : Dict[str, Any]
            Full configuration
        
        Returns
        -------
        Tuple[Dict[str, Any], Dict[str, Any]]
            (restart_required_config, runtime_modifiable_config)
        """
        restart_config = {}
        runtime_config = {}

        for knob_name, value in config.items():
            if knob_name in self.knobs:
                if self.knobs[knob_name].restart_required:
                    restart_config[knob_name] = value
                else:
                    runtime_config[knob_name] = value

        return restart_config, runtime_config

    def validate_config(self, config: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Validate a configuration.
        
        Parameters
        ----------
        config : Dict[str, Any]
            Configuration to validate
            
        Returns
        -------
        Tuple[bool, List[str]]
            (is_valid, error_messages)
        """
        errors = []

        for knob_name in config.keys():
            if knob_name not in self.knobs:
                errors.append(f"Unknown knob: {knob_name}")

        for knob_name in self.knobs.keys():
            if knob_name not in config:
                errors.append(f"Missing knob: {knob_name}")

        for knob_name, value in config.items():
            if not self.knobs[knob_name].validate_value(value):
                errors.append(
                    f"Invalid value for {knob_name}: {value} "
                    f"(expected type: {self.knobs[knob_name].knob_type.value})"
                )

        return (len(errors) == 0, errors)

    def sample_random_config(self, seed: Optional[int] = None) -> Dict[str, Any]:
        """
        Sample a random configuration.
        
        Parameters
        ----------
        seed : Optional[int]
            Random seed for reproducibility
            
        Returns
        -------
        Dict[str, Any]
            Random configuration
        """
        rng = np.random.default_rng(seed)
        config = {}

        for knob_name, knob_def in self.knobs.items():
            config[knob_name] = knob_def.sample_random_value(rng)

        return config

    def sample_diverse_configs(
        self,
        num_samples: int,
        seed: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Sample diverse configurations using Latin Hypercube Sampling (LHS).
        
        LHS ensures better coverage of the search space compared to pure random sampling,
        reducing the likelihood of early convergence due to similar initial configurations.
        
        Parameters
        ----------
        num_samples : int
            Number of configurations to sample
        seed : Optional[int]
            Random seed for reproducibility
            
        Returns
        -------
        List[Dict[str, Any]]
            List of diverse configurations
            
        Notes
        -----
        For numerical knobs (INTEGER, REAL):
        - Divides the range into num_samples equal intervals
        - Samples once from each interval
        - Randomly permutes the samples across dimensions
        
        For categorical knobs (BOOLEAN, ENUM):
        - Uses stratified sampling when possible
        - Falls back to random sampling for small populations
        """
        rng = np.random.default_rng(seed)
        configs = []

        numerical_knobs = [
            (name, defn) for name, defn in self.knobs.items()
            if defn.knob_type in (KnobType.INTEGER, KnobType.REAL)
        ]
        categorical_knobs = [
            (name, defn) for name, defn in self.knobs.items()
            if defn.knob_type in (KnobType.BOOLEAN, KnobType.ENUM)
        ]

        lhs_samples = {}
        for knob_name, knob_def in numerical_knobs:
            intervals = np.linspace(0, 1, num_samples + 1)
            samples = []

            for i in range(num_samples):
                u = rng.uniform(intervals[i], intervals[i + 1])

                if knob_def.scale == KnobScale.LOG:
                    log_min = np.log(knob_def.min_value)  # type: ignore
                    log_max = np.log(knob_def.max_value)  # type: ignore
                    log_value = log_min + u * (log_max - log_min)
                    value = np.exp(log_value)

                    if knob_def.knob_type == KnobType.INTEGER:
                        value = int(value)
                    else:
                        value = float(value)
                else:
                    value = knob_def.min_value + u * (
                        knob_def.max_value - knob_def.min_value
                        )  # type: ignore

                    if knob_def.knob_type == KnobType.INTEGER:
                        value = int(value)
                    else:
                        value = float(value)

                samples.append(value)

            # Randomly permute samples for this dimension
            rng.shuffle(samples)
            lhs_samples[knob_name] = samples

        categorical_samples = {}
        for knob_name, knob_def in categorical_knobs:
            if knob_def.knob_type == KnobType.BOOLEAN:
                # Alternate True/False, then shuffle
                samples = [True, False] * (num_samples // 2)
                if num_samples % 2 == 1:
                    samples.append(rng.choice([True, False]))
                rng.shuffle(samples)
            else:
                # For ENUM: stratified sampling if enough values, else random
                enum_values = knob_def.enum_values
                if enum_values and len(enum_values) >= num_samples // 2:
                    samples = []
                    for i in range(num_samples):
                        samples.append(enum_values[i % len(enum_values)])
                    rng.shuffle(samples)
                else:
                    # Too few enum values or None, use random sampling
                    samples = [
                        knob_def.sample_random_value(rng)
                        for _ in range(num_samples)
                    ]

            categorical_samples[knob_name] = samples

        for i in range(num_samples):
            config = {}

            for knob_name, _ in numerical_knobs:
                config[knob_name] = lhs_samples[knob_name][i]

            for knob_name, _ in categorical_knobs:
                config[knob_name] = categorical_samples[knob_name][i]

            configs.append(config)

        return configs

    def perturb_config(
        self,
        config: Dict[str, Any],
        perturbation_factor: Tuple[float, float] = (0.8, 1.2),
        seed: Optional[int] = None,
        exclude_knobs: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Perturb a configuration (PBT exploration step).
        
        For numerical knobs: Multiply by random factor from perturbation_factor range
        For categorical knobs: Randomly resample with some probability
        
        Parameters
        ----------
        config : Dict[str, Any]
            Original configuration
        perturbation_factor : Tuple[float, float]
            (min_factor, max_factor) for perturbation. Default (0.8, 1.2) means ±20%
        seed : Optional[int]
            Random seed
        exclude_knobs : Optional[List[str]]
            List of knob names to exclude from perturbation (keep unchanged)
            
        Returns
        -------
        Dict[str, Any]
            Perturbed configuration
        """
        rng = np.random.default_rng(seed)
        perturbed = {}
        exclude_set = set(exclude_knobs or [])

        for knob_name, value in config.items():
            if knob_name in exclude_set:
                perturbed[knob_name] = value
                continue

            knob_def = self.knobs[knob_name]

            if knob_def.knob_type == KnobType.INTEGER:
                factor = rng.uniform(perturbation_factor[0], perturbation_factor[1])
                new_value = int(value * factor)

                # Ensuring valid range...
                if knob_def.min_value is not None:
                    new_value = max(new_value, knob_def.min_value)
                if knob_def.max_value is not None:
                    new_value = min(new_value, knob_def.max_value)

                perturbed[knob_name] = new_value

            elif knob_def.knob_type == KnobType.REAL:
                factor = rng.uniform(perturbation_factor[0], perturbation_factor[1])
                new_value = value * factor

                if knob_def.min_value is not None:
                    new_value = max(new_value, knob_def.min_value)
                if knob_def.max_value is not None:
                    new_value = min(new_value, knob_def.max_value)

                perturbed[knob_name] = new_value

            elif knob_def.knob_type == KnobType.BOOLEAN:
                # For boolean: higher probability (30%) since only 2 values
                # When perturbed, always flip (deterministic neighborhood)
                if rng.random() < 0.3:
                    perturbed[knob_name] = not value
                else:
                    perturbed[knob_name] = value

            elif knob_def.knob_type == KnobType.ENUM:
                # Proportional perturbation based on cardinality
                # More options → lower probability to maintain diversity
                # Fewer options → higher probability to explore thoroughly
                enum_count = len(knob_def.enum_values) if knob_def.enum_values else 2
                perturb_prob = min(0.4, 2.0 / enum_count)

                if rng.random() < perturb_prob:
                    # Neighborhood sampling: choose from OTHER values only
                    # This ensures we actually explore when we perturb
                    if knob_def.enum_values and len(knob_def.enum_values) > 1:
                        other_values = [v for v in knob_def.enum_values if v != value]
                        perturbed[knob_name] = rng.choice(other_values)
                    else:
                        # Fallback for degenerate case
                        perturbed[knob_name] = value
                else:
                    perturbed[knob_name] = value

        return perturbed

    def get_default_config(self) -> Dict[str, Any]:
        """
        Get default configuration (PostgreSQL defaults).
        
        Returns
        -------
        Dict[str, Any]
            Default configuration
        """
        return {knob_name: knob_def.default for knob_name, knob_def in self.knobs.items()}

    def get_knob_names(self) -> List[str]:
        """Get list of all knob names"""
        return list(self.knobs.keys())

    def get_knobs_by_category(self, category: str) -> List[str]:
        """Get list of knob names in a specific category"""
        return [name for name, knob in self.knobs.items() if knob.category == category]
