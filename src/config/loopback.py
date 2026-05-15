"""
Loopback Ext4 Image Manager
============================

Provides transparent POSIX-compliant storage on non-POSIX filesystems
(exFAT, NTFS, FAT32) by managing an ext4 loopback image file.

This allows PostgreSQL containers to use bind-mounted data directories
on external drives that lack native POSIX permission support.

Lifecycle:
    1. One-time setup:  `create_image()` creates and formats a sparse ext4 image
    2. Per-session:     `ensure_mounted()` auto-mounts it via `udisksctl` (no sudo)
    3. Shutdown:        `unmount()` cleanly detaches the loop device

The module is designed to be called transparently by `resolve_data_root`
when the target path is detected to reside on a non-POSIX filesystem.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from src.utils.logger import get_logger

logger = get_logger("LoopbackManager")

# Filesystems that do not support POSIX ownership/permissions
_NON_POSIX_FILESYSTEMS = frozenset({"exfat", "vfat", "fat32", "ntfs", "fuseblk"})

# Default image filename
LOOPBACK_IMAGE_NAME = "pbt_data.ext4"


def get_filesystem_type(path: Path) -> Optional[str]:
    """Return the filesystem type of the mount point containing *path*.

    Returns `None` if detection fails.
    """
    try:
        resolved = str(path.resolve())
        result = subprocess.run(
            ["df", "-T", resolved],
            capture_output=True,
            text=True,
            check=True,
        )
        # Parse the second line: /dev/sdb1  exfat  ...
        lines = result.stdout.strip().splitlines()
        if len(lines) >= 2:
            parts = lines[1].split()
            if len(parts) >= 2:
                return parts[1].lower()
    except (subprocess.CalledProcessError, OSError, IndexError):
        pass
    return None


def is_non_posix_filesystem(path: Path) -> bool:
    """Return `True` if *path* resides on a non-POSIX filesystem."""
    fs_type = get_filesystem_type(path)
    if fs_type is None:
        return False
    return fs_type in _NON_POSIX_FILESYSTEMS


def find_image_file(data_root: Path) -> Optional[Path]:
    """Search for an existing ext4 loopback image near *data_root*.

    Search order:
        1. `data_root / LOOPBACK_IMAGE_NAME`  (e.g., `.instances/pbt_data.ext4`)
        2. `data_root.parent / LOOPBACK_IMAGE_NAME`  (e.g., `PBTune/pbt_data.ext4`)
        3. Walk up to the mount point of the filesystem
    """
    # Walk up from data_root to the mount point
    candidate = data_root
    while True:
        image_path = candidate / LOOPBACK_IMAGE_NAME
        if image_path.is_file():
            return image_path
        if os.path.ismount(candidate) or candidate.parent == candidate:
            break
        candidate = candidate.parent
    return None


def get_loop_device_for_image(image_path: Path) -> Optional[str]:
    """Return the loop device (e.g. `/dev/loop0`) backing *image_path*, or `None`."""
    try:
        result = subprocess.run(
            ["losetup", "-j", str(image_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            # Output format: /dev/loop0: ... (/path/to/image)
            return result.stdout.strip().split(":")[0]
    except (OSError, IndexError):
        pass
    return None


def get_mount_point_for_device(device: str) -> Optional[Path]:
    """Return the mount point for a given block device, or `None`."""
    try:
        result = subprocess.run(
            ["findmnt", "-n", "-o", "TARGET", device],
            capture_output=True,
            text=True,
            check=True,
        )
        mount_point = result.stdout.strip()
        if mount_point:
            return Path(mount_point)
    except (subprocess.CalledProcessError, OSError):
        pass
    return None


def ensure_mounted(image_path: Path) -> Optional[Path]:
    """Ensure the ext4 loopback image is loop-mounted and return the mount point.

    Uses `udisksctl` for rootless mounting (standard on desktop Linux with
    KDE Plasma, GNOME, etc.).

    Returns the mount point `Path` on success, or `None` on failure.
    """
    if not image_path.is_file():
        logger.error("Loopback image not found: %s", image_path)
        return None

    # Check if already loop-mounted
    loop_device = get_loop_device_for_image(image_path)

    if loop_device:
        # Already set up as a loop device — check if mounted
        mount_point = get_mount_point_for_device(loop_device)
        if mount_point:
            logger.info(
                "Loopback image already mounted at %s (via %s)",
                mount_point,
                loop_device,
            )
            _fix_mount_ownership(mount_point)
            return mount_point
        else:
            # Loop device exists but not mounted — mount it
            logger.info(
                "Loop device %s exists but not mounted, mounting...", loop_device
            )
            try:
                result = subprocess.run(
                    ["udisksctl", "mount", "-b", loop_device, "--no-user-interaction"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                mount_point = _parse_udisksctl_mount_output(result.stdout)
                if mount_point:
                    logger.info("Mounted loopback at %s", mount_point)
                    _fix_mount_ownership(mount_point)
                    return mount_point
            except subprocess.CalledProcessError as e:
                logger.error(
                    "Failed to mount loop device %s: %s", loop_device, e.stderr
                )
                return None

    # Not set up yet — create loop device and mount
    logger.info("Setting up loopback for %s...", image_path)

    if not shutil.which("udisksctl"):
        logger.error(
            "udisksctl not found. Install udisks2 to enable rootless loopback mounting. "
            "On Arch: pacman -S udisks2"
        )
        return None

    try:
        # Step 1: Create the loop device
        result = subprocess.run(
            [
                "udisksctl",
                "loop-setup",
                "-f",
                str(image_path),
                "--no-user-interaction",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        loop_device = _parse_udisksctl_loop_setup_output(result.stdout)
        if not loop_device:
            logger.error(
                "Could not parse loop device from udisksctl output: %s", result.stdout
            )
            return None

        logger.debug("Loop device created: %s", loop_device)

        # Step 2: Mount it
        result = subprocess.run(
            ["udisksctl", "mount", "-b", loop_device, "--no-user-interaction"],
            capture_output=True,
            text=True,
            check=True,
        )
        mount_point = _parse_udisksctl_mount_output(result.stdout)
        if mount_point:
            logger.info("Loopback image mounted at %s", mount_point)
            _fix_mount_ownership(mount_point)
            return mount_point

        logger.error(
            "Could not parse mount point from udisksctl output: %s", result.stdout
        )
        return None

    except subprocess.CalledProcessError as e:
        logger.error("Failed to setup/mount loopback image: %s", e.stderr)
        return None


def unmount(image_path: Path) -> bool:
    """Unmount and detach the loopback image. Returns ``True`` on success."""
    loop_device = get_loop_device_for_image(image_path)
    if not loop_device:
        logger.debug("No loop device found for %s, nothing to unmount", image_path)
        return True

    try:
        # Unmount first
        mount_point = get_mount_point_for_device(loop_device)
        if mount_point:
            subprocess.run(
                ["udisksctl", "unmount", "-b", loop_device, "--no-user-interaction"],
                capture_output=True,
                text=True,
                check=True,
            )
            logger.info("Unmounted %s from %s", loop_device, mount_point)

        # Then delete the loop device
        subprocess.run(
            ["udisksctl", "loop-delete", "-b", loop_device, "--no-user-interaction"],
            capture_output=True,
            text=True,
            check=True,
        )
        logger.info("Detached loop device %s", loop_device)
        return True

    except subprocess.CalledProcessError as e:
        logger.error("Failed to unmount/detach loopback: %s", e.stderr)
        return False


def _fix_mount_ownership(mount_point: Path) -> None:
    """Ensure the ext4 mount root is owned by the current user.

    Freshly formatted ext4 images have their root directory owned by
    ``root:root``.  Since ``udisksctl`` mounts without ``sudo``, the
    calling user cannot ``chown`` the mount root directly.  We use a
    Docker container running as root to fix this.
    """
    uid = os.getuid()
    gid = os.getgid()

    # Quick check — skip if already owned by us
    try:
        stat = mount_point.stat()
        if stat.st_uid == uid and stat.st_gid == gid:
            return
    except OSError:
        return

    logger.debug("Fixing ownership of %s to %d:%d via Docker...", mount_point, uid, gid)
    try:
        import docker

        client = docker.from_env()
        client.containers.run(
            "alpine",
            entrypoint=["chown", f"{uid}:{gid}", "/mnt"],
            volumes={str(mount_point): {"bind": "/mnt", "mode": "rw"}},
            remove=True,
        )
        logger.debug("Ownership fixed successfully")
    except Exception as exc:
        logger.warning(
            "Could not fix ownership of %s: %s. "
            "You may need to run: sudo chown %d:%d %s",
            mount_point,
            exc,
            uid,
            gid,
            mount_point,
        )


def _parse_udisksctl_loop_setup_output(output: str) -> Optional[str]:
    """Extract the loop device path from ``udisksctl loop-setup`` output.

    Example output: ``Mapped file ... as /dev/loop0.``
    """
    match = re.search(r"as\s+(/dev/loop\d+)", output)
    return match.group(1) if match else None


def _parse_udisksctl_mount_output(output: str) -> Optional[Path]:
    """Extract the mount point from ``udisksctl mount`` output.

    Example output: ``Mounted /dev/loop0 at /run/media/user/abcdef.``
    """
    match = re.search(r"at\s+(.+?)\.?\s*$", output)
    return Path(match.group(1)) if match else None
