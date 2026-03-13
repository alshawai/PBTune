# Performance Evaluation System

> Last reviewed: 2026-03-13

See also: [Documentation Index](./README.md)

## Overview

This document explains the **Performance Evaluation System** that measures how well PostgreSQL configurations perform under workload. The evaluation system consists of three main components:

1. **Evaluator**: Executes workloads and orchestrates metric collection
2. **PerformanceMetrics**: Structured measurements (latency, throughput, resource usage)
3. **Scoring System**: Converts raw metrics into a single performance score for optimization

This system serves as the **fitness function** for Population Based Training—it's how PBT determines which configurations are "good" and which are "poor."

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Component 1: PerformanceMetrics](#component-1-performancemetrics)
3. [Component 2: Metric Scoring](#component-2-metric-scoring)
4. [Component 3: Evaluator](#component-3-evaluator)
5. [System Monitoring with psutil](#system-monitoring-with-psutil)
6. [Workload Types and Optimization Goals](#workload-types-and-optimization-goals)
7. [Design Decisions](#design-decisions)
8. [Related Documentation](#related-documentation)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      PBT Training Loop                      │
│                (see PBT_CORE_COMPONENTS.md)                 │
└───────────────────────────────┬─────────────────────────────┘
                                │
           evaluate_fn(worker)  │ 
                                ▼
                ┌──────────────────────────────┐
                │           Evaluator          │
                │        (Orchestrator)        │
                └───────────────┬──────────────┘
                                │
                ┌───────────────┼───────────────┐
                │               │               │
                ▼               ▼               ▼
            ┌──────────┐   ┌──────────┐   ┌──────────┐
            │  Apply   │   │   Run    │   │ Collect  │
            │  Config  │   │ Workload │   │ Metrics  │
            └──────────┘   └──────────┘   └──────────┘
                │               │               │
                ▼               ▼               ▼
         KnobApplicator    PostgreSQL    psutil + pg_stat
                │               │               │
                └───────────────┼───────────────┘
                                │
                                ▼
                    ┌─────────────────────┐
                    │ PerformanceMetrics  │
                    │  (Structured Data)  │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  compute_score()    │
                    │  (Composite Score)  │
                    └──────────┬──────────┘
                               │
                               ▼
                       Single float value
                       (higher = better)
                               │
                               ▼
                     Back to PBT Population
                      for exploit/explore
```

### Data Flow

1. **PBT** requests evaluation of a worker's configuration
2. **Evaluator** applies the configuration to PostgreSQL
3. **Evaluator** executes workload (SYSBENCH, TPC-H, etc.)
4. **Metrics Collection** gathers performance data (latency, throughput, resources)
5. **Scoring** converts metrics → single composite score
6. **PBT** uses score to rank workers and drive evolution

---

## Component 1: PerformanceMetrics

**Location**: [src/tuner/evaluator/metrics.py](../src/tuner/evaluator/metrics.py)

### Purpose

The `PerformanceMetrics` dataclass is a **structured container** for all performance measurements collected during workload execution. It captures:
- **Latency**: Response time percentiles (p50, p95, p99)
- **Throughput**: Queries/transactions per second
- **Resources**: CPU, memory, disk I/O
- **Quality**: Error rate, cache hit ratio

### Why a Structured Metrics Class?

**Without structure**:
```python
# Metrics as loose dictionary - fragile, no type safety
metrics = {
    "latency": 45.3,  # p50? p95? p99? Units?
    "throughput": 1234,  # TPS or QPS?
    "cpu": 0.67  # Percentage or fraction?
}
```

**With PerformanceMetrics**:
```python
# Clear, type-safe, self-documenting
metrics = PerformanceMetrics(
    latency_p50=38.2,      # Milliseconds, median
    latency_p95=45.3,      # Milliseconds, 95th percentile
    latency_p99=52.1,      # Milliseconds, 99th percentile
    throughput=1234.5,     # Queries per second
    cpu_utilization=0.67,  # Fraction (0.0 to 1.0)
    memory_utilization=0.82,
    io_read_mb=125.4,
    io_write_mb=43.2,
    cache_hit_ratio=0.95,
    error_rate=0.001
)
```

### Metric Categories

#### Latency Metrics

```python
latency_p50: float  # Median (50th percentile)
latency_p95: float  # 95th percentile (typical SLA target)
latency_p99: float  # 99th percentile (tail latency)
```

**Why percentiles?**
- **Mean is misleading**: A few slow queries can skew average
- **p95/p99 capture tail latency**: Critical for user experience
- **p50 shows typical case**: Good for overall performance assessment

**Example**:
```
100 queries: [10ms, 12ms, 11ms, ..., 95ms, 102ms, 250ms]
Mean: 25ms (skewed by outliers)
p50: 12ms (typical query)
p95: 95ms (most queries faster than this)
p99: 250ms (worst-case for 99% of queries)
```

#### Throughput Metrics

```python
throughput: float        # Queries/second or TPS
total_queries: int       # Total executed
total_time: float        # Duration in seconds
error_rate: float        # Failed queries (0.0 to 1.0)
```

**Throughput calculation**:
```python
throughput = total_queries / total_time
```

**Why track errors separately?**
- High throughput with high error rate is misleading
- Error rate used as penalty in scoring function

#### Resource Utilization Metrics

```python
cpu_utilization: float      # CPU usage (0.0 to 1.0)
memory_utilization: float   # Memory usage (0.0 to 1.0)
io_read_mb: float          # MB read from disk
io_write_mb: float         # MB written to disk
cache_hit_ratio: float     # Buffer cache hits (0.0 to 1.0)
```

**Why track resources?**
- **Efficiency**: High performance at low resource cost is better
- **Scalability**: Resource-efficient configs scale better
- **Real-world constraints**: Production systems have resource limits

### Methods

#### `to_dict()`
Converts metrics to dictionary for logging/serialization:

```python
def to_dict(self) -> Dict[str, float]:
    return {
        'latency_p50': self.latency_p50,
        'latency_p95': self.latency_p95,
        # ... all other fields
    }
```

#### `from_dict(data)`
Creates PerformanceMetrics from dictionary (deserialization):

```python
@staticmethod
def from_dict(data: Dict[str, float]) -> PerformanceMetrics:
    return PerformanceMetrics(**data)
```

Useful for loading metrics from checkpoints or logs.

---

## Component 2: Metric Scoring

**Location**: [src/tuner/evaluator/metrics.py](../src/tuner/evaluator/metrics.py)

### Purpose

The scoring system converts **multiple raw metrics** into a **single composite score** that PBT can optimize. This is the **fitness function** that drives evolution.

### Why Composite Scoring?

**Challenge**: PostgreSQL has ~350 knobs affecting different performance aspects:
- `shared_buffers` affects cache hit ratio
- `work_mem` affects sort/join performance
- `max_parallel_workers` affects query parallelism

**Problem**: Optimizing one metric often degrades others
- Increase `work_mem` → better query performance, but higher memory usage
- Increase `shared_buffers` → better cache hits, but less memory for other processes

**Solution**: Weighted composite score captures trade-offs:
```
score = w1·throughput + w2·(1/latency) + w3·(1 - cpu_util) + w4·cache_hit_ratio
```

### MetricConfig: Workload-Specific Weights

```python
@dataclass
class MetricConfig:
    """
    Configuration for metric scoring.
    Weights determine importance of each metric.
    """
    workload_type: WorkloadType
    
    # Primary metrics
    latency_weight: float = 0.4        # Lower latency = better
    throughput_weight: float = 0.3     # Higher TPS = better
    
    # Resource efficiency
    cpu_weight: float = 0.1            # Lower CPU = better
    memory_weight: float = 0.1         # Lower memory = better
    io_weight: float = 0.05            # Lower I/O = better
    
    # Quality metrics
    cache_weight: float = 0.04         # Higher cache hits = better
    error_penalty: float = 10.0        # Error rate penalty multiplier
```

**Default weights** (OLTP-focused):
- Latency: 40% (most important for OLTP)
- Throughput: 30% (high transaction rate critical)
- CPU: 10% (efficiency matters)
- Memory: 10% (memory efficiency)
- I/O: 5% (less critical with good caching)
- Cache: 4% (indirectly captured by latency)
- Error penalty: 10× (heavily penalize errors)

### Scoring Functions

#### OLTP Scoring (Transaction Processing)

```python
def compute_oltp_score(metrics: PerformanceMetrics, config: MetricConfig) -> float:
    """
    OLTP optimization goal: High throughput + Low latency
    
    Priorities:
    1. Low latency (fast transaction response)
    2. High throughput (many transactions/second)
    3. Resource efficiency (low CPU/memory)
    """
    
    # lower is better
    latency_score = config.latency_weight / (metrics.latency_p95 + 1.0)
    
    # higher is better
    throughput_score = config.throughput_weight * metrics.throughput / 1000.0
    
    # lower usage is better
    cpu_score = config.cpu_weight * (1.0 - metrics.cpu_utilization)
    memory_score = config.memory_weight * (1.0 - metrics.memory_utilization)
    
    cache_score = config.cache_weight * metrics.cache_hit_ratio
    score = latency_score + throughput_score + cpu_score + memory_score + cache_score
    
    if metrics.error_rate > 0:
        score *= (1.0 - metrics.error_rate * config.error_penalty)
    
    return max(0.0, score)  # Ensure non-negative
```

**Example**:
```
Metrics: latency_p95=45ms, throughput=1200 TPS, cpu=0.65, memory=0.72, 
         cache_hit=0.95, errors=0.001

Score calculation:
  latency_score  = 0.4 / (45 + 1)    = 0.00870
  throughput_score = 0.3 * 1200/1000  = 0.36000
  cpu_score      = 0.1 * (1 - 0.65)  = 0.03500
  memory_score   = 0.1 * (1 - 0.72)  = 0.02800
  cache_score    = 0.04 * 0.95       = 0.03800
                                       ────────
  Subtotal                           = 0.48970
  Error penalty  = 1 - (0.001 * 10)  = 0.99000
                                       ────────
  Final score    = 0.48970 * 0.99    = 0.48480
```

#### OLAP Scoring (Analytics Queries)

```python
def compute_olap_score(metrics: PerformanceMetrics, config: MetricConfig) -> float:
    """
    OLAP optimization goal: Fast query execution + Resource efficiency
    
    Priorities:
    1. Low query execution time
    2. Efficient memory usage (large sorts/joins)
    3. Good cache utilization
    """
    
    query_time_score = config.latency_weight / (metrics.latency_p95 + 1.0)
    
    # OLAP often memory-constrained
    memory_score = config.memory_weight * (1.0 - metrics.memory_utilization)
    
    # Cache efficiency (critical for OLAP)
    cache_score = config.cache_weight * metrics.cache_hit_ratio
    
    # I/O efficiency (OLAP can be I/O-bound)
    io_score = config.io_weight / (metrics.io_read_mb + 1.0)
    
    score = query_time_score + memory_score + cache_score + io_score
    
    if metrics.error_rate > 0:
        score *= (1.0 - metrics.error_rate * config.error_penalty)
    
    return max(0.0, score)
```

**Key differences from OLTP**:
- Throughput less important (queries are longer, fewer per second)
- Memory efficiency more critical (large sorts/joins)
- I/O efficiency matters (scanning large datasets)

#### Mixed Workload Scoring

```python
def compute_mixed_score(metrics: PerformanceMetrics, config: MetricConfig) -> float:
    """
    Mixed workload: Balance between OLTP and OLAP
    
    Computes weighted average of OLTP and OLAP scores.
    """
    oltp_score = compute_oltp_score(metrics, config)
    olap_score = compute_olap_score(metrics, config)
    
    # 60% OLTP, 40% OLAP (adjustable)
    return 0.6 * oltp_score + 0.4 * olap_score
```

### Main Scoring Function

```python
def compute_score(
    metrics: PerformanceMetrics,
    config: MetricConfig
) -> float:
    """
    Compute composite performance score.
    
    Dispatches to workload-specific scoring function.
    Higher score = better performance.
    """
    if config.workload_type == WorkloadType.OLTP:
        return compute_oltp_score(metrics, config)
    elif config.workload_type == WorkloadType.OLAP:
        return compute_olap_score(metrics, config)
    else:  # MIXED
        return compute_mixed_score(metrics, config)
```

**Usage in PBT**:
```python
def evaluate_worker(worker: Worker) -> tuple[PerformanceMetrics, float]:
    # Apply configuration
    applicator.apply(worker.knob_config)
    
    # Run workload and collect metrics
    metrics = evaluator.run_workload()
    
    # Compute score for PBT optimization
    score = compute_score(metrics, metric_config)
    
    return metrics, score
```

---

## Component 3: Evaluator

**Location**: [src/tuner/evaluator/evaluator.py](../src/tuner/evaluator/evaluator.py)

### Purpose

The **Evaluator class** orchestrates the entire evaluation process:
1. Apply configuration to PostgreSQL
2. Execute workload
3. Collect performance metrics
4. Compute composite score

It serves as the **bridge** between PBT's Population and PostgreSQL database.

### EvaluatorConfig

```python
@dataclass
class EvaluatorConfig:
    workload_type: WorkloadType           # OLTP, OLAP, MIXED
    metric_config: MetricConfig           # Scoring weights
    connection_params: Dict[str, Any]     # DB connection
    warmup_queries: int = 100             # Queries before measurement
    measurement_duration: float = 60.0    # Measurement window (seconds)
    cooldown_duration: float = 5.0        # Wait after config change
```

**Why warmup?**
- Database caches (buffer pool) need to warm up
- First queries after config change may be atypical
- Warmup ensures steady-state measurement

**Why cooldown?**
- Some config changes require PostgreSQL to stabilize
- Connection pooling needs to adjust
- Background processes (autovacuum) need to settle

### Evaluation Flow

```python
def evaluate(
    self,
    knob_config: Dict[str, Any],
    worker_id: Optional[int] = None
) -> tuple[PerformanceMetrics, float]:
    """
    Main evaluation method.
    
    Flow:
    1. Apply configuration to PostgreSQL
    2. Wait cooldown period
    3. Execute warmup queries
    4. Measure performance during measurement window
    5. Collect metrics
    6. Compute score
    """
    
    logger.info(f"Applying configuration (worker {worker_id})...")
    self._apply_configuration(knob_config)
    
    logger.info(f"Cooldown period ({self.config.cooldown_duration}s)...")
    time.sleep(self.config.cooldown_duration)
    
    logger.info(f"Executing workload...")
    metrics = self._execute_workload()
    
    score = compute_score(metrics, self.config.metric_config)
    
    logger.info(f"Evaluation complete: score={score:.4f}")
    
    return metrics, score
```

### Configuration Application

```python
def _apply_configuration(self, knob_config: Dict[str, Any]):
    """
    Apply knob configuration to PostgreSQL.
    
    Uses KnobApplicator for safe, validated application.
    See CONFIGURATION_MANAGEMENT.md for details.
    """
    from src.tuner.utils.applicator import KnobApplicator, ApplicatorConfig
    
    applicator_config = ApplicatorConfig(
        persist=False,        # Don't save to postgresql.conf
        auto_reload=True,     # Reload configs that need it
        validate=True,        # Validate against pg_settings
        rollback_on_error=True  # Rollback if any param fails
    )
    
    applicator = KnobApplicator(applicator_config)
    
    try:
        result = applicator.apply(knob_config)
        if not result.success:
            logger.error(f"Config application failed: {result.message}")
            raise RuntimeError(f"Failed to apply config: {result.failed}")
    finally:
        applicator.disconnect()
```

### Workload Execution

The Evaluator uses a **Strategy Pattern** to support different workload types:

```python
class WorkloadExecutor(ABC):
    """Abstract base for workload-specific executors."""
    
    @abstractmethod
    def execute(
        self,
        connection: PostgresConnection,
        duration: float,
        warmup: int = 0
    ) -> PerformanceMetrics:
        """Execute workload and return metrics."""
```

#### SYSBENCH OLTP Executor

```python
class SysbenchOLTPExecutor(WorkloadExecutor):
    """
    SYSBENCH OLTP workload:
    - Point selects (SELECT ... WHERE id = ?)
    - Range selects (SELECT ... WHERE id BETWEEN ? AND ?)
    - Updates (UPDATE ... SET ... WHERE id = ?)
    - Inserts
    - Deletes
    """
    
    def execute(self, connection, duration, warmup=0):
        for _ in range(warmup):
            self._execute_random_query(connection)
        
        start_time = time.time()
        queries = []
        
        process = psutil.Process()
        cpu_start = process.cpu_percent()
        mem_start = process.memory_percent()
        
        while time.time() - start_time < duration:
            query_start = time.time()
            self._execute_random_query(connection)
            query_time = (time.time() - query_start) * 1000  # Convert to ms
            queries.append(query_time)
        
        cpu_end = process.cpu_percent()
        mem_end = process.memory_percent()
        
        metrics = self._compute_metrics(
            queries,
            duration,
            cpu=(cpu_start + cpu_end) / 2,
            mem=(mem_start + mem_end) / 2
        )
        
        return metrics
```

#### Custom Query Executor

```python
class CustomQueryExecutor(WorkloadExecutor):
    """
    Execute user-defined SQL queries.
    Useful for application-specific workloads.
    """
    
    def __init__(self, queries: List[str]):
        self.queries = queries
    
    def execute(self, connection, duration, warmup=0):
        # Similar structure to SYSBENCH
        # But executes user-provided queries
        pass
```

---

## System Monitoring with psutil

**Why psutil?** Initially, we approximated system metrics using database statistics. However, this proved **inaccurate** for actual resource usage.

### The Problem with Approximations

**Initial approach** (approximated):
```python
# BAD: Approximations based on database stats
cpu_util = 0.5 + random.uniform(-0.1, 0.1)  # Guess!
memory_util = 0.7 + random.uniform(-0.1, 0.1)  # Guess!
```

**Problems**:
1. **Not real usage**: Just random numbers, no correlation with actual system state
2. **Misleading for PBT**: Evolution driven by fake signals
3. **No validation**: Can't verify if config actually reduced CPU/memory

### The Solution: psutil Integration

**psutil** is a Python library for retrieving system and process information:

```python
import psutil

# System-wide metrics
cpu_percent = psutil.cpu_percent(interval=1.0)
memory_info = psutil.virtual_memory()
memory_percent = memory_info.percent

# Process-specific metrics
process = psutil.Process()
process_cpu = process.cpu_percent()
process_memory = process.memory_percent()

# I/O metrics
io_counters = psutil.disk_io_counters()
io_read_mb = io_counters.read_bytes / (1024 * 1024)
io_write_mb = io_counters.write_bytes / (1024 * 1024)
```

### Integration in Evaluator

```python
def _collect_system_metrics(self) -> Dict[str, float]:
    """
    Collect system metrics using psutil.
    
    Returns actual CPU, memory, and I/O usage.
    """
    # CPU utilization (system-wide)
    cpu_util = psutil.cpu_percent(interval=0.5) / 100.0
    
    # Memory utilization (system-wide)
    memory_info = psutil.virtual_memory()
    memory_util = memory_info.percent / 100.0
    
    # Disk I/O (system-wide)
    io_counters = psutil.disk_io_counters()
    io_read_mb = io_counters.read_bytes / (1024 * 1024)
    io_write_mb = io_counters.write_bytes / (1024 * 1024)
    
    return {
        'cpu_utilization': cpu_util,
        'memory_utilization': memory_util,
        'io_read_mb': io_read_mb,
        'io_write_mb': io_write_mb
    }
```

### Why This Matters for PBT

**With psutil**, PBT can optimize for **real resource efficiency**:

```
Configuration A:
  shared_buffers = 256MB
  work_mem = 4MB
  → CPU: 75%, Memory: 60%, Score: 0.82

Configuration B:
  shared_buffers = 512MB
  work_mem = 16MB
  → CPU: 65%, Memory: 70%, Score: 0.86

PBT decision: Config B better (higher score, lower CPU despite higher memory)
```

**Without psutil**, these would be random/approximated, leading to **false optimization signals**.

### Requirement

psutil must be installed (already in `requirements.txt`):
```bash
pip install psutil
```

See [ENVIRONMENT_SETUP.md](./ENVIRONMENT_SETUP.md) for installation instructions.

---

## Workload Types and Optimization Goals

### OLTP (Online Transaction Processing)

**Characteristics**:
- Short, simple queries (point selects, updates)
- High concurrency (many simultaneous transactions)
- Low latency critical (user-facing applications)

**Optimization Goals**:
1. **Minimize latency** (p95 < 100ms typical target)
2. **Maximize throughput** (TPS as high as possible)
3. **Resource efficiency** (low CPU/memory per transaction)

**Typical Workloads**:
- SYSBENCH OLTP
- TPC-C
- Web application queries

**Key PostgreSQL Knobs**:
- `shared_buffers`: Cache for frequently accessed data
- `work_mem`: Sort/hash operations
- `max_connections`: Concurrent connections
- `checkpoint_completion_target`: Write smoothing

### OLAP (Online Analytical Processing)

**Characteristics**:
- Complex, long-running queries (aggregations, joins)
- Low concurrency (few simultaneous queries)
- Query execution time critical

**Optimization Goals**:
1. **Minimize query execution time**
2. **Efficient memory usage** (large sorts/joins)
3. **Good cache utilization** (scan large datasets)

**Typical Workloads**:
- TPC-H
- Business intelligence queries
- Data warehouse analytics

**Key PostgreSQL Knobs**:
- `work_mem`: Large sorts/joins
- `maintenance_work_mem`: Index creation
- `effective_cache_size`: Query planner hint
- `max_parallel_workers_per_gather`: Parallelism

### Mixed Workload

**Characteristics**:
- Combination of OLTP and OLAP queries
- Variable query complexity
- Competing optimization goals

**Optimization Goals**:
- **Balance** latency and throughput
- Handle diverse query patterns
- Resource efficiency across workload spectrum

**Challenge**: Configurations optimal for one workload type may degrade the other.

---

## Design Decisions

### 1. Structured PerformanceMetrics Dataclass

**Decision**: Use typed dataclass instead of dictionary.

**Why?**
- **Type safety**: IDE autocomplete, type checking
- **Self-documenting**: Field names clarify meaning/units
- **Validation**: Can add validation in `__post_init__`
- **Serialization**: Easy to/from dictionary for checkpointing

### 2. Workload-Specific Scoring Functions

**Decision**: Separate scoring functions for OLTP, OLAP, MIXED.

**Why?**
- **Different optimization goals**: OLTP prioritizes latency, OLAP prioritizes query time
- **Appropriate weights**: MetricConfig can be customized per workload
- **Clarity**: Explicit about what each workload optimizes
- **Flexibility**: Easy to add new workload types

### 3. psutil for System Monitoring

**Decision**: Use psutil instead of approximations.

**Why?**
- **Accuracy**: Real system metrics vs random guesses
- **Validation**: Can verify config changes affect resources
- **Trust**: PBT optimization driven by actual performance
- **Cross-platform**: psutil works on Windows, Linux, macOS

**Trade-off**: Adds dependency, but psutil is stable and widely used.

### 4. Warmup and Cooldown Periods

**Decision**: Include warmup and cooldown in evaluation flow.

**Why?**
- **Steady state**: Avoid measuring transient effects
- **Fair comparison**: All configs measured under similar conditions
- **Stability**: Allow PostgreSQL to adjust to new settings

**Typical values**:
- Warmup: 100 queries or 10-30 seconds
- Cooldown: 5-10 seconds

### 5. Strategy Pattern for Workload Executors

**Decision**: Abstract `WorkloadExecutor` base class with concrete implementations.

**Why?**
- **Extensibility**: Easy to add new workload types
- **Separation of concerns**: Evaluator orchestrates, Executor implements workload
- **Testability**: Can mock WorkloadExecutor for unit tests
- **Reusability**: Executors can be used outside Evaluator

### 6. Composite Scoring Function

**Decision**: Single score = weighted sum of multiple metrics.

**Why?**
- **PBT requirement**: Needs single value to rank workers
- **Multi-objective optimization**: Captures trade-offs (latency vs throughput vs resources)
- **Tunable**: Weights can be adjusted for different priorities
- **Interpretable**: Easy to understand contribution of each metric

### 7. Error Rate Penalty

**Decision**: Multiply score by (1 - error_rate × penalty).

**Why?**
- **Correctness first**: Configurations that cause errors should be heavily penalized
- **Proportional**: Small error rates → small penalty, high error rates → large penalty
- **Tunable**: `error_penalty` multiplier can be adjusted (default 10.0)

**Example**:
```
Score before penalty: 0.85
Error rate: 0.05 (5% of queries failed)
Error penalty: 10.0

Final score: 0.85 × (1 - 0.05 × 10) = 0.85 × 0.5 = 0.425
```

---

## Related Documentation

### Prerequisites

- **[Environment Setup](./ENVIRONMENT_SETUP.md)**: Install psutil and other dependencies
- **[PostgreSQL Connection](./POSTGRESQL_CONNECTION_AND_KNOBS.md)**: Database connection management

### Related Components

- **[PBT Core Components](./PBT_CORE_COMPONENTS.md)**: Worker, Evolution, Population classes that use the Evaluator
- **[Configuration Management](./CONFIGURATION_MANAGEMENT.md)**: KnobApplicator used by Evaluator to apply configs

### Integration

The Evaluator is called by the Population during training:

```python
# From Population.evaluate_generation()
def evaluate_fn(worker: Worker) -> tuple[PerformanceMetrics, float]:
    return evaluator.evaluate(worker.knob_config, worker.worker_id)

population.evaluate_generation(evaluate_fn, parallel=True)
```

See [PBT_CORE_COMPONENTS.md](./PBT_CORE_COMPONENTS.md) for complete integration details.

---

## Summary

The Performance Evaluation System provides the **fitness function** for PBT optimization:

1. **PerformanceMetrics**: Structured container for all performance measurements
2. **Scoring System**: Converts multiple metrics → single composite score (workload-dependent)
3. **Evaluator**: Orchestrates config application, workload execution, metric collection
4. **psutil Integration**: Accurate system monitoring (CPU, memory, I/O)

**Key Insight**: The scoring function is **workload-dependent**—OLTP prioritizes low latency and high throughput, OLAP prioritizes query execution time and resource efficiency. PBT uses these scores to evolve configurations toward better performance.

**File Locations**:
- Metrics: [src/tuner/evaluator/metrics.py](../src/tuner/evaluator/metrics.py)
- Evaluator: [src/tuner/evaluator/evaluator.py](../src/tuner/evaluator/evaluator.py)
- Tests: [src/tuner/evaluator/\_\_main\_\_.py](../src/tuner/evaluator/__main__.py)