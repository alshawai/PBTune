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

import psutil


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
                        return line.split(":", 1)[1].strip()
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
                    return result.stdout.strip()
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
                return value.strip()
        except (OSError, ImportError):
            pass

    # Generic fallback
    processor = platform.processor()
    if processor:
        return processor

    return "unknown"


def detect_core_count() -> Dict[str, int]:
    """Detect physical and logical CPU core counts."""
    physical = psutil.cpu_count(logical=False)
    logical = psutil.cpu_count(logical=True)
    return {
        "physical": physical or 0,
        "logical": logical or 0,
    }


def detect_ram_total() -> Dict[str, Any]:
    """Detect total system RAM in bytes and human-readable format."""
    mem = psutil.virtual_memory()
    total_bytes = mem.total
    total_gb = round(total_bytes / (1024 ** 3), 2)
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
    system = platform.system()

    if system == "Linux":
        return _detect_disk_type_linux()
    if system == "Darwin":
        return _detect_disk_type_macos()
    if system == "Windows":
        return _detect_disk_type_windows()

    return "unknown"


def _detect_disk_type_linux() -> str:
    """Linux disk type detection via /sys/block rotational flag."""
    try:
        partitions = psutil.disk_partitions()
        root_device = None
        for part in partitions:
            if part.mountpoint == "/":
                root_device = part.device
                break

        if not root_device:
            return "unknown"

        dev_name = os.path.basename(os.path.realpath(root_device))

        if dev_name.startswith("nvme"):
            base = dev_name.split("p")[0] if "p" in dev_name[4:] else dev_name
        else:
            base = dev_name.rstrip("0123456789")

        rotational_path = Path(f"/sys/block/{base}/queue/rotational")
        if rotational_path.exists():
            val = rotational_path.read_text(encoding="utf-8").strip()
            return "HDD" if val == "1" else "SSD"
    except (OSError, IndexError, ValueError):
        pass

    return "unknown"


def _detect_disk_type_macos() -> str:
    """macOS disk type detection via diskutil."""
    diskutil = shutil.which("diskutil")
    if not diskutil:
        return "unknown"
    try:
        result = subprocess.run(
            [diskutil, "info", "/"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                stripped = line.strip().lower()
                if "solid state" in stripped:
                    return "SSD" if "yes" in stripped else "HDD"
    except (subprocess.TimeoutExpired, OSError):
        pass

    return "unknown"


def _detect_disk_type_windows() -> str:
    """Windows disk type detection via PowerShell Get-PhysicalDisk."""
    powershell = shutil.which("powershell")
    if not powershell:
        return "unknown"
    try:
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
                return "HDD" if media == "HDD" else "unknown"
    except (subprocess.TimeoutExpired, OSError):
        pass

    return "unknown"


def detect_pg_version() -> str:
    """
    Detect PostgreSQL server version by running ``pg_config --version``.

    Falls back to ``psql --version`` if pg_config is unavailable.
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
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, OSError):
            continue

    return "unknown"


def detect_os_info() -> Dict[str, str]:
    """Detect operating system details."""
    return {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
    }


def get_system_info() -> Dict[str, Any]:
    """
    Collect comprehensive system hardware and software information.

    Returns a dictionary suitable for embedding in results JSON under
    the ``"system_info"`` key. All sub-detectors handle errors gracefully.
    """
    info: Dict[str, Any] = {}

    try:
        info["cpu_model"] = detect_cpu_model()
    except (OSError, ValueError, TypeError):
        info["cpu_model"] = "detection_failed"

    try:
        info["cpu_cores"] = detect_core_count()
    except (OSError, ValueError, TypeError):
        info["cpu_cores"] = {"physical": 0, "logical": 0}

    try:
        info["ram"] = detect_ram_total()
    except (OSError, ValueError, TypeError):
        info["ram"] = {"total_bytes": 0, "total_gb": 0.0}

    try:
        info["disk_type"] = detect_disk_type()
    except (OSError, ValueError, TypeError):
        info["disk_type"] = "detection_failed"

    try:
        info["pg_version"] = detect_pg_version()
    except (OSError, ValueError, TypeError):
        info["pg_version"] = "detection_failed"

    try:
        info["os"] = detect_os_info()
    except (OSError, ValueError, TypeError):
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
    logger.info("  CPU Model:      %s", system_info.get("cpu_model", "unknown"))
    logger.info(
        "  CPU Cores:      %s physical / %s logical",
        cores.get("physical", "?"),
        cores.get("logical", "?"),
    )
    logger.info("  RAM:            %.2f GB", ram.get("total_gb", 0.0))
    logger.info("  Disk Type:      %s", system_info.get("disk_type", "unknown"))
    logger.info("  PostgreSQL:     %s", system_info.get("pg_version", "unknown"))
    logger.info(
        "  OS:             %s %s (%s)",
        os_info.get("system", "unknown"),
        os_info.get("release", ""),
        os_info.get("machine", ""),
    )

    return system_info
