"""
Hardware Information Detection
==============================

Detects and reports system hardware characteristics for reproducibility
and provenance tracking in tuning results.

Captures CPU, memory, disk, OS, and PostgreSQL version information.
All detection functions are designed to fail gracefully, returning
"unknown" values rather than raising exceptions.
"""

import json
import logging
import os
import platform
import shutil
import subprocess
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional
import math
from dataclasses import dataclass

import psutil

from src.utils.logger.setup import get_logger
from src.utils.logger.context import get_color_context

LOGGER = get_logger("HardwareInfo")
COLORS = get_color_context()


@dataclass
class WorkerResources:
    """Per-worker hardware resources for hardware-aware knob ranges.

    Disk bandwidth/IOPS fields default to ``0`` (= unlimited / unenforced)
    so legacy call sites that construct ``WorkerResources(ram_bytes=...,
    cpu_cores=..., disk_type=...)`` remain valid. When non-zero, they
    are enforced per worker container via Docker's ``device_read_bps``,
    ``device_write_bps``, ``device_read_iops``, and ``device_write_iops``
    (cgroup v1 ``blkio`` or cgroup v2 ``io.max``). The accompanying
    device-node path is resolved separately and held on the environment.
    """

    ram_bytes: int  # Available RAM for this worker (already divided if bare-metal)
    cpu_cores: int  # Available CPU cores for this worker
    disk_type: str  # "SSD", "HDD", or "unknown"
    disk_read_bps: int = 0
    disk_write_bps: int = 0
    disk_read_iops: int = 0
    disk_write_iops: int = 0
    disk_class: str = "unknown"  # Refined classification used for heuristics


# ---------------------------------------------------------------------------
# Disk bandwidth detection
# ---------------------------------------------------------------------------

# Floor budgets to avoid pathological zero-budget configs when many workers
# share a slow disk. 1 MB/s and 100 IOPS still let the database make forward
# progress while remaining clearly throttled.
_DISK_MIN_BPS_PER_WORKER = 1 * 1024 * 1024
_DISK_MIN_IOPS_PER_WORKER = 100

# Trust threshold for the fio probe. When the probed sustained throughput
# falls below this fraction of the disk-class heuristic, we treat the
# probe as unreliable (cold cache, burst-credit starvation on cloud PDs,
# concurrent I/O during startup) and fall back to the heuristic instead.
# 0.25 = "if the probe says you're getting <25% of what your disk class
# is normally capable of, the probe is the suspect, not the disk." Tuned
# against GCP pd-ssd noise where the first 5-10s of probing can return
# <5% of true sustained capacity on a freshly attached volume.
_DISK_PROBE_TRUST_FLOOR = 0.25

# Heuristic sustained-throughput ceilings per disk class.
# Values reflect realistic sustained (not peak-burst) numbers for the
# 4k-random write workloads PostgreSQL produces. Read budgets are larger
# than write because SSD endurance + WAL fsync dominate the write path.
_DISK_CLASS_BUDGETS: Dict[str, Dict[str, int]] = {
    "hdd": {
        "read_bps": 150 * 1024 * 1024,
        "write_bps": 100 * 1024 * 1024,
        "read_iops": 200,
        "write_iops": 200,
    },
    "sata_ssd": {
        "read_bps": 500 * 1024 * 1024,
        "write_bps": 250 * 1024 * 1024,
        "read_iops": 80_000,
        "write_iops": 60_000,
    },
    "nvme_pcie3": {
        "read_bps": int(2.5 * 1024 * 1024 * 1024),
        "write_bps": int(1.5 * 1024 * 1024 * 1024),
        "read_iops": 400_000,
        "write_iops": 300_000,
    },
    "nvme_pcie4": {
        "read_bps": 6 * 1024 * 1024 * 1024,
        "write_bps": 4 * 1024 * 1024 * 1024,
        "read_iops": 700_000,
        "write_iops": 600_000,
    },
    "nvme_pcie5": {
        "read_bps": 12 * 1024 * 1024 * 1024,
        "write_bps": 10 * 1024 * 1024 * 1024,
        "read_iops": 1_500_000,
        "write_iops": 1_200_000,
    },
    # USB / external is overlaid as a min() cap on top of the underlying
    # class default — the bus is the bottleneck, not the media.
    "usb_external": {
        "read_bps": 400 * 1024 * 1024,
        "write_bps": 200 * 1024 * 1024,
        "read_iops": 50_000,
        "write_iops": 40_000,
    },
    "unknown": {
        "read_bps": 500 * 1024 * 1024,
        "write_bps": 250 * 1024 * 1024,
        "read_iops": 50_000,
        "write_iops": 30_000,
    },
}


def _normalize_dev_basename(device_path: str) -> str:
    """Resolve symlinks and return the bare kernel device name (e.g. nvme0n1)."""
    return os.path.basename(os.path.realpath(device_path))


def _resolve_parent_block_device(device_path: str) -> str:
    """Walk a partition device node up to its parent disk node.

    cgroup v2 ``io.max`` (and cgroup v1 ``blkio.throttle.*``) are
    enforced on the parent block device, not on individual partitions.
    Writing ``"8:3 rbps=..."`` to ``io.max`` for partition ``/dev/sda3``
    raises ``ENODEV`` ("no such device") because the kernel's I/O
    scheduler lives on the parent disk (``/dev/sda``, ``8:0``).

    For partitions (``/sys/class/block/<name>/partition`` exists), this
    walks ``/sys/class/block/<name>/..`` to the parent disk basename.
    For non-partition block devices (whole disks, dm-X, md-X), returns
    the input unchanged.
    """
    dev_basename = os.path.basename(device_path)
    sys_block_link = Path(f"/sys/class/block/{dev_basename}")
    if not sys_block_link.exists():
        return device_path

    partition_marker = sys_block_link / "partition"
    if not partition_marker.exists():
        # Whole disk, dm-X, md-X, etc. — no parent walk needed.
        return device_path

    try:
        # /sys/class/block/sda3 -> /sys/devices/.../sda/sda3
        # parent dir basename gives us "sda".
        resolved = sys_block_link.resolve(strict=True)
        parent_basename = resolved.parent.name
    except OSError:
        return device_path

    if not parent_basename:
        return device_path

    parent_node = f"/dev/{parent_basename}"
    if Path(parent_node).exists():
        return parent_node
    return device_path


def _resolve_block_device_node(target_path: Path) -> Optional[str]:
    """Return the host block-device node path (``/dev/sdX`` or ``/dev/nvmeXnY``)
    whose filesystem contains ``target_path``.

    Needed because Docker's ``device_*_bps`` kwargs require a device-node
    path (the daemon resolves it to ``(major, minor)`` for the cgroup
    write). Always returns the **parent disk** rather than a partition,
    so the cgroup write targets the device the I/O scheduler is bound to.
    Returns ``None`` when the path can't be resolved (non-Linux,
    bind-mount from a container, etc.).
    """
    if platform.system() != "Linux":
        return None

    mount_point, device = _find_mount_point(target_path)
    if not device:
        return None
    if not device.startswith("/dev/"):
        return None

    # Resolve symlinks (e.g. /dev/mapper/cryptroot -> /dev/dm-0) so the
    # daemon sees a stable device node.
    try:
        resolved = os.path.realpath(device)
        if not resolved.startswith("/dev/"):
            resolved = device
    except OSError:
        resolved = device

    return _resolve_parent_block_device(resolved)


def _is_usb_attached(dev_basename: str) -> bool:
    """Return True if the block device is attached over USB."""
    sys_block = Path(f"/sys/block/{dev_basename}")
    try:
        resolved = sys_block.resolve(strict=False)
    except OSError:
        return False
    return "/usb" in str(resolved).lower()


def _detect_nvme_pcie_class(dev_basename: str) -> str:
    """Resolve NVMe PCIe generation from sysfs.

    Reads ``/sys/class/nvme/<ctrl>/device/current_link_speed`` which the
    kernel populates as e.g. ``8.0 GT/s PCIe`` (PCIe 3), ``16.0 GT/s
    PCIe`` (PCIe 4), ``32.0 GT/s PCIe`` (PCIe 5). Falls back to
    ``nvme_pcie3`` when the speed string is missing or unrecognised
    (conservative, matches typical consumer drives).
    """
    # nvme0n1 -> nvme0
    match = re.match(r"^(nvme\d+)", dev_basename)
    if not match:
        return "nvme_pcie3"
    controller = match.group(1)
    speed_path = Path(f"/sys/class/nvme/{controller}/device/current_link_speed")
    try:
        speed = speed_path.read_text(encoding="utf-8").strip()
    except OSError:
        return "nvme_pcie3"
    speed_lower = speed.lower()
    if "32" in speed_lower or "32.0 gt/s" in speed_lower:
        return "nvme_pcie5"
    if "16" in speed_lower or "16.0 gt/s" in speed_lower:
        return "nvme_pcie4"
    if "8" in speed_lower or "8.0 gt/s" in speed_lower:
        return "nvme_pcie3"
    return "nvme_pcie3"


def _detect_disk_class(device_path: Optional[str]) -> str:
    """Classify the backing block device into one of the budget buckets.

    Returns one of ``hdd | sata_ssd | nvme_pcie3 | nvme_pcie4 |
    nvme_pcie5 | usb_external | unknown``. ``usb_external`` is special:
    it indicates the bus is the bottleneck and the caller should overlay
    the USB cap on top of the underlying media's budget.
    """
    if platform.system() != "Linux" or not device_path:
        return "unknown"

    dev_basename = _normalize_dev_basename(device_path)

    # Strip partition suffix to find the parent block device.
    if dev_basename.startswith("nvme"):
        base = dev_basename.split("p")[0] if "p" in dev_basename[4:] else dev_basename
    else:
        base = dev_basename.rstrip("0123456789")

    rotational_path = Path(f"/sys/block/{base}/queue/rotational")
    rotational = "unknown"
    try:
        rotational = rotational_path.read_text(encoding="utf-8").strip()
    except OSError:
        pass

    if _is_usb_attached(base):
        return "usb_external"

    if base.startswith("nvme"):
        return _detect_nvme_pcie_class(base)

    if rotational == "1":
        return "hdd"
    if rotational == "0":
        return "sata_ssd"
    return "unknown"


def _heuristic_disk_budget(disk_class: str) -> Dict[str, int]:
    """Return host-level disk bandwidth/IOPS budget for a disk class."""
    if disk_class == "usb_external":
        usb = _DISK_CLASS_BUDGETS["usb_external"]
        # USB-attached drives almost always have HDD/SATA-SSD media — cap
        # the bus, but never claim more than the media would deliver.
        media = _DISK_CLASS_BUDGETS["sata_ssd"]
        return {
            key: min(usb[key], media[key])
            for key in ("read_bps", "write_bps", "read_iops", "write_iops")
        }
    return dict(_DISK_CLASS_BUDGETS.get(disk_class, _DISK_CLASS_BUDGETS["unknown"]))


def _probe_disk_with_fio(
    data_path: Path, timeout_s: int = 20
) -> Optional[Dict[str, int]]:
    """Measure host disk bandwidth and IOPS with an ``fio`` probe.

    Runs a sequential write at 1 MiB blocks for sustained-bps and a
    4k random read for IOPS. The probe file is created under
    ``data_path`` so the measurement reflects the filesystem the
    workers actually write to. Returns ``None`` when fio is missing or
    any probe fails — callers fall back to the heuristic.

    Probe runtimes (8s write, 5s read) are tuned to outlast the
    burst-credit + cold-cache window on cloud-attached block storage
    (GCP Persistent Disk, AWS EBS gp3). Shorter runs can land entirely
    inside a credit-starved moment and report a small fraction of true
    sustained capacity. The host_budget validator in
    ``_resolve_host_disk_budget`` provides a second layer of defence
    against pathological probe results.
    """
    fio_bin = shutil.which("fio")
    if not fio_bin:
        return None
    if not data_path.exists():
        try:
            data_path.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None

    with tempfile.NamedTemporaryFile(
        prefix=".pbt_fio_probe_", dir=str(data_path), delete=False
    ) as fh:
        probe_file = Path(fh.name)

    common_args = [
        fio_bin,
        f"--filename={probe_file}",
        "--size=64M",
        "--time_based",
        "--output-format=json",
        "--group_reporting",
    ]

    def _run(args: list[str]) -> Optional[Dict[str, Any]]:
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
            if result.returncode != 0:
                return None
            return json.loads(result.stdout)
        except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
            return None

    try:
        write_payload = _run(
            common_args
            + [
                "--name=write_probe",
                "--rw=write",
                "--bs=1M",
                "--runtime=8",
                "--ioengine=psync",
                "--direct=0",
                "--fsync_on_close=1",
            ]
        )
        read_payload = _run(
            common_args
            + [
                "--name=read_probe",
                "--rw=randread",
                "--bs=4k",
                "--runtime=5",
                "--ioengine=psync",
                "--direct=0",
            ]
        )
    finally:
        try:
            probe_file.unlink()
        except OSError:
            pass

    if not write_payload or not read_payload:
        return None

    try:
        write_job = write_payload["jobs"][0]["write"]
        read_job = read_payload["jobs"][0]["read"]
        # fio reports bandwidth in KiB/s (`bw`) and IOPS as a float.
        return {
            "read_bps": int(read_job.get("bw", 0)) * 1024,
            "write_bps": int(write_job.get("bw", 0)) * 1024,
            "read_iops": int(read_job.get("iops", 0)),
            "write_iops": int(write_job.get("iops", 0)),
        }
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _probe_passes_trust_floor(
    probed: Dict[str, int],
    heuristic: Dict[str, int],
    *,
    floor: float = _DISK_PROBE_TRUST_FLOOR,
) -> tuple[bool, list[str]]:
    """Decide whether an fio probe is plausible relative to the class
    heuristic.

    Cloud-attached block storage (GCP PD, AWS EBS) can return wildly
    pessimistic short-burst readings during the first few seconds after
    attach — credit-starvation, cold cache, concurrent container I/O.
    A probe that says "your SATA-SSD-class disk delivers 2 MB/s" is far
    more likely to be a broken measurement than a real ceiling.

    Returns (passes, reasons). When ``passes`` is False, ``reasons`` is
    the list of fields that fell below the trust floor — fed to the
    caller's log message so the operator can diagnose why their probe
    was rejected.
    """
    fields = ("read_bps", "write_bps", "read_iops", "write_iops")
    failed: list[str] = []
    for key in fields:
        threshold = heuristic[key] * floor
        if probed[key] < threshold:
            failed.append(key)
    return (not failed), failed


def _resolve_host_disk_budget(
    data_path: Optional[Path],
    *,
    probe_disk: bool,
) -> tuple[Dict[str, int], str]:
    """Resolve host-level disk budget plus the disk class label used.

    Probe results that fall below ``_DISK_PROBE_TRUST_FLOOR`` of the
    class heuristic on any metric are rejected and the heuristic is
    used instead — fio's 5-10s window can hit a credit-starved or
    cold-cache moment on a cloud PD and return a tiny fraction of
    sustained capacity, which would then bottleneck every worker for
    the rest of the session.
    """
    device = None
    if data_path is not None:
        device = _resolve_block_device_node(data_path)
    disk_class = _detect_disk_class(device)
    heuristic = _heuristic_disk_budget(disk_class)

    if probe_disk and shutil.which("fio") is None:
        LOGGER.warning(
            "  ➤ --probe-disk requested but the `fio` binary was not found on "
            "PATH. Falling back to the %s-class heuristic for per-worker disk "
            "budgeting instead of a measured probe. Install fio (e.g. "
            "`apt install fio`, `dnf install fio`, `pacman -S fio`) and re-run "
            "to enable the measured probe.",
            disk_class,
        )

    if probe_disk and data_path is not None:
        probed = _probe_disk_with_fio(data_path)
        if probed is not None:
            passes, failed_fields = _probe_passes_trust_floor(probed, heuristic)
            if passes:
                LOGGER.debug(
                    "  ➤ Disk probe via fio: read=%.1f MB/s, write=%.1f MB/s, "
                    "read_iops=%d, write_iops=%d",
                    probed["read_bps"] / (1024 * 1024),
                    probed["write_bps"] / (1024 * 1024),
                    probed["read_iops"],
                    probed["write_iops"],
                )
                return probed, disk_class
            LOGGER.warning(
                "  ➤ Disk probe via fio returned implausibly low values "
                "(below %d%% of %s-class heuristic on %s): "
                "read=%.1f MB/s, write=%.1f MB/s, read_iops=%d, write_iops=%d. "
                "Falling back to heuristic. Cloud-attached PDs (GCP, AWS) "
                "can return short-burst credit-starved readings during the "
                "first few seconds after attach — re-run after the host has "
                "been idle for ~60s if the heuristic is itself wrong for "
                "this disk.",
                int(_DISK_PROBE_TRUST_FLOOR * 100),
                disk_class,
                ", ".join(failed_fields),
                probed["read_bps"] / (1024 * 1024),
                probed["write_bps"] / (1024 * 1024),
                probed["read_iops"],
                probed["write_iops"],
            )

    LOGGER.debug(
        "  ➤ Disk budget via heuristic (class=%s): read=%.1f MB/s, write=%.1f MB/s",
        disk_class,
        heuristic["read_bps"] / (1024 * 1024),
        heuristic["write_bps"] / (1024 * 1024),
    )
    return heuristic, disk_class


def _partition_disk_budget(
    host_budget: Dict[str, int],
    *,
    num_workers: int,
    threshold: float,
) -> Dict[str, int]:
    """Divide host-level disk budget by ``num_workers`` with headroom."""
    num_workers = max(1, num_workers)
    bps_floor = _DISK_MIN_BPS_PER_WORKER
    iops_floor = _DISK_MIN_IOPS_PER_WORKER
    return {
        "read_bps": max(bps_floor, int(host_budget["read_bps"] * threshold / num_workers)),
        "write_bps": max(bps_floor, int(host_budget["write_bps"] * threshold / num_workers)),
        "read_iops": max(iops_floor, int(host_budget["read_iops"] * threshold / num_workers)),
        "write_iops": max(iops_floor, int(host_budget["write_iops"] * threshold / num_workers)),
    }


def detect_cpu_model() -> str:
    """Detect CPU model name, either on supportsLinux, macOS, or Windows."""
    system = platform.system()

    if system == "Linux":
        try:
            cpuinfo_path = Path("/proc/cpuinfo")
            if cpuinfo_path.exists():
                text = cpuinfo_path.read_text(encoding="utf-8")
                for line in text.splitlines():
                    if line.startswith("model name"):
                        model = line.split(":", 1)[1].strip()
                        LOGGER.debug(" ➤ Detected CPU model: %s", model)
                        return model
        except OSError:
            pass

    if system == "Darwin":
        sysctl = shutil.which("sysctl")
        if sysctl:
            try:
                result = subprocess.run(
                    [sysctl, "-n", "machdep.cpu.brand_string"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if result.returncode == 0 and result.stdout.strip():
                    model = result.stdout.strip()
                    LOGGER.debug(" ➤ Detected CPU model: %s", model)
                    return model
            except (subprocess.TimeoutExpired, OSError):
                pass

    if system == "Windows":
        try:
            import winreg

            key = winreg.OpenKey(  # type: ignore[attr-defined]
                winreg.HKEY_LOCAL_MACHINE,  # type: ignore[attr-defined]
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            )
            value, _ = winreg.QueryValueEx(key, "ProcessorNameString")  # type: ignore[attr-defined]
            winreg.CloseKey(key)  # type: ignore[attr-defined]
            if value:
                model = value.strip()
                LOGGER.debug(" ➤ Detected CPU model: %s", model)
                return model
        except (OSError, ImportError):
            pass

    # Generic fallback
    processor = platform.processor()
    if processor:
        LOGGER.debug(" ➤ Detected CPU model via generic fallback: %s", processor)
        return processor

    LOGGER.warning(" ➤ Could not detect CPU model.")
    return "unknown"


def detect_core_count() -> Dict[str, int]:
    """Detect physical and logical CPU core counts."""
    physical = psutil.cpu_count(logical=False)
    logical = psutil.cpu_count(logical=True)
    LOGGER.debug(" ➤ Detected CPU cores: %s physical, %s logical", physical, logical)
    return {
        "physical": physical or 0,
        "logical": logical or 0,
    }


def detect_ram_total() -> Dict[str, Any]:
    """Detect total system RAM in bytes and human-readable format."""
    mem = psutil.virtual_memory()
    total_bytes = mem.total
    total_gb = round(total_bytes / (1024**3), 2)
    LOGGER.debug(" ➤ Detected total RAM: %s GB", total_gb)
    return {
        "total_bytes": total_bytes,
        "total_gb": total_gb,
    }


def detect_disk_type() -> str:
    """
    Detect whether the primary disk is SSD or HDD.

    Uses OS-specific methods:
    - Linux: ``/sys/block/<dev>/queue/rotational``
    - macOS: ``diskutil info``
    - Windows: PowerShell ``Get-PhysicalDisk``

    Returns 'SSD', 'HDD', or 'unknown'.
    """
    return detect_disk_type_for_path(Path("/"))


def _find_mount_point(target_path: Path) -> tuple[str, str]:
    """Find the mount point and device name for a given path."""
    target_str = str(target_path.resolve())
    best_match = ""
    best_device = ""

    try:
        # Sort by length descending to match deepest mount point first
        partitions = sorted(
            psutil.disk_partitions(all=True),
            key=lambda p: len(p.mountpoint),
            reverse=True,
        )

        for part in partitions:
            # Check if target path starts with the mount point
            # Also handle the root mount '/' correctly
            mount_str = part.mountpoint
            if not mount_str.endswith(os.sep):
                mount_str += os.sep

            check_path = target_str
            if not check_path.endswith(os.sep):
                check_path += os.sep

            if check_path.startswith(mount_str):
                best_match = part.mountpoint
                best_device = part.device
                break

        # Fallback to root if nothing found
        if not best_match:
            for part in partitions:
                if part.mountpoint == "/" or part.mountpoint == "C:\\":
                    best_match = part.mountpoint
                    best_device = part.device
                    break

    except (OSError, PermissionError, AttributeError) as e:
        LOGGER.debug(
            "%s Error finding mount point for %s: %s%s",
            COLORS.italic,
            target_path,
            e,
            COLORS.reset,
        )

    return best_match, best_device


def detect_disk_type_for_path(target_path: Path) -> str:
    """Detect disk type (SSD/HDD/unknown) for the filesystem hosting target_path.

    Cross-platform: Linux, macOS, Windows.
    This is critical for external drives: the root filesystem may be SSD
    while the data directory lives on an external HDD.
    """
    system = platform.system()
    mount_point, device = _find_mount_point(target_path)

    if not mount_point:
        # Fallback if mount resolution fails
        mount_point = "/"

    if system == "Linux":
        return _detect_disk_type_linux(device)
    if system == "Darwin":
        return _detect_disk_type_macos(mount_point)
    if system == "Windows":
        return _detect_disk_type_windows()

    LOGGER.warning(" ➤ Unknown operating system for disk detection: %s", system)
    return "unknown"


def _is_containerized() -> bool:
    """Detect if running inside a Docker/container environment."""
    if os.path.exists("/.dockerenv"):
        LOGGER.debug("  ➤ Detected container environment via /.dockerenv")
        return True
    try:
        with open("/proc/1/cgroup", "rt", encoding="utf-8") as f:
            content = f.read()
            if "docker" in content or "containerd" in content:
                LOGGER.debug("  ➤ Detected container environment via /proc/1/cgroup")
                return True
    except OSError:
        pass
    LOGGER.debug("  ➤ No container environment detected")
    return False


def detect_worker_resources(
    max_parallel_workers: int = 1,
    data_path: Optional[Path] = None,
    threshold: float = 0.8,
    probe_disk: bool = True,
    disk_overrides: Optional[Dict[str, Optional[int]]] = None,
) -> WorkerResources:
    """
    Detect per-worker hardware resources.

    If in a container, uses cgroups via psutil limits.
    If bare-metal, divides system resources by max_parallel_workers.
    Reserves (1 - threshold) of resources for OS/tuning system.
    If data_path is provided, detects disk type for that specific path.

    Disk bandwidth/IOPS budgets are resolved by (1) the optional
    fio probe when available and ``probe_disk`` is True, falling back
    to (2) a refined heuristic per disk class. Manual per-field
    overrides in ``disk_overrides`` (``{"read_bps": ..., "write_bps":
    ..., "read_iops": ..., "write_iops": ...}``) win over both.
    """
    if data_path is not None:
        disk_type = detect_disk_type_for_path(data_path)
    else:
        disk_type = detect_disk_type()

    mem = psutil.virtual_memory()
    ram_bytes = mem.total

    try:
        # Rely on process affinity when isolated via taskset / cpuset-cpus
        cpu_cores = len(psutil.Process().cpu_affinity())
    except (AttributeError, NotImplementedError):
        cpu_cores = psutil.cpu_count(logical=True) or 1

    # Total RAM and CPU seen are constrained to leave headroom for OS Essentials
    usable_ram = int(ram_bytes * threshold)
    usable_cpu = cpu_cores * threshold

    worker_ram = max(
        100 * 1024 * 1024, int(usable_ram / max_parallel_workers)
    )  # min 100MB
    worker_cpu = max(
        1, math.floor(usable_cpu / max_parallel_workers)
    )  # min 1 logical core

    host_disk_budget, disk_class = _resolve_host_disk_budget(
        data_path,
        probe_disk=probe_disk,
    )
    per_worker_disk = _partition_disk_budget(
        host_disk_budget,
        num_workers=max_parallel_workers,
        threshold=threshold,
    )
    if disk_overrides:
        for key in ("read_bps", "write_bps", "read_iops", "write_iops"):
            override = disk_overrides.get(key)
            if override is not None:
                per_worker_disk[key] = int(override)

    LOGGER.debug(
        "➤ Worker resources allocated: RAM=%s bytes, CPU=%s cores, Disk=%s, "
        "read=%.1f MB/s, write=%.1f MB/s, read_iops=%d, write_iops=%d",
        worker_ram,
        worker_cpu,
        disk_type,
        per_worker_disk["read_bps"] / (1024 * 1024),
        per_worker_disk["write_bps"] / (1024 * 1024),
        per_worker_disk["read_iops"],
        per_worker_disk["write_iops"],
    )

    return WorkerResources(
        ram_bytes=worker_ram,
        cpu_cores=worker_cpu,
        disk_type=disk_type,
        disk_read_bps=per_worker_disk["read_bps"],
        disk_write_bps=per_worker_disk["write_bps"],
        disk_read_iops=per_worker_disk["read_iops"],
        disk_write_iops=per_worker_disk["write_iops"],
        disk_class=disk_class,
    )


def parse_ram_value(value: str) -> int:
    """Parse a human-readable RAM string into bytes."""
    value = str(value).strip().upper()
    match = re.match(r"^(\d+)\s*([KMGTPE]?)[B]?$", value)
    if not match:
        raise ValueError(f"Invalid RAM value format: {value}")

    number = int(match.group(1))
    suffix = match.group(2)

    multipliers = {
        "": 1,
        "K": 1024,
        "M": 1024**2,
        "G": 1024**3,
        "T": 1024**4,
        "P": 1024**5,
        "E": 1024**6,
    }
    return number * multipliers[suffix]


def resolve_manual_worker_resources(
    worker_ram: Optional[str] = None,
    worker_cpus: Optional[int] = None,
    num_workers: int = 1,
    data_path: Optional[Path] = None,
    worker_disk_read_bps: Optional[int] = None,
    worker_disk_write_bps: Optional[int] = None,
    worker_disk_read_iops: Optional[int] = None,
    worker_disk_write_iops: Optional[int] = None,
    probe_disk: bool = True,
) -> WorkerResources:
    """
    Resolve manual worker resources and validate against host limits.

    Allows up to 95% of host capacity. Warns if > 80% is used.
    Falls back entirely to auto-detection if > 95% is requested.

    Disk bandwidth / IOPS may be partially overridden -- fields left as
    ``None`` fall back to auto-detection (fio probe, then heuristic).
    Manual values that would push the total beyond 95% of the detected
    host ceiling are dropped in favour of auto-detected values, mirroring
    the RAM/CPU overflow behaviour.
    """
    if data_path is not None:
        disk_type = detect_disk_type_for_path(data_path)
    else:
        disk_type = detect_disk_type()

    mem = psutil.virtual_memory()
    total_ram = mem.total

    try:
        total_cpus = len(psutil.Process().cpu_affinity())
    except (AttributeError, NotImplementedError):
        total_cpus = psutil.cpu_count(logical=True) or 1

    # Fallback auto-detection values
    usable_ram = int(total_ram * 0.8)
    usable_cpu = total_cpus * 0.8
    auto_ram = max(100 * 1024 * 1024, int(usable_ram / num_workers))
    auto_cpu = max(1, math.floor(usable_cpu / num_workers))

    host_disk_budget, disk_class = _resolve_host_disk_budget(
        data_path,
        probe_disk=probe_disk,
    )
    auto_disk = _partition_disk_budget(
        host_disk_budget,
        num_workers=num_workers,
        threshold=0.8,
    )

    resolved_ram = auto_ram
    if worker_ram is not None:
        resolved_ram = parse_ram_value(worker_ram)

    resolved_cpu = auto_cpu
    if worker_cpus is not None:
        resolved_cpu = int(worker_cpus)

    manual_disk: Dict[str, Optional[int]] = {
        "read_bps": worker_disk_read_bps,
        "write_bps": worker_disk_write_bps,
        "read_iops": worker_disk_read_iops,
        "write_iops": worker_disk_write_iops,
    }
    resolved_disk = dict(auto_disk)
    for key in ("read_bps", "write_bps", "read_iops", "write_iops"):
        override = manual_disk[key]
        if override is not None:
            resolved_disk[key] = int(override)

    total_req_ram = resolved_ram * num_workers
    total_req_cpu = resolved_cpu * num_workers

    # Validation logic (95% cap)
    ram_exceeded = total_req_ram > (total_ram * 0.95)
    cpu_exceeded = total_req_cpu > (total_cpus * 0.95)
    disk_exceeded = any(
        manual_disk[key] is not None
        and resolved_disk[key] * num_workers > host_disk_budget[key] * 0.95
        for key in ("read_bps", "write_bps", "read_iops", "write_iops")
    )

    if ram_exceeded or cpu_exceeded or disk_exceeded:
        reasons = []
        if ram_exceeded:
            reasons.append("RAM")
        if cpu_exceeded:
            reasons.append("CPU")
        if disk_exceeded:
            reasons.append("disk I/O")
        LOGGER.warning(
            "Manual resource allocation exceeds 95%% of host capacity (%s); "
            "falling back to auto-detected resources.",
            ", ".join(reasons),
        )
        LOGGER.debug(
            "Worker resources allocated (fallback): RAM=%s bytes, CPU=%s cores, "
            "Disk=%s, read=%.1f MB/s, write=%.1f MB/s",
            auto_ram,
            auto_cpu,
            disk_type,
            auto_disk["read_bps"] / (1024 * 1024),
            auto_disk["write_bps"] / (1024 * 1024),
        )
        return WorkerResources(
            ram_bytes=auto_ram,
            cpu_cores=auto_cpu,
            disk_type=disk_type,
            disk_read_bps=auto_disk["read_bps"],
            disk_write_bps=auto_disk["write_bps"],
            disk_read_iops=auto_disk["read_iops"],
            disk_write_iops=auto_disk["write_iops"],
            disk_class=disk_class,
        )

    # Warning logic (> 80% and <= 95%)
    ram_warn = total_req_ram > (total_ram * 0.80)
    cpu_warn = total_req_cpu > (total_cpus * 0.80)

    if ram_warn or cpu_warn:
        LOGGER.warning(
            "Manual resource allocation uses more than 80%% of host capacity. "
            "This may bottleneck the host machine and tuning processes. "
            "Proceeding with requested allocation."
        )

    LOGGER.info(
        "Using manual worker resources: RAM=%s bytes, CPU=%s cores, Disk=%s, "
        "read=%.1f MB/s, write=%.1f MB/s, read_iops=%d, write_iops=%d",
        resolved_ram,
        resolved_cpu,
        disk_type,
        resolved_disk["read_bps"] / (1024 * 1024),
        resolved_disk["write_bps"] / (1024 * 1024),
        resolved_disk["read_iops"],
        resolved_disk["write_iops"],
    )
    return WorkerResources(
        ram_bytes=resolved_ram,
        cpu_cores=resolved_cpu,
        disk_type=disk_type,
        disk_read_bps=resolved_disk["read_bps"],
        disk_write_bps=resolved_disk["write_bps"],
        disk_read_iops=resolved_disk["read_iops"],
        disk_write_iops=resolved_disk["write_iops"],
        disk_class=disk_class,
    )


def _detect_disk_type_linux(device_path: Optional[str] = None) -> str:
    """Linux disk type detection via /sys/block rotational flag."""
    try:
        if not device_path:
            partitions = psutil.disk_partitions()
            for part in partitions:
                if part.mountpoint == "/":
                    device_path = part.device
                    break

        if not device_path:
            return "unknown"

        dev_name = os.path.basename(os.path.realpath(device_path))

        # Loop devices: resolve the backing file's physical device
        if dev_name.startswith("loop"):
            backing_file_path = Path(f"/sys/block/{dev_name}/loop/backing_file")
            if backing_file_path.exists():
                backing_file = backing_file_path.read_text(encoding="utf-8").strip()
                # Find which physical device hosts the backing file
                _, backing_device = _find_mount_point(Path(backing_file))
                if backing_device:
                    dev_name = os.path.basename(os.path.realpath(backing_device))
                else:
                    # Fallback: loop device itself reports rotational correctly
                    rotational_path = Path(f"/sys/block/{dev_name}/queue/rotational")
                    if rotational_path.exists():
                        val = rotational_path.read_text(encoding="utf-8").strip()
                        return "HDD" if val == "1" else "SSD"
                    return "unknown"

        if dev_name.startswith("nvme"):
            base = dev_name.split("p")[0] if "p" in dev_name[4:] else dev_name
        else:
            base = dev_name.rstrip("0123456789")

        rotational_path = Path(f"/sys/block/{base}/queue/rotational")
        if rotational_path.exists():
            val = rotational_path.read_text(encoding="utf-8").strip()
            disk_type = "HDD" if val == "1" else "SSD"
            return disk_type
    except (OSError, IndexError, ValueError) as e:
        LOGGER.debug(
            "%s  Error detecting Linux disk type: %s%s", COLORS.italic, e, COLORS.reset
        )

    LOGGER.debug(
        "%s  Could not detect Linux disk type, defaulting to unknown%s",
        COLORS.warning,
        COLORS.reset,
    )
    return "unknown"


def _detect_disk_type_macos(mount_point: str = "/") -> str:
    """macOS disk type detection via diskutil."""
    diskutil = shutil.which("diskutil")
    if not diskutil:
        return "unknown"
    try:
        result = subprocess.run(
            [diskutil, "info", mount_point],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                stripped = line.strip().lower()
                if "solid state" in stripped:
                    disk_type = "SSD" if "yes" in stripped else "HDD"
                    return disk_type
    except (subprocess.TimeoutExpired, OSError) as e:
        LOGGER.debug(
            "%s Error detecting macOS disk type: %s%s", COLORS.italic, e, COLORS.reset
        )

    LOGGER.debug(
        "%s Could not detect macOS disk type, defaulting to unknown%s",
        COLORS.warning,
        COLORS.reset,
    )
    return "unknown"


def _detect_disk_type_windows() -> str:
    """Windows disk type detection via PowerShell Get-PhysicalDisk."""
    powershell = shutil.which("powershell")
    if not powershell:
        return "unknown"
    try:
        # Simple detection first
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-Command",
                "Get-PhysicalDisk | Select-Object -First 1 -ExpandProperty MediaType",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0:
            media = result.stdout.strip()
            if media == "SSD":
                return "SSD"
            if media in ("HDD", "Unspecified"):
                disk_type = "HDD" if media == "HDD" else "unknown"
                return disk_type
    except (subprocess.TimeoutExpired, OSError) as e:
        LOGGER.debug(
            "%s Error detecting Windows disk type: %s%s", COLORS.italic, e, COLORS.reset
        )

    LOGGER.debug(
        "%s Could not detect Windows disk type, defaulting to unknown%s",
        COLORS.warning,
        COLORS.reset,
    )
    return "unknown"


def detect_pg_version() -> str:
    """
    Detect PostgreSQL server version by running ``pg_config --version``.

    Falls back to ``psql --version`` if pg_config is unavailable.
    LOGGER.info("  Detecting PostgreSQL version...")
    """
    for cmd in (["pg_config", "--version"], ["psql", "--version"]):
        binary = shutil.which(cmd[0])
        if binary is None:
            continue
        try:
            result = subprocess.run(
                [binary] + cmd[1:],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                version = result.stdout.strip()
                LOGGER.debug(" ➤ Detected PostgreSQL version: %s", version)
                return version
        except (subprocess.TimeoutExpired, OSError) as e:
            LOGGER.debug(
                "%s  Error checking postgres version via %s: %s%s",
                COLORS.italic,
                cmd[0],
                e,
                COLORS.reset,
            )
            continue

    LOGGER.debug(" ➤ Could not detect PostgreSQL version, defaulting to unknown")
    return "unknown"


def detect_os_info() -> Dict[str, str]:
    """Detect operating system details."""
    os_info = {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
    }
    LOGGER.debug(" ➤ Detected OS info: %s %s", os_info["system"], os_info["release"])
    return os_info


def get_system_info(data_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Collect comprehensive system hardware and software information.

    Returns a dictionary suitable for embedding in results JSON under
    the ``"system_info"`` key. All sub-detectors handle errors gracefully.
    """
    info: Dict[str, Any] = {}

    try:
        info["cpu_model"] = detect_cpu_model()
    except (OSError, ValueError, TypeError) as e:
        LOGGER.warning("Failed to detect CPU model: %s", e)
        info["cpu_model"] = "detection_failed"

    try:
        info["cpu_cores"] = detect_core_count()
    except (OSError, ValueError, TypeError) as e:
        LOGGER.warning("Failed to detect CPU cores: %s", e)
        info["cpu_cores"] = {"physical": 0, "logical": 0}

    try:
        info["ram"] = detect_ram_total()
    except (OSError, ValueError, TypeError) as e:
        LOGGER.warning("Failed to detect RAM: %s", e)
        info["ram"] = {"total_bytes": 0, "total_gb": 0.0}

    try:
        system_disk = detect_disk_type()
        info["disk_type"] = system_disk

        # Add specific data_disk_type if data_path is provided and different
        if data_path is not None:
            data_disk = detect_disk_type_for_path(data_path)
            if data_disk != system_disk:
                info["data_disk_type"] = data_disk
    except (OSError, ValueError, TypeError) as e:
        LOGGER.warning("Failed to detect disk type: %s", e)
        info["disk_type"] = "detection_failed"

    try:
        info["pg_version"] = detect_pg_version()
    except (OSError, ValueError, TypeError) as e:
        LOGGER.warning("Failed to detect PostgreSQL version: %s", e)
        info["pg_version"] = "detection_failed"

    try:
        info["os"] = detect_os_info()
    except (OSError, ValueError, TypeError) as e:
        LOGGER.warning("Failed to detect OS info: %s", e)
        info["os"] = {
            "system": "detection_failed",
            "release": "",
            "version": "",
            "machine": "",
        }

    return info


def log_system_info(
    logger: logging.Logger,
    system_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Log detected hardware information and return the info dict.

    Parameters
    ----------
    logger : logging.Logger
        Logger instance to write to.
    system_info : dict, optional
        Pre-collected system info. If None, calls ``get_system_info()``.

    Returns
    -------
    dict
        The system information dictionary.
    """
    if system_info is None:
        system_info = get_system_info()

    cores = system_info.get("cpu_cores", {})
    ram = system_info.get("ram", {})
    os_info = system_info.get("os", {})

    logger.info("System Information:")
    logger.info(
        " CPU Model:      %s%s%s",
        COLORS.cyan,
        system_info.get("cpu_model", "unknown"),
        COLORS.reset,
    )
    logger.info(
        " CPU Cores:      %s%s physical / %s logical%s",
        COLORS.cyan,
        cores.get("physical", "?"),
        cores.get("logical", "?"),
        COLORS.reset,
    )
    logger.info(
        " RAM:            %s%.2f GB%s",
        COLORS.cyan,
        ram.get("total_gb", 0.0),
        COLORS.reset,
    )
    logger.info(
        " Disk Type:      %s%s%s",
        COLORS.cyan,
        system_info.get("disk_type", "unknown"),
        COLORS.reset,
    )
    if "data_disk_type" in system_info:
        logger.info(
            " Data Disk Type: %s%s%s",
            COLORS.cyan,
            system_info["data_disk_type"],
            COLORS.reset,
        )
    logger.info(
        " PostgreSQL:     %s%s%s",
        COLORS.cyan,
        system_info.get("pg_version", "unknown"),
        COLORS.reset,
    )
    logger.info(
        " OS:             %s%s %s (%s)%s",
        COLORS.cyan,
        os_info.get("system", "unknown"),
        os_info.get("release", ""),
        os_info.get("machine", ""),
        COLORS.reset,
    )

    return system_info
