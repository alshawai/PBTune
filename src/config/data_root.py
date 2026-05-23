"""
Data Root Configuration
=======================

Provides a single source of truth for resolving the data directory across
all entry points (PBT tuner, BO baseline, evaluation runner, cleanup script).

When the resolved path resides on a non-POSIX filesystem (exFAT, NTFS,
FAT32), the module transparently manages an ext4 loopback image so that
PostgreSQL containers can use bind-mounted data directories with full
POSIX permission support.
"""

import os
from pathlib import Path
from typing import Optional

from src.utils.logger import get_logger, get_color_context


LOGGER = get_logger("DataRoot")
COLORS = get_color_context()
    

def resolve_data_root(cli_override: Optional[str] = None) -> Path:
    """
    Resolve the data root directory for instance storage.

    Priority:
    1. CLI --data-dir override
    2. PBT_DATA_ROOT environment variable
    3. Default ./.instances

    Cross-platform: accepts any valid directory path on Linux, macOS, or Windows.
    Validates: path exists, is a directory, is writable.

    When the target path is on a non-POSIX filesystem (exFAT, NTFS, etc.),
    this function automatically detects and mounts an ext4 loopback image
    (``pbt_data.ext4``) stored near the target path. This provides transparent
    POSIX support for PostgreSQL containers on external drives.

    Logs the resolved path for auditability.

    Parameters
    ----------
    cli_override : Optional[str]
        Optional path string provided via CLI argument

    Returns
    -------
    Path
        Resolved and validated Path object for the data root
    """
    LOGGER.debug(
        "Resolving data root directory with CLI override: %s",
        cli_override,
    )

    default_path = Path("./.instances")

    if cli_override is not None:
        path = Path(cli_override)
        source = "CLI override"
    else:
        env_val = os.getenv("PBT_DATA_ROOT")
        if env_val:
            path = Path(env_val)
            source = "PBT_DATA_ROOT environment variable"
        else:
            path = default_path
            source = "default"

    # Ensure absolute path for consistency if not the default relative path
    if path != default_path:
        path = path.resolve()

    # ── Non-POSIX filesystem handling (exFAT, NTFS, FAT32) ──────────
    # If the target path sits on a filesystem that doesn't support chown/chmod,
    # PostgreSQL will refuse to start. We transparently mount an ext4 loopback
    # image stored near the original path to provide full POSIX semantics.
    if source != "default":
        path = _maybe_use_loopback(path, source)

    # Create directory if it doesn't exist to validate it can be used
    if not path.exists():
        try:
            path.mkdir(parents=True, exist_ok=True)
            if source != "default":
                LOGGER.debug(
                    "  Created new data root directory at %s (from %s)", path, source
                )
        except OSError as e:
            LOGGER.error("Failed to create data root directory `%s`: %s", path, e)
            LOGGER.warning("➤ Falling back to default relative path: `%s`", default_path)
            return default_path.resolve()

    # Validate directory
    if not path.is_dir():
        LOGGER.error("Data root `%s` exists but is not a directory", path)
        LOGGER.warning("➤ Falling back to default relative path: `%s`", default_path)
        return default_path.resolve()

    # Validate writability
    if not os.access(path, os.W_OK):
        LOGGER.error("Data root `%s` is not writable", path)
        LOGGER.warning("➤ Falling back to default relative path: `%s`", default_path)
        return default_path.resolve()

    if source != "default":
        LOGGER.info("%s➤ Using data root: %s (from %s)%s", COLORS.info, path, source, COLORS.reset)

    return path


def _maybe_use_loopback(path: Path, source: str) -> Path:
    """If 'path' is on a non-POSIX filesystem, find and mount a loopback image.

    Returns the mount-point `/.instances` subdirectory on success,
    or the original 'path' if loopback is not needed or fails.
    """
    try:
        from src.config.loopback import (
            is_non_posix_filesystem,
            find_image_file,
            ensure_mounted,
            LOOPBACK_IMAGE_NAME,
        )
    except ImportError:
        return path

    LOGGER.debug(
        "  Checking if data root path '%s' (from %s) is on a non-POSIX filesystem...",
        path,
        source,
    )
    # Check the parent directory if path doesn't exist yet
    check_path = path if path.exists() else path.parent
    if not check_path.exists():
        return path

    if not is_non_posix_filesystem(check_path):
        return path

    # Found a non-POSIX filesystem — look for an ext4 image
    image = find_image_file(path)
    if image is None:
        LOGGER.error(
            "Data root '%s' is on a non-POSIX filesystem (exFAT/NTFS) which cannot "
            "host PostgreSQL data directories. To fix this, create a loopback image:\n"
            "    truncate -s 500G %s/%s\n"
            "    mkfs.ext4 %s/%s\n"
            "Then re-run the command.",
            path,
            path.parent,
            LOOPBACK_IMAGE_NAME,
            path.parent,
            LOOPBACK_IMAGE_NAME,
        )
        return path

    LOGGER.debug(
        "    Non-POSIX filesystem detected. Mounting loopback image: %s",
        image,
    )
    mount_point = ensure_mounted(image)
    if mount_point is None:
        LOGGER.error("Failed to mount loopback image %s", image)
        return path

    # Use .instances subdirectory inside the mounted ext4 volume
    loopback_data_root = mount_point / ".instances"
    loopback_data_root.mkdir(parents=True, exist_ok=True)

    LOGGER.debug(
        "  ➤ Redirected data root from non-POSIX '%s' → POSIX '%s' (via %s from %s)",
        path,
        loopback_data_root,
        image.name,
        source,
    )
    return loopback_data_root
