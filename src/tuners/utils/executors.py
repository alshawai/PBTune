"""
Workload-executor construction shared across tuning strategies.

PBT's ``__init__`` contains a three-way branch (sysbench / tpch / custom) that
constructs a workload executor, extracts workload features, and derives a
snapshot identifier. Every strategy needs the same thing. This module lifts
that branch into a single factory returning a small result bundle.

The executors (``SysbenchExecutor``, ``TPCHExecutor``, template loaders) and
the ``WorkloadFeatureExtractor`` are reused as-is (copy-not-refactor).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from src.benchmarks.sysbench.executor import SysbenchExecutor
from src.benchmarks.tpch.executor import TPCHExecutor
from src.benchmarks.workload import (
    WorkloadFileLoader, extract_workload_template_metadata
)
from src.utils.metrics import WorkloadType
from src.utils.scoring.workload_features import WorkloadFeatureExtractor
from src.utils.types import BenchmarkConfig


@dataclass
class WorkloadBundle:
    """
    Everything a tuner needs to drive a workload.

    Attributes
    ----------
    executor
        The constructed workload executor (sysbench / tpch / template).
    benchmark_name
        Canonical benchmark driver name persisted to the session JSON.
    workload_type
        Resolved ``WorkloadType`` enum member.
    workload_features
        Extracted feature dict consumed by the composite scorer.
    snapshot_identifier
        Stable identifier used for baseline snapshot reuse.
    enable_snapshots
        Whether snapshot restoration is meaningful for this workload
        (False for read-only sysbench and all TPC-H runs).
    """

    executor: Any
    benchmark_name: str
    workload_type: WorkloadType
    workload_features: Dict[str, float] = field(default_factory=dict)
    snapshot_identifier: str = ""
    enable_snapshots: bool = True


def build_workload_bundle(
    *,
    benchmark: Optional[str],
    benchmark_config: BenchmarkConfig,
    workload_type: WorkloadType,
    cpu_cores: int,
    workload_file: Optional[str] = None,
) -> WorkloadBundle:
    """
    Construct the workload executor + features for a tuning run.

    Mirrors PBT's benchmark branch. ``benchmark`` selects the driver:
    'sysbench', 'tpch', or any other value / None for a custom template
    workload loaded from ``workload_file``.
    """
    extractor = WorkloadFeatureExtractor()

    if benchmark == "sysbench":
        tables = benchmark_config.sysbench_tables
        table_size = benchmark_config.sysbench_table_size
        script = benchmark_config.sysbench_workload

        executor = SysbenchExecutor(
            tables=tables, table_size=table_size, script=script
        )
        threads = int(getattr(executor, "threads", 8))
        features = extractor.extract_sysbench_features(
            script=script,
            threads=threads,
            cpu_cores=int(cpu_cores or 1),
            table_size=table_size,
            tables=tables,
        )
        return WorkloadBundle(
            executor=executor,
            benchmark_name="sysbench",
            workload_type=WorkloadType.OLTP,
            workload_features=features,
            snapshot_identifier=f"sysbench_{script}_t{tables}_s{table_size}",
            enable_snapshots=(script != "oltp_read_only"),
        )

    if benchmark == "tpch":
        scale_factor = benchmark_config.scale_factor
        executor = TPCHExecutor(scale_factor=scale_factor)
        features = extractor.extract_tpch_features(
            scale_factor=scale_factor,
            warmup_passes=benchmark_config.warmup_passes,
            queries=executor.queries,
        )
        return WorkloadBundle(
            executor=executor,
            benchmark_name="tpch",
            workload_type=WorkloadType.OLAP,
            workload_features=features,
            snapshot_identifier=f"tpch_sf{scale_factor}",
            enable_snapshots=False,
        )

    # Custom template workload.
    if not workload_file:
        raise ValueError(
            "A workload_file is required for custom (non-sysbench/tpch) workloads"
        )
    executor = WorkloadFileLoader.load_from_file(workload_file)
    benchmark_name = workload_type.value

    template_metadata = extract_workload_template_metadata(executor)
    features = extractor.extract_template_features(metadata=template_metadata)
    return WorkloadBundle(
        executor=executor,
        benchmark_name=benchmark_name,
        workload_type=workload_type,
        workload_features=features,
        snapshot_identifier=f"{benchmark_name}_sf{benchmark_config.scale_factor}",
        enable_snapshots=True,
    )
