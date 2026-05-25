# Configuration Management System

> Last reviewed: 2026-03-13

See also: [Documentation Index](./README.md)

## Overview

This document explains the **Configuration Management System** that handles PostgreSQL knob (parameter) definitions, validation, and runtime application. The system consists of two main components:

1. **KnobSpace**: Defines the search space for PBT optimization (which knobs to tune, valid ranges, sampling)
2. **KnobApplicator**: Applies configurations to PostgreSQL safely with validation and rollback

Together, these components ensure that PBT can explore valid configurations and apply them reliably to the database.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Component 1: KnobSpace](#component-1-knobspace)
3. [Component 2: KnobApplicator](#component-2-knobapplicator)
4. [Two-Layer Validation Architecture](#two-layer-validation-architecture)
5. [Context Manager Pattern](#context-manager-pattern)
6. [Rollback Mechanism](#rollback-mechanism)
7. [Design Decisions](#design-decisions)
8. [Related Documentation](#related-documentation)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     PBT Training Loop                       │
│                (see PBT_CORE_COMPONENTS.md)                 │
└────────────────────────┬────────────────────────────────────┘
                         │
         ┌───────────────┼───────────────┐
         │               │               │
         ▼               ▼               ▼
   ┌──────────┐   ┌──────────┐   ┌──────────┐
   │ Worker 0 │   │ Worker 1 │   │ Worker N │
   └─────┬────┘   └─────┬────┘   └─────┬────┘
         │              │              │
         │  knob_config │              │
         └──────────────┼──────────────┘
                        │
                        ▼
         ┌──────────────────────────────┐
         │        KnobSpace             │
         │   (Search Space Definition)  │
         │                              │
         │  • Define valid ranges       │
         │  • Sample configurations     │
         │  • Validate configs          │
         │  • Perturb values            │
         └──────────────┬───────────────┘
                        │
                        │ validated config
                        ▼
         ┌─────────────────────────────────┐
         │         KnobApplicator          │
         │     (Runtime Application)       │
         │                                 │
         │  • Validate against pg_settings │
         │  • Apply to PostgreSQL          │
         │  • Handle contexts              │
         │  • Rollback on error            │
         └─────────────┬───────────────────┘
                       │
                       ▼
         ┌────────────────────────────────┐
         │          PostgreSQL            │
         │       (Live Database)          │
         └────────────────────────────────┘
```

### Two-Phase Configuration Flow

1. **Design Time (KnobSpace)**: Define what configurations are **worth exploring**
2. **Runtime (KnobApplicator)**: Apply configurations **safely** to PostgreSQL

---

## Component 1: KnobSpace

**Location**: [src/tuner/config/knob_space.py](../src/tuner/config/knob_space.py)

### Purpose

KnobSpace defines the **search space** for PBT optimization. It answers:
- **Which knobs** should we tune?
- **What ranges** are reasonable to explore?
- **How** should we sample initial configurations?
- **How** should we validate configurations?

### Why a Search Space Definition?

PostgreSQL has ~350 configuration parameters, but:
- Not all affect performance significantly
- Not all should be tuned automatically (e.g., `port`, `data_directory`)
- Some have interdependencies (e.g., `shared_buffers` should be < total RAM)

**KnobSpace focuses PBT** on the most impactful, safely tunable parameters.

### Knob Definition

```python
@dataclass
class KnobDefinition:
    """
    Definition of a single PostgreSQL parameter for tuning.
    """
    name: str                            # Parameter name (e.g., 'shared_buffers')
    knob_type: KnobType                  # INTEGER, REAL, BOOLEAN, ENUM
    min_value: Optional[Union[int, float]]  # Minimum value (tuning range)
    max_value: Optional[Union[int, float]]  # Maximum value (tuning range)
    scale: KnobScale                     # LINEAR or LOG sampling
    default: Any                         # PostgreSQL default value
    unit: Optional[str]                  # Unit (kB, MB, ms, etc.)
    enum_values: Optional[List[str]]     # Valid values for ENUM type
    description: str                     # Human-readable description
    category: str                        # Functional category
    restart_required: bool               # Requires PostgreSQL restart?
```

### Knob Types

#### INTEGER Knobs

```python
KnobDefinition(
    name='shared_buffers',
    knob_type=KnobType.INTEGER,
    min_value=16384,      # 128MB (in 8KB pages)
    max_value=262144,     # 2GB (in 8KB pages)
    scale=KnobScale.LOG,  # Logarithmic sampling
    default=16384,
    unit='8kB',
    description='Amount of memory for caching data',
    category='memory',
    restart_required=True
)
```

**Why log scale?** Many memory/buffer parameters have exponential impact—differences matter more at lower values.

#### REAL (Float) Knobs

```python
KnobDefinition(
    name='random_page_cost',
    knob_type=KnobType.REAL,
    min_value=0.1,        # SSD/NVMe
    max_value=4.0,        # Traditional HDD
    scale=KnobScale.LINEAR,
    default=4.0,
    description='Planner cost estimate for random disk access',
    category='planner'
)
```

#### BOOLEAN Knobs

```python
KnobDefinition(
    name='enable_parallel_hash',
    knob_type=KnobType.BOOLEAN,
    default=True,
    description='Enable parallel hash joins',
    category='planner'
)
```

#### ENUM Knobs

```python
KnobDefinition(
    name='wal_level',
    knob_type=KnobType.ENUM,
    enum_values=['minimal', 'replica', 'logical'],
    default='replica',
    description='Level of WAL information to write',
    category='wal',
    restart_required=True
)
```

### Predefined Knob Sets

KnobSpace provides three predefined sets for different use cases:

#### MINIMAL_KNOBS (5 knobs)

**For**: Rapid prototyping, testing, proof-of-concept

```python
MINIMAL_KNOBS = [
    'shared_buffers',           # Memory cache
    'effective_cache_size',     # Planner hint
    'work_mem',                 # Sort/hash operations
    'random_page_cost',         # Disk cost estimate
    'max_parallel_workers_per_gather'  # Parallelism
]
```

**Why these?**
- **Highest impact**: Affect most queries
- **Safe to tune**: No risk of database corruption
- **No restart required** (except `shared_buffers`)

#### CORE_KNOBS (13 knobs)

**For**: Standard production tuning

Includes MINIMAL_KNOBS plus:
```python
CORE_KNOBS = MINIMAL_KNOBS + [
    'maintenance_work_mem',     # Index maintenance
    'checkpoint_completion_target',  # Write smoothing
    'wal_buffers',             # Write-ahead log memory
    'default_statistics_target',  # Query planner accuracy
    'effective_io_concurrency',   # Parallel I/O
    'max_worker_processes',      # Background workers
    'max_parallel_workers',      # Parallel query workers
    'min_wal_size'              # WAL retention
]
```

#### STANDARD_KNOBS (~30 knobs)

**For**: Comprehensive tuning, research

Includes CORE_KNOBS plus additional parameters for fine-tuning.

### KnobSpace Class

```python
class KnobSpace:
    """
    Collection of tunable knobs defining the search space.
    """
    
    def __init__(self, knob_definitions: List[KnobDefinition]):
        self.knobs = {k.name: k for k in knob_definitions}
    
    def sample(self) -> Dict[str, Any]:
        """Sample a random configuration from the space."""
        
    def validate_config(self, config: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Validate a configuration against knob definitions."""
    
    def get_knob(self, name: str) -> KnobDefinition:
        """Get knob definition by name."""
    
    def clamp_value(self, name: str, value: Any) -> Any:
        """Clamp value to valid range for the knob."""
```

### Core Methods

#### `sample()` - Random Configuration Sampling

```python
def sample(self, rng: Optional[np.random.Generator] = None) -> Dict[str, Any]:
    """
    Sample a random configuration from the knob space.
    
    Used for:
    - Initial population in PBT
    - Generating test configurations
    
    Returns
    -------
    Dict[str, Any]
        Random configuration with all knobs
    """
    config = {}
    
    for knob_name, knob_def in self.knobs.items():
        if knob_def.knob_type == KnobType.INTEGER:
            if knob_def.scale == KnobScale.LOG:
                # Logarithmic sampling
                log_min = np.log(knob_def.min_value)
                log_max = np.log(knob_def.max_value)
                log_value = rng.uniform(log_min, log_max)
                value = int(np.exp(log_value))
            else:
                # Linear sampling
                value = rng.integers(
                    knob_def.min_value,
                    knob_def.max_value + 1
                )
        
        elif knob_def.knob_type == KnobType.REAL:
            value = rng.uniform(knob_def.min_value, knob_def.max_value)
        
        elif knob_def.knob_type == KnobType.BOOLEAN:
            value = rng.choice([True, False])
        
        elif knob_def.knob_type == KnobType.ENUM:
            value = rng.choice(knob_def.enum_values)
        
        config[knob_name] = value
    
    return config
```

**Why log sampling?** For parameters like `shared_buffers`:
- Linear: Equal probability for 128MB and 129MB (1MB difference)
- Log: Appropriate probability for 128MB and 256MB (2× difference)

#### `validate_config()` - Configuration Validation

```python
def validate_config(
    self,
    config: Dict[str, Any]
) -> Tuple[bool, List[str]]:
    """
    Validate a configuration against knob definitions.
    
    Checks:
    1. No unknown knobs
    2. No missing knobs
    3. Each value is valid for its knob type
    4. Values within min/max bounds
    
    Returns
    -------
    Tuple[bool, List[str]]
        (is_valid, error_messages)
    """
    errors = []
    
    for knob_name in config:
        if knob_name not in self.knobs:
            errors.append(f"Unknown knob: {knob_name}")
    
    for knob_name in self.knobs:
        if knob_name not in config:
            errors.append(f"Missing knob: {knob_name}")
    
    for knob_name, value in config.items():
        if knob_name in self.knobs:
            knob_def = self.knobs[knob_name]
            if not knob_def.validate_value(value):
                errors.append(
                    f"{knob_name}: invalid value {value} "
                    f"(type={knob_def.knob_type}, "
                    f"range=[{knob_def.min_value}, {knob_def.max_value}])"
                )
    
    return (len(errors) == 0, errors)
```

**When is this used?**
- Before perturbing configurations (ensure valid before mutation)
- After perturbing (ensure still valid after mutation)
- When loading configurations from checkpoints

#### `clamp_value()` - Range Enforcement

```python
def clamp_value(self, name: str, value: Any) -> Any:
    """
    Clamp value to valid range for the knob.
    
    Used after perturbation to ensure values stay in bounds.
    """
    knob_def = self.knobs[name]
    
    if knob_def.knob_type in [KnobType.INTEGER, KnobType.REAL]:
        if knob_def.min_value is not None:
            value = max(value, knob_def.min_value)
        if knob_def.max_value is not None:
            value = min(value, knob_def.max_value)
    
    if knob_def.knob_type == KnobType.INTEGER:
        value = int(value)
    
    return value
```

**Example**:
```python
# After perturbation: work_mem = 32768 * 1.25 = 40960
# But max_value = 32768
clamped = knob_space.clamp_value('work_mem', 40960)
# Result: 32768 (clamped to maximum)
```

### Usage in PBT

```python
from src.tuner.config import get_knob_space

knob_space = get_knob_space('minimal')
configs = [knob_space.sample() for _ in range(8)]

for i, config in enumerate(configs):
    worker = Worker(
        worker_id=i,
        knob_space=knob_space,
        knob_config=config
    )

new_config = perturb_config(worker.knob_config, (0.8, 1.2))
is_valid, errors = knob_space.validate_config(new_config)

if not is_valid:
    # Clamp invalid values
    for knob_name, value in new_config.items():
        new_config[knob_name] = knob_space.clamp_value(knob_name, value)
```

---

## Component 2: KnobApplicator

**Location**: [src/tuner/utils/applicator.py](../src/tuner/utils/applicator.py)

### Purpose

KnobApplicator **applies configurations to a live PostgreSQL database** with:
- **Validation**: Against actual PostgreSQL constraints
- **Context awareness**: Handle postmaster vs runtime parameters
- **Rollback**: Undo changes if application fails
- **Safety**: Prevent invalid/dangerous configurations

### Why Not Just Execute SET Commands?

**Naive approach**:
```python
# BAD: No validation, no error handling, no rollback
for knob, value in config.items():
    cursor.execute(f"ALTER SYSTEM SET {knob} = {value}")
```

**Problems**:
1. **No validation**: Might apply invalid values
2. **No context handling**: Some params need restart, some need reload
3. **No rollback**: Partial application on error
4. **SQL injection**: User values directly in query

**KnobApplicator solves all of these**.

### ApplicatorConfig

```python
@dataclass
class ApplicatorConfig:
    """Configuration for KnobApplicator behavior."""
    
    persist: bool = True              # Write to postgresql.conf?
    auto_reload: bool = True          # Auto pg_reload_conf()?
    validate: bool = True             # Validate against pg_settings?
    dry_run: bool = False            # Simulate without applying?
    rollback_on_error: bool = True   # Rollback all if any fails?
    allow_restart_params: bool = True  # Allow postmaster params?
```

**Typical configurations**:

```python
# Production: Persist and validate
prod_config = ApplicatorConfig(
    persist=True,
    validate=True,
    rollback_on_error=True
)

# Evaluation: Runtime only, no persistence
eval_config = ApplicatorConfig(
    persist=False,      # Don't modify postgresql.conf
    auto_reload=True,   # Reload for sighup params
    validate=True
)

# Testing: Dry run
test_config = ApplicatorConfig(
    dry_run=True,       # Don't actually apply
    validate=True       # But do validate
)
```

### Parameter Information

KnobApplicator queries `pg_settings` to understand PostgreSQL's constraints:

```python
@dataclass
class ParameterInfo:
    """Information about a parameter from pg_settings."""
    name: str
    vartype: str           # 'bool', 'integer', 'real', 'string', 'enum'
    context: str           # 'internal', 'postmaster', 'sighup', 'user', etc.
    unit: Optional[str]    # Unit (kB, ms, etc.)
    min_val: Optional[str] # Minimum value
    max_val: Optional[str] # Maximum value
    enumvals: Optional[List[str]]  # Valid enum values
    boot_val: Optional[str]  # Boot-time value
    reset_val: Optional[str]  # Current value
```

**Parameter Contexts** (from PostgreSQL):
- **internal**: Read-only, cannot be changed
- **postmaster**: Requires PostgreSQL restart
- **sighup**: Requires `pg_reload_conf()` (SIGHUP signal)
- **user**: Can be changed per-session with SET
- **superuser**: Can be changed by superuser

### Core Methods

#### `apply()` - Main Entry Point

```python
def apply(
    self,
    knob_config: Dict[str, Any],
    description: str = ""
) -> ApplicationResult:
    """
    Apply configuration to PostgreSQL.
    
    Process:
    1. Connect to database
    2. Load parameter info from pg_settings
    3. Validate each parameter
    4. Apply parameters (or dry run)
    5. Reload configuration if needed
    6. Rollback on error if configured
    
    Parameters
    ----------
    knob_config : Dict[str, Any]
        Configuration to apply
    description : str
        Description for logging
    
    Returns
    -------
    ApplicationResult
        Result with success status, applied/failed params, restart info
    """
```

**Flow**:
```
apply(config)
    │
    ├─► connect()
    │
    ├─► _load_parameter_info()  # Query pg_settings
    │
    ├─► For each parameter:
    │       ├─► _validate_parameter()  # Check constraints
    │       ├─► _apply_parameter()     # Execute SET/ALTER SYSTEM
    │       └─► Track success/failure
    │
    ├─► If auto_reload and sighup params:
    │       └─► pg_reload_conf()
    │
    ├─► If rollback_on_error and any failures:
    │       └─► connection.rollback()  # PostgreSQL transaction rollback
    │   Else:
    │       └─► connection.commit()    # Commit changes
    │
    └─► Return ApplicationResult
```

#### `_validate_parameter()` - Runtime Validation

```python
def _validate_parameter(
    self,
    name: str,
    value: Any,
    param_info: ParameterInfo
) -> Tuple[bool, Optional[str]]:
    """
    Validate parameter against PostgreSQL constraints.
    
    Checks:
    1. Context: Is parameter modifiable? (not 'internal')
    2. Restart: Does it require restart? (respect allow_restart_params)
    3. Type: Boolean, integer, real, enum
    4. Range: Within min_val and max_val
    5. Enum: Value in enumvals list
    
    Returns
    -------
    Tuple[bool, Optional[str]]
        (is_valid, error_message)
    """
    if param_info.context == "internal":
        return False, f"{name} is read-only (internal context)"
    
    if param_info.context == "postmaster" and not self.config.allow_restart_params:
        return False, f"{name} requires restart (postmaster context)"
    
    if param_info.vartype == "bool":
        if not isinstance(value, (bool, int, str)):
            return False, f"{name} must be boolean"
    
    elif param_info.vartype == "integer":
        try:
            int_val = int(value)
            if param_info.min_val:
                min_int = int(param_info.min_val)
                if int_val < min_int:
                    return False, f"{name} below min ({min_int}): {int_val}"
            if param_info.max_val:
                max_int = int(param_info.max_val)
                if int_val > max_int:
                    return False, f"{name} above max ({max_int}): {int_val}"
        except ValueError:
            return False, f"{name} must be integer"
    
    elif param_info.vartype == "real":
        # Similar validation for float...
    
    elif param_info.vartype == "enum":
        if value not in param_info.enumvals:
            return False, f"{name} must be one of {param_info.enumvals}"
    
    return True, None
```

**This is the second validation layer** (see [Two-Layer Validation](#two-layer-validation-architecture)).

#### `_apply_parameter()` - Safe Application

```python
def _apply_parameter(
    self,
    name: str,
    value: Any,
    param_info: ParameterInfo
) -> bool:
    """
    Apply single parameter to PostgreSQL.
    
    Uses parameterized queries to prevent SQL injection.
    """
    try:
        cursor = self.connection.cursor()
        
        if self.config.persist:
            # ALTER SYSTEM writes to postgresql.auto.conf
            cursor.execute(
                "ALTER SYSTEM SET %s = %s",
                (AsIs(name), value)
            )
        else:
            # SET applies for current session only
            cursor.execute(
                "SET %s = %s",
                (AsIs(name), value)
            )
        
        cursor.close()
        return True
        
    except psycopg2.Error as e:
        logger.error(f"Failed to apply {name}: {e}")
        return False
```

**Key safety features**:
- **Parameterized queries**: Prevents SQL injection
- **Error handling**: Catches and logs PostgreSQL errors
- **Context awareness**: Uses ALTER SYSTEM vs SET appropriately

#### `read_back_knob_state()` - Read-Back Abstraction

```python
def read_back_knob_state(
    self,
    knob_names: List[str],
    knob_space: KnobSpace,
    connect_timeout: int = 5
) -> Dict[str, Any]:
    """
    Query pg_settings and return the actually applied knob values,
    with unit conversion and type casting.
    """
```

**Purpose**: Solves two critical problems when external optimizers (like BO) need to know the *exact* configuration running inside the database engine:

1. **Quantization trap**: PostgreSQL rounds values to internal block boundaries (e.g., `shared_buffers` is rounded to the nearest 8kB page). This method returns the true quantized value.
2. **Unit-conversion trap**: `pg_settings` returns raw numeric strings and separate unit strings. This method applies the necessary multipliers and converts the value back to the canonical unit and Python type expected by the `KnobDefinition` (e.g., `KnobType.INTEGER`).

**Usage**:
```python
# During evaluation loop
actual_knobs = applicator.read_back_knob_state(
    knob_names=list(knob_config.keys()),
    knob_space=knob_space
)
if actual_knobs:
    # Update the configuration dictionary with true values
    knob_config.update(actual_knobs)
```

**Note**: The actual KnobApplicator implementation doesn't have individual parameter rollback—it uses psycopg2's transaction rollback mechanism (see [Rollback Mechanism](#rollback-mechanism) section for details).

### ApplicationResult

```python
@dataclass
class ApplicationResult:
    """Result of applying configuration changes."""
    
    success: bool                        # Overall success
    applied: Dict[str, Any]              # Successfully applied
    failed: Dict[str, str]               # Failed with error messages
    restart_required: Set[str]           # Params needing restart
    applied_count: int                   # Count of successful
    failed_count: int                    # Count of failed
    message: str                         # Overall message
```

**Usage**:
```python
result = applicator.apply(config)

if result.success:
    print(f"✓ Applied {result.applied_count} parameters")
    if result.restart_required:
        print(f"⚠ Restart needed for: {', '.join(result.restart_required)}")
else:
    print(f"✗ Failed to apply configuration")
    for param, error in result.failed.items():
        print(f"  {param}: {error}")
```

---

## Two-Layer Validation Architecture

The configuration system has **two distinct validation layers**:

### Layer 1: KnobSpace Validation (Design Time)

**Purpose**: Validate against **tuning ranges**

**Scope**: Knob definitions from `data/knob_metadata.json`

**Example**:
```python
# work_mem tuning range: 4MB to 256MB
work_mem_def = KnobDefinition(
    name='work_mem',
    min_value=4096,    # 4MB in kB
    max_value=262144,  # 256MB in kB
    ...
)
```

**Validation**:
```python
config = {'work_mem': 512000}  # 500MB
is_valid, errors = knob_space.validate_config(config)
# is_valid = False
# errors = ["work_mem: invalid value 512000 (range=[4096, 262144])"]
```

**Why this range?** Based on experience/research:
- Below 4MB: Likely too small for most queries
- Above 256MB: Diminishing returns, memory waste
- This is the **tuning search space** for PBT

### Layer 2: KnobApplicator Validation (Runtime)

**Purpose**: Validate against **PostgreSQL actual constraints**

**Scope**: Parameter info from `pg_settings` table

**Example**:
```python
# PostgreSQL allows work_mem: 64kB to 2GB
param_info = ParameterInfo(
    name='work_mem',
    min_val='64',      # 64kB (from PostgreSQL)
    max_val='2097151', # ~2GB (from PostgreSQL)
    ...
)
```

**Validation**:
```python
# Value from KnobSpace: 128MB (within tuning range)
value = 131072  # 128MB in kB

is_valid, error = applicator._validate_parameter('work_mem', value, param_info)
# is_valid = True (within PostgreSQL's range)
```

**Why this range?** From PostgreSQL source code:
- PostgreSQL enforces minimum/maximum values
- These are **hard constraints** from the database

### Why Two Layers?

**Defense in depth**:
1. **KnobSpace** keeps PBT exploring reasonable regions (efficiency)
2. **KnobApplicator** ensures values are safe for PostgreSQL (safety)

**Different scopes**:
- **Tuning range** (KnobSpace): Narrower, optimized for typical workloads
- **PostgreSQL range** (KnobApplicator): Broader, covers all valid values

**Example**:
```
work_mem:
  PostgreSQL allows:    64 kB  ──────────────────────────────  2 GB
  KnobSpace tunes:           4 MB  ───────────  256 MB
  
  PBT explores 4-256MB (reasonable for most workloads)
  But PostgreSQL accepts 64kB-2GB (technical limits)
```

**Analogy**:
- **KnobSpace**: "Recommended speed range" (55-75 mph on highway)
- **KnobApplicator**: "Legal speed limit" (0-120 mph technically possible)

---

## Context Manager Pattern

KnobApplicator implements Python's **context manager protocol** for automatic resource management.

### What is a Context Manager?

A context manager is an object that defines `__enter__` and `__exit__` methods, allowing it to be used with Python's `with` statement:

```python
with resource as r:
    # Use resource
    pass
# Resource automatically cleaned up
```

### Implementation

```python
class KnobApplicator:
    def __enter__(self) -> 'KnobApplicator':
        """
        Called when entering 'with' block.
        Establishes database connection.
        """
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Called when exiting 'with' block.
        Automatically closes database connection.
        
        Called even if exception occurs!
        """
        self.disconnect()
        return False  # Don't suppress exceptions
```

### Usage

**Without context manager** (manual cleanup):
```python
applicator = KnobApplicator(config)
try:
    applicator.connect()
    result = applicator.apply(knob_config)
    # ... use result ...
finally:
    applicator.disconnect()  # Must remember this!
```

**With context manager** (automatic cleanup):
```python
with KnobApplicator(config) as applicator:
    result = applicator.apply(knob_config)
    # ... use result ...
# disconnect() called automatically!
```

### Why This Matters

**Exception safety**:
```python
with KnobApplicator(config) as applicator:
    result = applicator.apply(knob_config)
    if not result.success:
        raise RuntimeError("Application failed")  # Exception!
# disconnect() still called! (in __exit__)
```

Even if an exception occurs, `__exit__` is **always called**, ensuring the database connection is closed.

**Cleaner code**:
- No explicit `try/finally` needed
- Impossible to forget cleanup
- Follows Python idioms

---

## Rollback Mechanism

### Purpose

When `rollback_on_error=True`, if **any** parameter fails to apply, **all** successfully applied parameters are reverted.

### Why Rollback?

**Without rollback**:
```
Config: {work_mem: 8192, shared_buffers: 131072, invalid_param: 999}

Application:
  ✓ work_mem applied
  ✓ shared_buffers applied
  ✗ invalid_param failed

Result: Partial application! Database in inconsistent state.
```

**With rollback**:
```
Config: {work_mem: 8192, shared_buffers: 131072, invalid_param: 999}

Application:
  ✓ work_mem applied
  ✓ shared_buffers applied
  ✗ invalid_param failed
  ↻ Rolling back work_mem
  ↻ Rolling back shared_buffers

Result: All-or-nothing! Database state unchanged.
```

### How Rollback Works

KnobApplicator uses **PostgreSQL's transaction mechanism** via psycopg2:

```python
def apply(self, knob_config):
    try:
        for param_name, value in knob_config.items():
            is_valid, error = self._validate_parameter(param_name, value, ...)
            if not is_valid:
                failed_params[param_name] = error
                continue
            
            success = self._apply_parameter(param_name, value, ...)
            if success:
                applied_params.append(param_name)
            else:
                failed_params[param_name] = "Application failed"
        
        if failed_params and self.config.rollback_on_error:
            self.connection.rollback()  # ← PostgreSQL transaction rollback
            logger.warning("Rolled back all changes due to failures")
            return ApplicationResult(
                success=False,
                failed=failed_params,
                message="Rolled back: application failed"
            )
        else:
            self.connection.commit()  # ← Commit successful changes
            return ApplicationResult(
                success=True,
                applied=applied_params,
                failed=failed_params
            )
    
    except psycopg2.Error as e:
        self.connection.rollback()  # ← Rollback on exception
        return ApplicationResult(
            success=False,
            message=f"Database error: {e}"
        )
```

### How Transaction Rollback Works

**Key concept**: PostgreSQL treats all changes within a transaction as **atomic**.

```
BEGIN TRANSACTION (implicit)
  ├─► ALTER SYSTEM SET work_mem = 8192;      ✓ Executed
  ├─► ALTER SYSTEM SET shared_buffers = ...; ✓ Executed
  ├─► ALTER SYSTEM SET invalid_param = ...;  ✗ Failed
  └─► ROLLBACK;                               ↻ All changes undone
END TRANSACTION
```

**With psycopg2**:
- `connection.commit()`: Makes all changes permanent
- `connection.rollback()`: Discards all changes since last commit
- No need to manually undo each parameter—PostgreSQL handles it

**Advantages over manual rollback**:
- **Simpler**: One line (`connection.rollback()`) vs looping through parameters
- **More reliable**: PostgreSQL guarantees atomicity
- **Faster**: No need to query original values and execute RESET commands
- **Exception-safe**: Works even if tracking applied parameters fails

### When to Use Rollback

**Enable rollback** (`rollback_on_error=True`):
- Production deployments (consistency critical)
- Testing/validation (want clean state)
- Atomic configuration updates

**Disable rollback** (`rollback_on_error=False`):
- Best-effort application (apply what you can)
- Debugging (want to see partial effects)
- Manual rollback preferred

---

## Design Decisions

### 1. Two-Layer Validation

**Decision**: Separate KnobSpace (tuning ranges) and KnobApplicator (PostgreSQL constraints) validation.

**Why?**
- **Different purposes**: Tuning exploration vs runtime safety
- **Different scopes**: Reasonable ranges vs technical limits
- **Defense in depth**: Two chances to catch invalid configs
- **Flexibility**: Can adjust tuning ranges without changing application logic

### 2. Log-Scale Sampling

**Decision**: Use logarithmic sampling for memory/buffer parameters.

**Why?**
- **Exponential impact**: Doubling memory has bigger effect than adding fixed amount
- **Appropriate exploration**: Focus search on orders of magnitude
- **Real-world tuning**: DBAs typically tune in powers of 2

**Example**:
```
Linear: [16384, 32768, 49152, 65536, 81920, 98304, ...]
  → Uniform density, lots of similar values

Log: [16384, 32768, 65536, 131072, 262144]
  → Exponential density, covers wide range efficiently
```

### 3. Parameterized Queries

**Decision**: Use psycopg2 parameterized queries for parameter application.

**Why?**
- **SQL injection prevention**: User values never directly in SQL string
- **Type handling**: psycopg2 handles type conversion
- **PostgreSQL best practice**: Recommended by psycopg2 docs

**Example**:
```python
# UNSAFE
cursor.execute(f"SET work_mem = {value}")  # SQL injection risk!

# SAFE
cursor.execute("SET %s = %s", (AsIs('work_mem'), value))
```

### 4. Context Manager Implementation

**Decision**: Implement `__enter__` and `__exit__` for KnobApplicator.

**Why?**
- **Exception safety**: Guaranteed cleanup even on error
- **Pythonic**: Follows Python idioms (with statement)
- **Cleaner code**: No explicit try/finally needed
- **Testable**: Can verify lifecycle management

### 5. ApplicationResult Dataclass

**Decision**: Return structured result object instead of boolean or exception.

**Why?**
- **Rich information**: Success/failure, which params failed, why
- **No exceptions for expected failures**: Invalid params are expected, not exceptional
- **Actionable**: Caller can inspect and handle partial failures
- **Logging**: Easy to log comprehensive result

**Example**:
```python
result = applicator.apply(config)
if result.success:
    logger.info(f"Applied {result.applied_count} parameters")
else:
    logger.error(f"Failed: {result.message}")
    for param, error in result.failed.items():
        logger.error(f"  {param}: {error}")
```

### 6. Predefined Knob Sets

**Decision**: Provide MINIMAL, CORE, STANDARD knob sets.

**Why?**
- **Accessibility**: Easy to get started (just pick a set)
- **Best practices**: Curated based on research and experience
- **Progressive complexity**: Start with MINIMAL, expand to CORE/STANDARD
- **Benchmarking**: Standardized sets for comparing results

### 7. Dry Run Mode

**Decision**: Support `dry_run=True` to simulate without applying.

**Why?**
- **Testing**: Verify configuration without affecting database
- **Validation**: Check what would be applied
- **Safety**: Preview changes before committing
- **Debugging**: Understand application flow

**Example**:
```python
config = ApplicatorConfig(dry_run=True)
result = applicator.apply(knob_config)
# No changes made, but result shows what would happen
print(f"Would apply: {result.applied}")
print(f"Would fail: {result.failed}")
```

---

## Related Documentation

### Prerequisites

- **[Environment Setup](./ENVIRONMENT_SETUP.md)**: Install dependencies (psycopg2, numpy)
- **[PostgreSQL Connection](./POSTGRESQL_CONNECTION_AND_KNOBS.md)**: Database connection management and knob retrieval

### Integration

- **[PBT Core Components](./PBT_CORE_COMPONENTS.md)**: Workers use KnobSpace for configuration sampling and validation
- **[Performance Evaluation](./PERFORMANCE_EVALUATION.md)**: Evaluator uses KnobApplicator to apply configurations before workload execution

### Usage Flow

```
PBT Training Loop
    │
    ├─► Worker initialized with KnobSpace
    │       └─► sample() random config
    │
    ├─► Evaluator applies config
    │       └─► KnobApplicator.apply()
    │
    ├─► Workload executed (see PERFORMANCE_EVALUATION.md)
    │
    └─► Worker evolved (see PBT_CORE_COMPONENTS.md)
            └─► perturb_config() + validate_config()
```

---

## Summary

The Configuration Management System ensures safe, validated configuration handling:

1. **KnobSpace**: Defines the search space for PBT optimization
   - Sample random configurations
   - Validate against tuning ranges
   - Clamp perturbed values to valid ranges

2. **KnobApplicator**: Applies configurations to PostgreSQL safely
   - Validate against actual PostgreSQL constraints
   - Handle context-aware application (postmaster vs runtime)
   - Rollback on error for atomicity
   - Context manager for automatic cleanup

3. **Two-Layer Validation**: Defense in depth
   - Layer 1 (KnobSpace): Tuning ranges (exploration efficiency)
   - Layer 2 (KnobApplicator): PostgreSQL constraints (safety)

**Key Insight**: Separating search space definition (KnobSpace) from runtime application (KnobApplicator) provides both **exploration efficiency** (PBT stays in reasonable regions) and **safety** (invalid configs never reach PostgreSQL).

**File Locations**:
- KnobSpace: `src/tuner/config/knob_space.py`
- KnobApplicator: `src/tuner/utils/applicator.py`
- Tests: `src/tuner/config/__main__.py`, `src/tuner/utils/__main__.py`
