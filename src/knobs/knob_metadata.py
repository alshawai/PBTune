"""
Knob Tuning Metadata and Preprocessing
=======================================

This module defines tuning-specific metadata for PostgreSQL knobs that is
NOT available in pg_settings but is essential for optimization:

1. Tuning ranges (different from PostgreSQL min/max)
2. Scale type (linear vs logarithmic)
3. Impact tier (minimal, core, standard, extensive)
4. Recommended values and bounds

This metadata is overlaid onto knobs retrieved from pg_settings to create
a complete tuning specification.
"""

from typing import Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class TuningMetadata:
    """
    Tuning-specific metadata for a knob.
    
    Attributes
    ----------
    tuning_min : Optional[Any]
        Minimum value for tuning (may differ from PostgreSQL min)
    tuning_max : Optional[Any]
        Maximum value for tuning (may differ from PostgreSQL max)
    scale : str
        'linear' or 'log' - how to sample/perturb this knob
    impact_tier : str
        Categorization for preset groups: 'minimal', 'core', 'standard', 'extensive'
        This determines which preset knob space includes this knob.
    tuning_priority : int
        Fine-grained priority within a tier (1-5, where 1 is highest)
        Used for sorting within tiers and for advanced selection strategies.
        Example: Two 'core' knobs may have different priorities (1 vs 2)
    notes : str
        Tuning-specific notes
        
    Distinction:
    -----------
    - impact_tier: Categorical grouping (which preset to include in)
    - tuning_priority: Numerical ranking (importance within and across tiers)
    
    Example: 
    - shared_buffers: tier='minimal', priority=1 (most critical)
    - checkpoint_timeout: tier='core', priority=2 (important but secondary)
    - enable_nestloop: tier='standard', priority=4 (fine-tuning)
    """

    tuning_min: Optional[Any] = None
    tuning_max: Optional[Any] = None
    scale: str = "linear"
    impact_tier: str = "extensive"
    tuning_priority: int = 5
    notes: str = ""

KNOB_TUNING_METADATA: Dict[str, TuningMetadata] = {
    "shared_buffers": TuningMetadata(
        tuning_min=16384,  # 128MB (16384 × 8kB blocks)
        tuning_max=131072,  # 1GB (131072 × 8kB blocks)
        scale="log",
        impact_tier="minimal",
        tuning_priority=1,
        notes="Most impactful knob. Log scale because doubling matters more than addition."
    ),

    "effective_cache_size": TuningMetadata(
        tuning_min=65536,  # 512MB (65536 × 8kB blocks)
        tuning_max=1048576,  # 8GB (1048576 × 8kB blocks)
        scale="log",
        impact_tier="minimal",
        tuning_priority=1,
        notes="Planner's OS cache estimate. Doesn't allocate memory, only affects plans."
    ),

    "work_mem": TuningMetadata(
        tuning_min=4096,  # 4MB (kB)
        tuning_max=65536,
        scale="log",
        impact_tier="minimal",
        tuning_priority=1,
        notes="Per-operation memory. Total can be work_mem * connections * operations_per_query"
    ),

    "random_page_cost": TuningMetadata(
        tuning_min=0.1,
        tuning_max=4.0,
        scale="linear",
        impact_tier="minimal",
        tuning_priority=1,
        notes="Critical for index vs seqscan decisions. SSD: 1.0-1.5, HDD: 3.0-4.0"
    ),

    "max_parallel_workers_per_gather": TuningMetadata(
        tuning_min=0,
        tuning_max=4,  # Match available cores
        scale="linear",
        impact_tier="minimal",
        tuning_priority=1,
        notes="Parallelism for analytical queries. Limited by CPU cores."
    ),

    "maintenance_work_mem": TuningMetadata(
        tuning_min=65536,  # 64MB (kB)
        tuning_max=262144,
        scale="log",
        impact_tier="core",
        tuning_priority=2,
        notes="For VACUUM, CREATE INDEX. Can be larger than work_mem."
    ),

    "wal_buffers": TuningMetadata(
        tuning_min=64,  # 512kB (64 × 8kB blocks)
        tuning_max=2048,  # 16MB (2048 × 8kB blocks)
        scale="log",
        impact_tier="core",
        tuning_priority=2,
        notes="WAL buffer size. Default -1 means auto (1/32 of shared_buffers)."
    ),

    "effective_io_concurrency": TuningMetadata(
        tuning_min=0,
        tuning_max=200,
        scale="linear",
        impact_tier="core",
        tuning_priority=2,
        notes="Expected concurrent I/O. SSD: 100-200, HDD: 1-2"
    ),

    "default_statistics_target": TuningMetadata(
        tuning_min=10,
        tuning_max=10000,
        scale="log",
        impact_tier="core",
        tuning_priority=2,
        notes="Statistics sample size for ANALYZE. Higher = better plans, slower ANALYZE."
    ),

    "checkpoint_timeout": TuningMetadata(
        tuning_min=30,  # 30 seconds
        tuning_max=3600,  # 1 hour
        scale="log",
        impact_tier="core",
        tuning_priority=2,
        notes="Max time between automatic checkpoints. Affects recovery time."
    ),

    "checkpoint_completion_target": TuningMetadata(
        tuning_min=0.1,
        tuning_max=0.9,
        scale="linear",
        impact_tier="core",
        tuning_priority=2,
        notes="Spread checkpoint I/O over this fraction of checkpoint_timeout."
    ),

    "max_connections": TuningMetadata(
        tuning_min=50,
        tuning_max=200,
        scale="linear",
        impact_tier="core",
        tuning_priority=3,
        notes="Max concurrent connections. Requires restart. High values increase memory."
    ),

    "max_worker_processes": TuningMetadata(
        tuning_min=4,
        tuning_max=16,
        scale="linear",
        impact_tier="core",
        tuning_priority=3,
        notes="Max background workers. Requires restart. Must be >= max_parallel_workers."
    ),

    "maintenance_io_concurrency": TuningMetadata(
        tuning_min=0,
        tuning_max=200,
        scale="linear",
        impact_tier="standard",
        tuning_priority=3,
        notes="Maintenance I/O concurrency for VACUUM/CREATE INDEX, " \
        "similar semantics to effective_io_concurrency."
    ),

    "io_workers": TuningMetadata(
        tuning_min=1,
        tuning_max=16,
        scale="linear",
        impact_tier="standard",
        tuning_priority=3,
        notes="I/O worker count (PG17+); bounded to practical CPU-core-aligned range."
    ),

    "max_parallel_apply_workers_per_subscription": TuningMetadata(
        tuning_min=0,
        tuning_max=8,
        scale="linear",
        impact_tier="extensive",
        tuning_priority=5,
        notes="Logical replication apply parallelism; low-impact for " \
        "non-replication benchmarks but safely tunable."
    ),

    "hash_mem_multiplier": TuningMetadata(
        tuning_min=1.0,
        tuning_max=8.0,
        scale="linear",
        impact_tier="standard",
        tuning_priority=3,
        notes="Hash operation memory multiplier; bounded to avoid " \
        "aggressive memory oversubscription."
    ),

    "autovacuum_worker_slots": TuningMetadata(
        tuning_min=1,
        tuning_max=16,
        scale="linear",
        impact_tier="standard",
        tuning_priority=4,
        notes="Autovacuum worker slot capacity; aligned with practical worker process limits."
    ),

    "max_wal_senders": TuningMetadata(
        tuning_min=0,
        tuning_max=10,
        scale="linear",
        impact_tier="extensive",
        tuning_priority=5,
        notes="WAL sender process cap; safely bounded for " \
        "environments without heavy replication fanout."
    ),

    "max_logical_replication_workers": TuningMetadata(
        tuning_min=0,
        tuning_max=10,
        scale="linear",
        impact_tier="extensive",
        tuning_priority=5,
        notes="Logical replication worker cap; bounded for " \
        "stability in non-replication-centric tuning runs."
    ),

    "superuser_reserved_connections": TuningMetadata(
        tuning_min=0,
        tuning_max=5,
        scale="linear",
        impact_tier="extensive",
        tuning_priority=5,
        notes="Reserved connection slots for superusers; " \
        "tuned conservatively to prevent starvation."
    ),

    "reserved_connections": TuningMetadata(
        tuning_min=0,
        tuning_max=5,
        scale="linear",
        impact_tier="extensive",
        tuning_priority=5,
        notes="Reserved connection slots; conservative bounds preserve user connection capacity."
    ),

    "notify_buffers": TuningMetadata(
        tuning_min=4,
        tuning_max=64,
        scale="log",
        impact_tier="extensive",
        tuning_priority=5,
        notes="LISTEN/NOTIFY buffers (pages); bounded for safe memory footprint."
    ),

    "multixact_member_buffers": TuningMetadata(
        tuning_min=4,
        tuning_max=64,
        scale="log",
        impact_tier="extensive",
        tuning_priority=5,
        notes="MultiXact member SLRU buffers (pages); bounded for stable memory usage."
    ),

    "multixact_offset_buffers": TuningMetadata(
        tuning_min=4,
        tuning_max=64,
        scale="log",
        impact_tier="extensive",
        tuning_priority=5,
        notes="MultiXact offset SLRU buffers (pages); bounded for stable memory usage."
    ),

    "seq_page_cost": TuningMetadata(
        tuning_min=0.1,
        tuning_max=2.0,
        scale="linear",
        impact_tier="standard",
        tuning_priority=3,
        notes="Cost of sequential page fetch. Usually kept at 1.0 as baseline."
    ),

    "cpu_tuple_cost": TuningMetadata(
        tuning_min=0.001,
        tuning_max=0.1,
        scale="log",
        impact_tier="standard",
        tuning_priority=3,
        notes="Cost of processing each row."
    ),

    "cpu_index_tuple_cost": TuningMetadata(
        tuning_min=0.0001,
        tuning_max=0.01,
        scale="log",
        impact_tier="standard",
        tuning_priority=3,
        notes="Cost of processing each index entry."
    ),

    "cpu_operator_cost": TuningMetadata(
        tuning_min=0.0001,
        tuning_max=0.01,
        scale="log",
        impact_tier="standard",
        tuning_priority=3,
        notes="Cost of executing operators/functions."
    ),

    "max_wal_size": TuningMetadata(
        tuning_min=80,  # MB
        tuning_max=10240,  # 10GB
        scale="log",
        impact_tier="standard",
        tuning_priority=3,
        notes="Max WAL size before forced checkpoint."
    ),

    "min_wal_size": TuningMetadata(
        tuning_min=80,  # MB
        tuning_max=2048,  # 2GB
        scale="log",
        impact_tier="standard",
        tuning_priority=3,
        notes="Minimum WAL size to keep."
    ),

    "max_parallel_workers": TuningMetadata(
        tuning_min=0,
        tuning_max=16,
        scale="linear",
        impact_tier="standard",
        tuning_priority=3,
        notes="Max parallel workers system-wide. Must be <= max_worker_processes."
    ),

    "max_parallel_maintenance_workers": TuningMetadata(
        tuning_min=0,
        tuning_max=4,
        scale="linear",
        impact_tier="standard",
        tuning_priority=3,
        notes="Max parallel workers for maintenance (CREATE INDEX, VACUUM)."
    ),

    "parallel_setup_cost": TuningMetadata(
        tuning_min=1.0,
        tuning_max=10000.0,
        scale="log",
        impact_tier="standard",
        tuning_priority=4,
        notes="Cost of starting parallel workers."
    ),

    "parallel_tuple_cost": TuningMetadata(
        tuning_min=0.0001,
        tuning_max=10.0,
        scale="log",
        impact_tier="standard",
        tuning_priority=4,
        notes="Cost of transferring tuples between workers."
    ),

    "bgwriter_delay": TuningMetadata(
        tuning_min=10,
        tuning_max=2000,
        scale="log",
        impact_tier="standard",
        tuning_priority=4,
        notes="ms between bgwriter rounds.",
    ),

    "bgwriter_lru_maxpages": TuningMetadata(
        tuning_min=0,
        tuning_max=1000,
        scale="linear",
        impact_tier="standard",
        tuning_priority=4,
        notes="pages per bgwriter round.",
    ),

    "bgwriter_flush_after": TuningMetadata(
        tuning_min=0,
        tuning_max=256,
        scale="linear",
        impact_tier="extensive",
        tuning_priority=5,
        notes="pages written by bgwriter before OS flush.",
    ),

    "bgwriter_lru_multiplier": TuningMetadata(
        tuning_min=0.0,
        tuning_max=10.0,
        scale="linear",
        impact_tier="extensive",
        tuning_priority=5,
        notes="multiplier for pages to write based on recent usage.",
    ),

    "wal_writer_delay": TuningMetadata(
        tuning_min=1,
        tuning_max=5000,
        scale="log",
        impact_tier="standard",
        tuning_priority=4,
        notes="ms between WAL flushes.",
    ),

    "commit_delay": TuningMetadata(
        tuning_min=0,
        tuning_max=10000,
        scale="linear",
        impact_tier="extensive",
        tuning_priority=4,
        notes="microseconds to wait for group commit.",
    ),

    "commit_siblings": TuningMetadata(
        tuning_min=0,
        tuning_max=20,
        scale="linear",
        impact_tier="extensive",
        tuning_priority=4,
        notes="concurrent active transactions to trigger commit_delay.",
    ),

    "checkpoint_flush_after": TuningMetadata(
        tuning_min=0,
        tuning_max=256,
        scale="linear",
        impact_tier="extensive",
        tuning_priority=5,
        notes="pages written by checkpointer before OS flush.",
    ),

    "vacuum_cost_limit": TuningMetadata(
        tuning_min=1,
        tuning_max=2000,
        scale="linear",
        impact_tier="standard",
        tuning_priority=4,
        notes="aggregate cost cap for vacuum.",
    ),

    "vacuum_cost_page_dirty": TuningMetadata(
        tuning_min=0,
        tuning_max=1000,
        scale="linear",
        impact_tier="extensive",
        tuning_priority=5,
        notes="cost of dirtying a page.",
    ),

    "vacuum_cost_page_hit": TuningMetadata(
        tuning_min=0,
        tuning_max=100,
        scale="linear",
        impact_tier="extensive",
        tuning_priority=5,
        notes="cost of vacuuming a buffer-hit page.",
    ),

    "vacuum_cost_page_miss": TuningMetadata(
        tuning_min=0,
        tuning_max=1000,
        scale="linear",
        impact_tier="extensive",
        tuning_priority=5,
        notes="cost of vacuuming a disk-read page.",
    ),

    "autovacuum_vacuum_cost_limit": TuningMetadata(
        tuning_min=-1,
        tuning_max=2000,
        scale="linear",
        impact_tier="extensive",
        tuning_priority=4,
        notes="per-worker autovacuum cost limit.",
    ),

    "autovacuum_vacuum_cost_delay": TuningMetadata(
        tuning_min=-1.0,
        tuning_max=50.0,
        scale="linear",
        impact_tier="extensive",
        tuning_priority=4,
        notes="ms delay after cost limit is hit.",
    ),

    "autovacuum_vacuum_scale_factor": TuningMetadata(
        tuning_min=0.01,
        tuning_max=0.5,
        scale="linear",
        impact_tier="extensive",
        tuning_priority=4,
        notes="fraction of table modified for vacuum.",
    ),

    "autovacuum_analyze_scale_factor": TuningMetadata(
        tuning_min=0.01,
        tuning_max=0.5,
        scale="linear",
        impact_tier="extensive",
        tuning_priority=4,
        notes="fraction of table modified for analyze.",
    ),

    "autovacuum_vacuum_insert_scale_factor": TuningMetadata(
        tuning_min=0.01,
        tuning_max=0.5,
        scale="linear",
        impact_tier="extensive",
        tuning_priority=4,
        notes="fraction of table inserted for vacuum.",
    ),

    "vacuum_buffer_usage_limit": TuningMetadata(
        tuning_min=128,
        tuning_max=2048,  # 16MB in 8k pages
        scale="linear",
        impact_tier="extensive",
        tuning_priority=4,
        notes="buffer usage limit for vacuum (pages).",
    ),

    "commit_timestamp_buffers": TuningMetadata(
        tuning_min=0,
        tuning_max=1024,
        scale="log",
        impact_tier="extensive",
        tuning_priority=5,
        notes="SLRU buffers for commit timestamps (must be multiple of 16).",
    ),

    "serializable_buffers": TuningMetadata(
        tuning_min=16,
        tuning_max=1024,
        scale="log",
        impact_tier="extensive",
        tuning_priority=5,
        notes="SLRU buffers for serializable transactions (must be multiple of 16).",
    ),

    "subtransaction_buffers": TuningMetadata(
        tuning_min=0,
        tuning_max=1024,
        scale="log",
        impact_tier="extensive",
        tuning_priority=5,
        notes="SLRU buffers for subtransactions (must be multiple of 16).",
    ),

    "transaction_buffers": TuningMetadata(
        tuning_min=0,
        tuning_max=1024,
        scale="log",
        impact_tier="extensive",
        tuning_priority=5,
        notes="SLRU buffers for transactions (must be multiple of 16).",
    ),

    "io_combine_limit": TuningMetadata(
        tuning_min=1,
        tuning_max=128,
        scale="linear",
        impact_tier="extensive",
        tuning_priority=5,
        notes="I/O combine limit.",
    ),

    "io_max_combine_limit": TuningMetadata(
        tuning_min=1,
        tuning_max=128,
        scale="linear",
        impact_tier="extensive",
        tuning_priority=5,
        notes="Max I/O combine limit.",
    ),

    "io_max_concurrency": TuningMetadata(
        tuning_min=-1,
        tuning_max=256,
        scale="linear",
        impact_tier="extensive",
        tuning_priority=5,
        notes="Max I/O concurrency.",
    ),

    "backend_flush_after": TuningMetadata(
        tuning_min=0,
        tuning_max=256,
        scale="linear",
        impact_tier="extensive",
        tuning_priority=5,
        notes="pages written by backend before OS flush.",
    ),

    "min_parallel_table_scan_size": TuningMetadata(
        tuning_min=0,
        tuning_max=65536,
        scale="log",
        impact_tier="standard",
        tuning_priority=4,
        notes="pages required to trigger parallel table scan.",
    ),

    "min_parallel_index_scan_size": TuningMetadata(
        tuning_min=0,
        tuning_max=16384,
        scale="log",
        impact_tier="standard",
        tuning_priority=4,
        notes="pages required to trigger parallel index scan.",
    ),

    "geqo_effort": TuningMetadata(
        tuning_min=1,
        tuning_max=10,
        scale="linear",
        impact_tier="extensive",
        tuning_priority=5,
        notes="GEQO effort level.",
    ),

    "geqo_seed": TuningMetadata(
        tuning_min=0.0,
        tuning_max=1.0,
        scale="linear",
        impact_tier="extensive",
        tuning_priority=5,
        notes="GEQO random seed.",
    ),

    "geqo_selection_bias": TuningMetadata(
        tuning_min=1.5,
        tuning_max=2.0,
        scale="linear",
        impact_tier="extensive",
        tuning_priority=5,
        notes="GEQO selection bias.",
    ),

    "cursor_tuple_fraction": TuningMetadata(
        tuning_min=0.0,
        tuning_max=1.0,
        scale="linear",
        impact_tier="extensive",
        tuning_priority=5,
        notes="planner estimator for cursor retrieval fraction.",
    ),

    "recursive_worktable_factor": TuningMetadata(
        tuning_min=0.1,
        tuning_max=100.0,
        scale="log",
        impact_tier="extensive",
        tuning_priority=5,
        notes="planner multiplier for recursive queries.",
    ),

    "vacuum_freeze_min_age": TuningMetadata(
        tuning_min=0,
        tuning_max=100000000,
        scale="log",
        impact_tier="extensive",
        tuning_priority=4,
        notes="minimum age before freezing tuples.",
    ),

    "vacuum_multixact_freeze_min_age": TuningMetadata(
        tuning_min=0,
        tuning_max=100000000,
        scale="log",
        impact_tier="extensive",
        tuning_priority=4,
        notes="minimum age before freezing multixacts.",
    ),

    "autovacuum": TuningMetadata(
        tuning_min=None,  # Boolean
        tuning_max=None,
        scale="categorical",
        impact_tier="standard",
        tuning_priority=3,
        notes="Enable autovacuum. Usually keep on."
    ),

    "autovacuum_max_workers": TuningMetadata(
        tuning_min=1,
        tuning_max=8,
        scale="linear",
        impact_tier="standard",
        tuning_priority=4,
        notes="Max autovacuum worker processes."
    ),

    "autovacuum_naptime": TuningMetadata(
        tuning_min=1,  # seconds
        tuning_max=600,  # 10 minutes
        scale="log",
        impact_tier="standard",
        tuning_priority=4,
        notes="Time between autovacuum runs."
    ),

    "temp_buffers": TuningMetadata(
        tuning_min=1024,  # 8MB (8kB blocks)
        tuning_max=4096,  # 32MB (constrained)
        scale="log",
        impact_tier="standard",
        tuning_priority=4,
        notes="Temp buffer size per session."
    ),

    "enable_seqscan": TuningMetadata(
        scale="categorical",
        impact_tier="standard",
        tuning_priority=4,
        notes="Enable sequential scans. Usually leave on."
    ),

    "enable_indexscan": TuningMetadata(
        scale="categorical",
        impact_tier="standard",
        tuning_priority=4,
        notes="Enable index scans. Usually leave on."
    ),

    "enable_bitmapscan": TuningMetadata(
        scale="categorical",
        impact_tier="standard",
        tuning_priority=4,
        notes="Enable bitmap scans."
    ),

    "enable_hashjoin": TuningMetadata(
        scale="categorical",
        impact_tier="standard",
        tuning_priority=4,
        notes="Enable hash joins."
    ),

    "enable_mergejoin": TuningMetadata(
        scale="categorical",
        impact_tier="standard",
        tuning_priority=4,
        notes="Enable merge joins."
    ),

    "enable_nestloop": TuningMetadata(
        scale="categorical",
        impact_tier="standard",
        tuning_priority=4,
        notes="Enable nested loop joins."
    ),
}


# Tier definitions
IMPACT_TIERS = {
    "minimal": [
        k for k, v in KNOB_TUNING_METADATA.items() if v.impact_tier == "minimal"
    ],

    "core": [
        k for k, v in KNOB_TUNING_METADATA.items()
        if v.impact_tier in ("minimal", "core")
    ],
    "standard": [
        k for k, v in KNOB_TUNING_METADATA.items()
        if v.impact_tier in ("minimal", "core", "standard")
    ],

    "extensive": None,  # Will include all tunable knobs from pg_settings
}


def get_knobs_by_tier(tier: str) -> list:
    """Get list of knob names for a specific tier"""
    tier_lower = tier.lower()
    if tier_lower not in IMPACT_TIERS:
        raise ValueError(f"Unknown tier: {tier}. Must be one of {list(IMPACT_TIERS.keys())}")
    return IMPACT_TIERS[tier_lower]  # type: ignore
