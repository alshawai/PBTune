"""Worker-resource resolution shared across tuning strategies.

Both PBT and BO contain the same branch: if the caller supplied any manual
resource override (RAM, CPUs, or a disk budget), route through
``resolve_manual_worker_resources``; otherwise auto-detect via
``detect_worker_resources``. This module lifts that branch into a single
helper so every strategy resolves resources identically.

The underlying ``src/utils/hardware_info`` primitives are reused as-is; this
is purely a thin dispatch wrapper (copy-not-refactor).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from src.utils.hardware_info import (
    WorkerResources,
    detect_worker_resources,
    resolve_manual_worker_resources,
)


def resolve_worker_resources(
    *,
    num_workers: int,
    data_path: Path,
    worker_ram: Optional[str] = None,
    worker_cpus: Optional[Any] = None,
    worker_disk_read_bps: Optional[int] = None,
    worker_disk_write_bps: Optional[int] = None,
    worker_disk_read_iops: Optional[int] = None,
    worker_disk_write_iops: Optional[int] = None,
    probe_disk: bool = True,
) -> WorkerResources:
    """Resolve per-worker hardware resources, honoring manual overrides.

    If any of the RAM / CPU / disk-budget overrides are provided, the manual
    resolver is used (and disk is probed unless an explicit budget covers it).
    Otherwise resources are auto-detected from the host, partitioned across
    ``num_workers``.

    Parameters mirror the override surface of both incumbent tuners. See
    ``src/utils/hardware_info`` for the resolution semantics.
    """
    manual_disk_provided = any(
        v is not None
        for v in (
            worker_disk_read_bps,
            worker_disk_write_bps,
            worker_disk_read_iops,
            worker_disk_write_iops,
        )
    )

    if worker_ram is not None or worker_cpus is not None or manual_disk_provided:
        return resolve_manual_worker_resources(
            worker_ram=worker_ram,
            worker_cpus=worker_cpus,
            num_workers=num_workers,
            data_path=data_path,
            worker_disk_read_bps=worker_disk_read_bps,
            worker_disk_write_bps=worker_disk_write_bps,
            worker_disk_read_iops=worker_disk_read_iops,
            worker_disk_write_iops=worker_disk_write_iops,
            probe_disk=probe_disk,
        )

    return detect_worker_resources(
        num_workers,
        data_path=data_path,
        probe_disk=probe_disk,
    )
