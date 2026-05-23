"""
Hardware Information Detection
==============================

Detects and reports system hardware characteristics for reproducibility
and provenance tracking in tuning results.

Captures CPU, memory, disk, OS, and PostgreSQL version information.
All detection functions are designed to fail gracefully, returning
"unknown" values rather than raising exceptions.
"""

import logging
import os
import platform
import shutil
import subprocess
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
    """Per-worker hardware resources for hardware-aware knob ranges."""

    ram_bytes: int  # Available RAM for this worker (already divided if bare-metal)
    cpu_cores: int  # Available CPU cores for this worker
    disk_type: str  # "SSD", "HDD", or "unknown"


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
            COLORS.reset
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
) -> WorkerResources:
    """
    Detect per-worker hardware resources.

    If in a container, uses cgroups via psutil limits.
    If bare-metal, divides system resources by max_parallel_workers.
    Reserves 20% of resources for OS/tuning system.
    If data_path is provided, detects disk type for that specific path.
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
    usable_ram = int(ram_bytes * 0.8)
    usable_cpu = cpu_cores * 0.8

    worker_ram = max(
        100 * 1024 * 1024, int(usable_ram / max_parallel_workers)
    )  # min 100MB
    worker_cpu = max(
        1, math.floor(usable_cpu / max_parallel_workers)
    )  # min 1 logical core

    LOGGER.debug(
        "➤ Worker resources allocated: RAM=%s bytes, CPU=%s cores, Disk=%s",
        worker_ram,
        worker_cpu,
        disk_type
    )

    return WorkerResources(
        ram_bytes=worker_ram, cpu_cores=worker_cpu, disk_type=disk_type
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
            "%s  Error detecting Linux disk type: %s%s",
            COLORS.italic,
            e,
            COLORS.reset)

    LOGGER.debug(
        "%s  Could not detect Linux disk type, defaulting to unknown%s",
        COLORS.warning,
        COLORS.reset
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
            "%s Error detecting macOS disk type: %s%s",
            COLORS.italic,
            e,
            COLORS.reset)

    LOGGER.debug(
        "%s Could not detect macOS disk type, defaulting to unknown%s",
        COLORS.warning,
        COLORS.reset
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
            "%s Error detecting Windows disk type: %s%s",
            COLORS.italic,
            e,
            COLORS.reset)

    LOGGER.debug(
        "%s Could not detect Windows disk type, defaulting to unknown%s",
        COLORS.warning,
        COLORS.reset
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
                COLORS.reset
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
        COLORS.reset
    )
    logger.info(
        " CPU Cores:      %s%s physical / %s logical%s",
        COLORS.cyan,
        cores.get("physical", "?"),
        cores.get("logical", "?"),
        COLORS.reset
    )
    logger.info(
        " RAM:            %s%.2f GB%s",
        COLORS.cyan,
        ram.get("total_gb", 0.0),
        COLORS.reset
    )
    logger.info(
        " Disk Type:      %s%s%s",
        COLORS.cyan,
        system_info.get("disk_type", "unknown"),
        COLORS.reset
    )
    if "data_disk_type" in system_info:
        logger.info(
            " Data Disk Type: %s%s%s",
            COLORS.cyan,
            system_info["data_disk_type"],
            COLORS.reset
        )
    logger.info(
        " PostgreSQL:     %s%s%s",
        COLORS.cyan,
        system_info.get("pg_version", "unknown"),
        COLORS.reset
    )
    logger.info(
        " OS:             %s%s %s (%s)%s",
        COLORS.cyan,
        os_info.get("system", "unknown"),
        os_info.get("release", ""),
        os_info.get("machine", ""),
        COLORS.reset
    )

    return system_info
