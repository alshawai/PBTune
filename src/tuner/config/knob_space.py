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

    def perturb_config(
        self,
        config: Dict[str, Any],
        perturbation_factor: Tuple[float, float] = (0.8, 1.2),
        seed: Optional[int] = None
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
            
        Returns
        -------
        Dict[str, Any]
            Perturbed configuration
        """
        rng = np.random.default_rng(seed)
        perturbed = {}

        for knob_name, value in config.items():
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

            elif knob_def.knob_type in [KnobType.BOOLEAN, KnobType.ENUM]:
                # Resample categorical with 20% probability
                if rng.random() < 0.2:
                    perturbed[knob_name] = knob_def.sample_random_value(rng)
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
