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
from src.tuner.utils import WorkerResources
from src.tuner.utils import get_logger

logger = get_logger(__name__)


# Hardware-relative specifications for converting fractions to absolute values
HARDWARE_RELATIVE_SPECS = {
    # RAM-Relative Knobs: (Fraction Min, Fraction Max)
    "shared_buffers": (0.15, 0.40),
    "effective_cache_size": (0.40, 0.75),
    "work_mem": (0.001, 0.02),
    "maintenance_work_mem": (0.01, 0.05),

    # CPU-Relative Knobs: (Fraction Min, Fraction Max, Floor Min)
    "max_worker_processes": (0.50, 2.0, 4),
    "max_parallel_workers": (0.25, 1.0, 0),
    "max_parallel_workers_per_gather": (0.0, 0.50, 0),
    "max_parallel_maintenance_workers": (0.0, 0.50, 0),
    "io_workers": (0.125, 1.0, 1),
}


# Disk-Type-Conditional Ranges (absolute values, not fractions)
DISK_TYPE_CONDITIONAL_RANGES = {
    # SSD Range, HDD Range, Unknown Range
    "effective_io_concurrency": {"SSD": (100, 200), "HDD": (1, 4), "unknown": (1, 200)},
    "maintenance_io_concurrency": {"SSD": (100, 200), "HDD": (1, 4), "unknown": (1, 200)},
    "random_page_cost": {"SSD": (1.0, 1.5), "HDD": (3.0, 4.0), "unknown": (0.1, 4.0)},
    "seq_page_cost": {"SSD": (0.1, 1.0), "HDD": (1.0, 2.0), "unknown": (0.1, 2.0)},
}


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
    step : Optional[int]
        Optional discrete step alignment for integer knobs
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
    step: Optional[int] = None
    hardware_relative: bool = False
    resource_type: Optional[str] = None  # "ram", "cpu", or "disk_type"

    def _normalize_integer(self, value: Any) -> int:
        """Clamp and align integer values to the valid discrete grid."""
        normalized = int(value)

        if self.min_value is not None:
            normalized = max(normalized, int(self.min_value))
        if self.max_value is not None:
            normalized = min(normalized, int(self.max_value))

        if self.step and self.step > 1:
            base = int(self.min_value) if self.min_value is not None else 0
            offset = normalized - base
            normalized = base + int(round(offset / self.step)) * self.step

            if self.min_value is not None:
                normalized = max(normalized, int(self.min_value))
            if self.max_value is not None:
                normalized = min(normalized, int(self.max_value))

        return normalized

    def normalize_value(self, value: Any) -> Any:
        """Normalize a candidate value into this knob's valid domain."""
        if self.knob_type == KnobType.INTEGER:
            return self._normalize_integer(value)

        if self.knob_type == KnobType.REAL:
            normalized = float(value)
            if self.min_value is not None:
                normalized = max(normalized, float(self.min_value))
            if self.max_value is not None:
                normalized = min(normalized, float(self.max_value))
            return normalized

        if self.knob_type == KnobType.BOOLEAN:
            return bool(value)

        if self.knob_type == KnobType.ENUM:
            if self.enum_values is None:
                return value
            return str(value)

        return value

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
            if self.step and self.step > 1:
                base = int(self.min_value) if self.min_value is not None else 0
                if (int(value) - base) % self.step != 0:
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
            if self.scale == KnobScale.LOG and self.min_value is not None and self.min_value > 0:
                log_min = np.log(self.min_value)  # type: ignore
                log_max = np.log(self.max_value)  # type: ignore
                log_value = rng.uniform(log_min, log_max)
                return self._normalize_integer(np.exp(log_value))

            if self.step and self.step > 1:
                min_value = int(self.min_value)  # type: ignore
                max_value = int(self.max_value)  # type: ignore
                num_steps = ((max_value - min_value) // self.step) + 1
                step_index = int(rng.integers(0, num_steps))
                return min_value + step_index * self.step

            return int(rng.integers(self.min_value, self.max_value + 1))  # type: ignore

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
            return str(rng.choice(self.enum_values))  # type: ignore

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
        self.worker_resources: Optional[WorkerResources] = None

    def _get_bytes_per_unit(self, knob: "KnobDefinition") -> int:
        """Parse unit string to bytes."""
        if knob.unit == "8kB":
            return 8192
        elif knob.unit == "kB":
            return 1024
        elif knob.unit == "MB":
            return 1024 * 1024
        elif knob.unit == "GB":
            return 1024 * 1024 * 1024
        return 1

    def resolve_hardware_ranges(self, resources: WorkerResources) -> None:
        """Override min/max for hardware-relative knobs using detected resources."""
        self.worker_resources = resources

        for name, knob in self.knobs.items():
            if not knob.hardware_relative:
                continue

            if knob.resource_type == "ram" and name in HARDWARE_RELATIVE_SPECS:
                f_min, f_max = HARDWARE_RELATIVE_SPECS[name]  # type: ignore
                bytes_per_unit = self._get_bytes_per_unit(knob)
                knob.min_value = int(resources.ram_bytes * f_min / bytes_per_unit)
                knob.max_value = int(resources.ram_bytes * f_max / bytes_per_unit)
                logger.debug(
                    "Resolved %s range to [%s, %s] for %s %s",
                    name,
                    knob.min_value,
                    knob.max_value,
                    int(resources.ram_bytes / bytes_per_unit),
                    knob.unit or "units"
                )

            elif knob.resource_type == "cpu" and name in HARDWARE_RELATIVE_SPECS:
                f_min, f_max, floor_min = HARDWARE_RELATIVE_SPECS[name]  # type: ignore
                knob.min_value = max(floor_min, round(resources.cpu_cores * f_min))
                knob.max_value = max(floor_min, round(resources.cpu_cores * f_max))
                logger.debug(
                    "Resolved %s range to [%s, %s] for %s CPUs",
                    name,
                    knob.min_value,
                    knob.max_value,
                    resources.cpu_cores
                )

            elif knob.resource_type == "disk_type" and name in DISK_TYPE_CONDITIONAL_RANGES:
                ranges = DISK_TYPE_CONDITIONAL_RANGES[name]
                disk_type = resources.disk_type if resources.disk_type in ranges else "unknown"
                min_val, max_val = ranges[disk_type]
                knob.min_value = min_val
                knob.max_value = max_val
                logger.debug(
                    "Resolved %s range to [%s, %s] for %s disk",
                    name,
                    min_val,
                    max_val,
                    disk_type
                )

    def config_to_fractions(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert absolute config values to fractional representation for serialization.
        
        Parameters
        ----------
        config : Dict[str, Any]
            Absolute configuration values
            
        Returns
        -------
        Dict[str, Any]
            Fractional configuration values

        Notes
        -----
        - Hardware-relative knobs are converted to fractions of total resources
        - Non-hardware-relative knobs are kept as absolute values
        - Returns a dict with the same keys as the input config (even for knobs not in the knob space)
        """
        fractional_config = {}
        for name, value in config.items():
            if name not in self.knobs:
                fractional_config[name] = value
                continue

            knob = self.knobs[name]
            if (
                not knob.hardware_relative
                or knob.resource_type == "disk_type"
                or self.worker_resources is None
            ):
                fractional_config[name] = value
                continue

            if knob.resource_type == "ram" and name in HARDWARE_RELATIVE_SPECS:
                bytes_per_unit = self._get_bytes_per_unit(knob)
                fraction = (value * bytes_per_unit) / self.worker_resources.ram_bytes
                fractional_config[name] = fraction

            elif knob.resource_type == "cpu":
                fraction = value / self.worker_resources.cpu_cores
                fractional_config[name] = fraction
            else:
                fractional_config[name] = value

        return fractional_config

    def fractions_to_config(self, fractions: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert fractional representation back to absolute values for this hardware.
        
        Parameters
        ----------
        fractions : Dict[str, Any]
            Fractional configuration values
            
        Returns
        -------
        Dict[str, Any]
            Absolute configuration values
        
        Notes
        -----
        - Hardware-relative knobs are converted to absolute values based on total resources
        - Non-hardware-relative knobs are kept as absolute values
        - Returns a dict with the same keys as the input config (even for knobs not in the knob space)
        """
        config = {}
        for name, frac_val in fractions.items():
            if name not in self.knobs:
                config[name] = frac_val
                continue

            knob = self.knobs[name]
            # Disk type knobs are absolute in fraction representation
            if (
                not knob.hardware_relative or
                knob.resource_type == "disk_type" or
                self.worker_resources is None
            ):
                config[name] = frac_val
                continue

            if knob.resource_type == "ram" and name in HARDWARE_RELATIVE_SPECS:
                bytes_per_unit = self._get_bytes_per_unit(knob)
                abs_val = (frac_val * self.worker_resources.ram_bytes) / bytes_per_unit
                config[name] = knob.normalize_value(abs_val)

            elif knob.resource_type == "cpu":
                abs_val = frac_val * self.worker_resources.cpu_cores
                config[name] = knob.normalize_value(abs_val)
            else:
                config[name] = frac_val

        return config

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

    def _repair_memory_budget(
        self,
        config: Dict[str, Any],
        budget_bytes: int
    ) -> Dict[str, Any]:
        """Proportionally scale down memory allocations to fit budget."""
        sb_bytes = config.get("shared_buffers", 0) * 8192
        wm_bytes = config.get("work_mem", 0) * 1024
        mwm_bytes = config.get("maintenance_work_mem", 0) * 1024
        max_conn = config.get("max_connections", 100)

        total = sb_bytes + (max_conn * wm_bytes) + mwm_bytes

        if total <= budget_bytes:
            return config

        scale = budget_bytes / total   # < 1.0

        # Scale all memory allocations uniformly (preserves ratios)
        if "shared_buffers" in config and "shared_buffers" in self.knobs:
            config["shared_buffers"] = self.knobs[
                "shared_buffers"].normalize_value(config["shared_buffers"] * scale)
        if "work_mem" in config and "work_mem" in self.knobs:
            config["work_mem"] = self.knobs[
                "work_mem"].normalize_value(config["work_mem"] * scale)
        if "maintenance_work_mem" in config and "maintenance_work_mem" in self.knobs:
            config["maintenance_work_mem"] = self.knobs[
                "maintenance_work_mem"].normalize_value(config["maintenance_work_mem"] * scale)
        if "max_connections" in config and "max_connections" in self.knobs:
            config["max_connections"] = self.knobs[
                "max_connections"].normalize_value(config["max_connections"] * scale)

        return config

    def repair_config_dependencies(
            self,
            config: Dict[str, Any],
            worker_id: Optional[int] = None,
            budget_ram_bytes: Optional[int] = None
        ) -> Dict[str, Any]:
        """
        Repair configuration to satisfy known dependencies and constraints between knobs.
        
        Parameters
        ----------
        config : Dict[str, Any]
            Configuration to repair
        worker_id : Optional[int]
            ID of the worker (for logging)
        budget_ram_bytes : Optional[int]
            Memory budget in bytes

        Returns
        -------
        Dict[str, Any]
            Repaired configuration
        
        Notes
        -----
        This method applies a series of heuristic rules to fix common invalid combinations of knobs.
        It also ensures that any repaired numerical values are normalized to valid discrete steps and bounds.
        If a memory budget is provided, it will also adjust memory-related knobs to fit within the budget.
        """
        worker_logger = (
            get_logger(__name__, worker_id=worker_id)
            if worker_id is not None else logger
        )
        repaired = dict(config)

        wal_level = repaired.get("wal_level")
        if wal_level == "minimal":
            if repaired.get("archive_mode") in {"on", "always"}:
                worker_logger.warning(
                    "[REPAIR] Corrected 'archive_mode' from '%s' to 'off' "
                    "because wal_level is 'minimal'",
                    repaired["archive_mode"]
                )
                repaired["archive_mode"] = "off"

            max_wal_senders = repaired.get("max_wal_senders")
            if isinstance(
                max_wal_senders,
                (int, np.integer, float, np.floating)
            ) and max_wal_senders > 0:
                worker_logger.warning(
                    "[REPAIR] Corrected 'max_wal_senders' from %s to 0 "
                    "because wal_level is 'minimal'",
                    repaired["max_wal_senders"]
                )
                repaired["max_wal_senders"] = 0

            summarize_wal = repaired.get("summarize_wal")
            if str(summarize_wal).lower() in {"on", "true", "1"}:
                worker_logger.warning(
                    "[REPAIR] Corrected 'summarize_wal' from '%s' to 'off' "
                    "because wal_level is 'minimal'",
                    repaired["summarize_wal"]
                )
                repaired["summarize_wal"] = "off"

        # Handle huge_pages OS constraints
        huge_pages = repaired.get("huge_pages")
        if huge_pages in {"on", "try"}:
            # huge_pages is not supported with sysv shared_memory_type
            if repaired.get("shared_memory_type") == "sysv":
                worker_logger.warning(
                    "[REPAIR] Corrected 'shared_memory_type' from 'sysv' to 'mmap' "
                    "because huge_pages is '%s'",
                    huge_pages
                )
                repaired["shared_memory_type"] = "mmap"

            # If huge_pages="on" in standard envs without configured huge pages,
            # the process strictly crashes. Convert "on" to "try" to allow graceful
            # fallback but retain performance if possible.
            if huge_pages == "on":
                worker_logger.warning(
                    "[REPAIR] Corrected 'huge_pages' from 'on' to 'try' "
                    "to allow graceful OS fallback"
                )
                repaired["huge_pages"] = "try"

        # Handle max_worker_processes vs max_parallel_workers constraints
        max_worker = repaired.get("max_worker_processes")
        max_parallel = repaired.get("max_parallel_workers")
        if (
            isinstance(max_worker, (int, float, np.number)) and
            isinstance(max_parallel, (int, float, np.number))
        ):
            if max_worker < max_parallel:
                worker_logger.warning(
                    "[REPAIR] Corrected 'max_parallel_workers' from %s to %s "
                    "because it cannot exceed 'max_worker_processes'",
                    max_parallel, max_worker
                )
                repaired["max_parallel_workers"] = int(max_worker)

        # Handle min_wal_size vs max_wal_size constraint
        min_wal = repaired.get("min_wal_size")
        max_wal = repaired.get("max_wal_size")
        if (
            isinstance(min_wal, (int, float, np.number)) and
            isinstance(max_wal, (int, float, np.number))
        ):
            if min_wal > max_wal:
                new_min_wal = max(1, int(max_wal) // 2)  # Give it a reasonable gap
                worker_logger.warning(
                    "[REPAIR] Corrected 'min_wal_size' from %s to %s "
                    "because it cannot exceed 'max_wal_size' (%s)",
                    min_wal, new_min_wal, max_wal
                )
                repaired["min_wal_size"] = new_min_wal

        # Handle connection slot constraints
        max_conn = repaired.get("max_connections")
        res_conn = repaired.get("reserved_connections", 0)
        super_res = repaired.get("superuser_reserved_connections", 0)

        if (
            isinstance(max_conn, (int, float, np.number)) and
            isinstance(res_conn, (int, float, np.number)) and
            isinstance(super_res, (int, float, np.number))
        ):
            reserved_total = int(res_conn) + int(super_res)
            if reserved_total >= int(max_conn):
                new_max = reserved_total + 10  # Ensure at least 10 slots for regular users
                worker_logger.warning(
                    "[REPAIR] Corrected 'max_connections' from %s to %s "
                    "because reserved slots (%s) consumed all capacity",
                    max_conn, new_max, reserved_total
                )
                repaired["max_connections"] = new_max

        # Ensure all modified numerical knobs still strictly conform to their bounds/steps
        for k, v in repaired.items():
            if k in self.knobs and self.knobs[k].knob_type in (KnobType.INTEGER, KnobType.REAL):
                repaired[k] = self.knobs[k].normalize_value(v)

        # Apply memory budget constraint
        db_budget = int(self.worker_resources.ram_bytes * 0.8) if self.worker_resources else None
        budget = budget_ram_bytes if budget_ram_bytes is not None else db_budget
        if budget is not None:
            repaired = self._repair_memory_budget(repaired, budget)

        return repaired

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

        return self.repair_config_dependencies(config)

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

                if (
                    knob_def.scale == KnobScale.LOG and
                    knob_def.min_value is not None and
                    knob_def.min_value > 0
                ):
                    log_min = np.log(knob_def.min_value)
                    log_max = np.log(knob_def.max_value)  # type: ignore
                    log_value = log_min + u * (log_max - log_min)
                    value = np.exp(log_value)

                    if knob_def.knob_type == KnobType.INTEGER:
                        value = knob_def.normalize_value(value)
                    else:
                        value = float(value)
                else:
                    value = knob_def.min_value + u * (
                        knob_def.max_value - knob_def.min_value
                        )  # type: ignore

                    if knob_def.knob_type == KnobType.INTEGER:
                        value = knob_def.normalize_value(value)
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

            configs.append(self.repair_config_dependencies(config))

        return configs

    def perturb_config(
        self,
        config: Dict[str, Any],
        perturbation_factor: Tuple[float, float] = (0.8, 1.2),
        seed: Optional[int] = None,
        worker_id: Optional[int] = None,
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
        worker_id : Optional[int]
            Worker ID for reproducibility
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
                if knob_def.scale == KnobScale.LOG and value > 0:
                    log_factor = rng.uniform(
                        np.log(perturbation_factor[0]),
                        np.log(perturbation_factor[1]),
                    )
                    new_value = np.exp(np.log(value) + log_factor)
                else:
                    factor = rng.uniform(perturbation_factor[0], perturbation_factor[1])
                    new_value = value * factor

                perturbed[knob_name] = knob_def.normalize_value(new_value)

            elif knob_def.knob_type == KnobType.REAL:
                if knob_def.scale == KnobScale.LOG and value > 0:
                    log_factor = rng.uniform(
                        np.log(perturbation_factor[0]),
                        np.log(perturbation_factor[1]),
                    )
                    new_value = np.exp(np.log(value) + log_factor)
                else:
                    factor = rng.uniform(perturbation_factor[0], perturbation_factor[1])
                    new_value = value * factor

                if knob_def.min_value is not None:
                    new_value = max(new_value, knob_def.min_value)
                if knob_def.max_value is not None:
                    new_value = min(new_value, knob_def.max_value)

                perturbed[knob_name] = knob_def.normalize_value(new_value)

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
                        perturbed[knob_name] = str(rng.choice(other_values))
                    else:
                        # Fallback for degenerate case
                        perturbed[knob_name] = value
                else:
                    perturbed[knob_name] = value

        return self.repair_config_dependencies(perturbed, worker_id=worker_id)

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
