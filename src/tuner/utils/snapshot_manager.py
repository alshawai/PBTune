"""
Cross-Platform Snapshot Manager for PostgreSQL Data Directories
================================================================

This module provides a unified interface for creating and restoring
database snapshots across different platforms and filesystems.

Supported Methods:
-----------------
- **Btrfs** (Linux): Instant copy-on-write snapshots
- **APFS** (macOS): Native filesystem snapshots via tmutil
- **rsync** (Linux/macOS): Fast incremental file synchronization
- **robocopy** (Windows): Native Windows file mirroring
- **shutil** (All): Python fallback using shutil.copytree

The manager automatically detects the best available method for the
current platform and filesystem, falling back to slower but universal
methods when faster options aren't available.

Usage:
------
    >>> from src.tuner.utils.snapshot_manager import SnapshotManager, SnapshotConfig
    >>> 
    >>> config = SnapshotConfig(
    ...     baseline_path=Path("/pg_snapshots/baseline"),
    ...     restore_interval=1
    ... )
    >>> manager = SnapshotManager(config)
    >>> 
    >>> # Create baseline from clean database
    >>> manager.create_baseline(Path("/pg_instances/worker_0"))
    >>> 
    >>> # Restore worker to baseline state
    >>> manager.restore_worker(Path("/pg_instances/worker_0"))

Architecture:
------------
The module uses the Strategy pattern to support multiple snapshot methods:

    SnapshotManager
        │
        ├── detect_best_method() → SnapshotMethod
        │
        └── _get_strategy(method) → SnapshotStrategy
                                        │
                    ┌───────────────────┼────────────────────┐
                    │                   │                    │
                BtrfsStrategy     RsyncStrategy       RobocopyStrategy
                                        │
                                ShutilStrategy (fallback)
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable
import platform
import shutil
import subprocess
import time
import concurrent.futures

from src.database.connection import get_connection
from src.config.database import DatabaseConfig
from src.tuner.utils.logger_config import get_logger
        
logger = get_logger(__name__)


class SnapshotMethod(Enum):
    """
    Available snapshot methods ordered by preference.
    
    Higher preference methods are faster and more space-efficient.
    The manager will automatically select the best available method.
    """
    BTRFS = "btrfs"           # Linux with Btrfs filesystem - instant snapshots
    APFS = "apfs"             # macOS with APFS - native snapshots
    RSYNC = "rsync"           # Linux/macOS - fast incremental copy
    ROBOCOPY = "robocopy"     # Windows - native file mirroring
    SHUTIL = "shutil"         # All platforms - Python fallback


@dataclass
class SnapshotConfig:
    """
    Configuration for SnapshotManager.
    
    Attributes
    ----------
    baseline_path : Path
        Directory to store baseline snapshot data.
        This should be on the same filesystem as worker directories
        for Btrfs/APFS to work optimally.
        
    restore_interval : int
        Restore snapshots every N generations.
        - 1 = restore every generation (maximum consistency)
        - 5 = restore every 5 generations (faster training)
        Default: 1
        
    method : Optional[SnapshotMethod]
        Force a specific snapshot method. If None, auto-detect.
        Default: None (auto-detect)
        
    exclude_configs : bool
        Whether to exclude PostgreSQL config files from restore.
        When True, keeps worker's tuned postgresql.conf.
        Default: True
        
    parallel_restore : bool
        Whether to restore multiple workers in parallel.
        Default: True
        
    max_parallel_workers : int
        Maximum number of parallel restore operations.
        Default: 4
        
    verify_restore : bool
        Whether to verify file integrity after restore.
        Adds overhead but ensures correctness.
        Default: False
    """
    baseline_path: Path
    restore_interval: int = 1
    method: Optional[SnapshotMethod] = None
    exclude_configs: bool = True
    parallel_restore: bool = True
    max_parallel_workers: int = 4
    verify_restore: bool = False

    # Files to exclude from restore (keep worker's config)
    excluded_files: List[str] = field(default_factory=lambda: [
        'postgresql.conf',
        'postgresql.auto.conf',
        'postmaster.pid',
        'postmaster.opts',
        'pg_hba.conf',
        'pg_ident.conf',
    ])

    def __post_init__(self):
        """Validate configuration."""
        if self.restore_interval < 1:
            raise ValueError("restore_interval must be at least 1")
        if self.max_parallel_workers < 1:
            raise ValueError("max_parallel_workers must be at least 1")

        # Ensure baseline_path is a Path object
        if isinstance(self.baseline_path, str):
            self.baseline_path = Path(self.baseline_path)


class SnapshotStrategy(ABC):
    """
    Abstract base class for snapshot strategies.
    
    Each strategy implements platform/filesystem-specific
    snapshot and restore operations.
    """

    @property
    @abstractmethod
    def method(self) -> SnapshotMethod:
        """Return the snapshot method this strategy implements."""

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this method is available on the current system."""

    @abstractmethod
    def create_snapshot(
        self,
        source_path: Path,
        snapshot_path: Path,
        excluded_files: List[str]
    ) -> bool:
        """
        Create a snapshot of the source directory.
        
        Parameters
        ----------
        source_path : Path
            Source directory to snapshot
        snapshot_path : Path
            Destination for snapshot
        excluded_files : List[str]
            Files to exclude from snapshot
            
        Returns
        -------
        bool
            True if successful, False otherwise
        """

    @abstractmethod
    def restore_snapshot(
        self,
        snapshot_path: Path,
        target_path: Path,
        excluded_files: List[str]
    ) -> bool:
        """
        Restore a snapshot to the target directory.
        
        Parameters
        ----------
        snapshot_path : Path
            Source snapshot directory
        target_path : Path
            Target directory to restore to
        excluded_files : List[str]
            Files to exclude from restore (keep existing)
            
        Returns
        -------
        bool
            True if successful, False otherwise
        """


class BtrfsStrategy(SnapshotStrategy):
    """
    Btrfs filesystem snapshot strategy.
    
    Uses Btrfs subvolume snapshots for instant, space-efficient
    copy-on-write snapshots. Only works on Btrfs filesystems.
    
    Advantages:
    - Instant snapshot creation (O(1) time)
    - Space-efficient (only stores differences)
    - Atomic operations
    
    Requirements:
    - Linux with Btrfs filesystem
    - btrfs-progs installed
    - Source/target on Btrfs subvolume
    """

    @property
    def method(self) -> SnapshotMethod:
        return SnapshotMethod.BTRFS

    def is_available(self) -> bool:
        """Check if Btrfs is available and usable."""
        if platform.system() != "Linux":
            return False

        if not shutil.which("btrfs"):
            return False

        return True

    def _is_btrfs_path(self, path: Path) -> bool:
        """Check if path is on a Btrfs filesystem."""
        try:
            result = subprocess.run(
                ["stat", "-f", "-c", "%T", str(path)],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.stdout.strip() == "btrfs"
        except (subprocess.SubprocessError, OSError):
            return False

    def _is_subvolume(self, path: Path) -> bool:
        """Check if path is a Btrfs subvolume."""
        try:
            result = subprocess.run(
                ["btrfs", "subvolume", "show", str(path)],
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, OSError):
            return False

    def create_snapshot(
        self,
        source_path: Path,
        snapshot_path: Path,
        excluded_files: List[str]
    ) -> bool:
        """Create a Btrfs subvolume snapshot."""
        if not self._is_btrfs_path(source_path):
            logger.error("Source path is not on Btrfs filesystem: %s", source_path)
            return False

        try:
            # Remove existing snapshot if present
            if snapshot_path.exists():
                if self._is_subvolume(snapshot_path):
                    subprocess.run(
                        ["btrfs", "subvolume", "delete", str(snapshot_path)],
                        check=True,
                        capture_output=True,
                        timeout=30
                    )
                else:
                    shutil.rmtree(snapshot_path)

            # Create read-only snapshot
            result = subprocess.run(
                ["btrfs", "subvolume", "snapshot", "-r", str(source_path), str(snapshot_path)],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                logger.error("Btrfs snapshot failed: %s", result.stderr)
                return False

            logger.info("Created Btrfs snapshot: %s -> %s", source_path, snapshot_path)
            return True

        except subprocess.SubprocessError as e:
            logger.error("Btrfs snapshot error: %s", e)
            return False

    def restore_snapshot(
        self,
        snapshot_path: Path,
        target_path: Path,
        excluded_files: List[str]
    ) -> bool:
        """Restore from Btrfs snapshot."""
        if not self._is_btrfs_path(snapshot_path):
            logger.error("Snapshot path is not on Btrfs filesystem: %s", snapshot_path)
            return False

        try:
            # Save excluded files
            saved_files = {}
            for filename in excluded_files:
                src_file = target_path / filename
                if src_file.exists():
                    saved_files[filename] = src_file.read_bytes()

            # Delete existing target subvolume if it exists
            if target_path.exists():
                if self._is_subvolume(target_path):
                    subprocess.run(
                        ["btrfs", "subvolume", "delete", str(target_path)],
                        check=True,
                        capture_output=True,
                        timeout=30
                    )
                else:
                    shutil.rmtree(target_path)

            # Create writable snapshot from baseline
            result = subprocess.run(
                ["btrfs", "subvolume", "snapshot", str(snapshot_path), str(target_path)],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                logger.error("Btrfs restore failed: %s", result.stderr)
                return False

            # Restore excluded files
            for filename, content in saved_files.items():
                (target_path / filename).write_bytes(content)

            logger.debug("Restored from Btrfs snapshot: %s -> %s", snapshot_path, target_path)
            return True

        except subprocess.SubprocessError as e:
            logger.error("Btrfs restore error: %s", e)
            return False


class APFSStrategy(SnapshotStrategy):
    """
    macOS APFS filesystem snapshot strategy.
    
    Uses APFS local snapshots via tmutil for efficient snapshots.
    This is the preferred method on macOS.
    
    Advantages:
    - Native macOS support
    - Space-efficient snapshots
    - Fast creation and restore
    
    Requirements:
    - macOS 10.13+ with APFS filesystem
    - tmutil available
    
    Note:
    APFS snapshots are volume-level, not directory-level.
    We use tmutil for local snapshots and rsync for restore.
    """
    
    @property
    def method(self) -> SnapshotMethod:
        return SnapshotMethod.APFS
    
    def is_available(self) -> bool:
        """Check if APFS snapshots are available."""
        # Must be macOS
        if platform.system() != "Darwin":
            return False
        
        # Check if tmutil exists
        if not shutil.which("tmutil"):
            return False
        
        return True
    
    def _get_filesystem_type(self, path: Path) -> str:
        """Get filesystem type for a path."""
        try:
            result = subprocess.run(
                ["diskutil", "info", str(path)],
                capture_output=True,
                text=True,
                timeout=10
            )
            for line in result.stdout.split('\n'):
                if 'Type (Bundle):' in line:
                    return line.split(':')[1].strip()
            return ""
        except (subprocess.SubprocessError, OSError):
            return ""
    
    def create_snapshot(
        self,
        source_path: Path,
        snapshot_path: Path,
        excluded_files: List[str]
    ) -> bool:
        """
        Create snapshot using rsync (APFS snapshots are volume-level).
        
        For directory-level snapshots on APFS, we fall back to rsync
        which is still efficient due to APFS clone support.
        """
        try:
            # Use rsync with APFS clone support
            rsync_args = [
                "rsync", "-a", "-c", "--delete",
                "--exclude=.DS_Store",
            ]
            
            for excluded in excluded_files:
                rsync_args.extend(["--exclude", excluded])
            
            rsync_args.extend([
                f"{source_path}/",
                f"{snapshot_path}/"
            ])
            
            # Create destination if needed
            snapshot_path.mkdir(parents=True, exist_ok=True)
            
            result = subprocess.run(
                rsync_args,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            if result.returncode != 0:
                logger.error("APFS snapshot (rsync) failed: %s", result.stderr)
                return False
            
            logger.info("Created APFS snapshot (via rsync): %s -> %s", source_path, snapshot_path)
            return True
            
        except subprocess.SubprocessError as e:
            logger.error("APFS snapshot error: %s", e)
            return False
    
    def restore_snapshot(
        self,
        snapshot_path: Path,
        target_path: Path,
        excluded_files: List[str]
    ) -> bool:
        """Restore snapshot using rsync."""
        try:
            rsync_args = [
                "rsync", "-a", "-c", "--delete",
                "--exclude=.DS_Store",
            ]
            
            for excluded in excluded_files:
                rsync_args.extend(["--exclude", excluded])
            
            rsync_args.extend([
                f"{snapshot_path}/",
                f"{target_path}/"
            ])
            
            result = subprocess.run(
                rsync_args,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            if result.returncode != 0:
                logger.error("APFS restore (rsync) failed: %s", result.stderr)
                return False
            
            logger.debug("Restored from APFS snapshot: %s -> %s", snapshot_path, target_path)
            return True
            
        except subprocess.SubprocessError as e:
            logger.error("APFS restore error: %s", e)
            return False


class RsyncStrategy(SnapshotStrategy):
    """
    rsync-based snapshot strategy.
    
    Uses rsync for fast incremental file synchronization.
    Works on Linux and macOS with any filesystem.
    
    Advantages:
    - Fast incremental copies (only changed files)
    - Works on any filesystem
    - Reliable and well-tested
    - Preserves permissions and timestamps
    
    Requirements:
    - rsync installed (standard on Linux/macOS)
    """
    
    @property
    def method(self) -> SnapshotMethod:
        return SnapshotMethod.RSYNC
    
    def is_available(self) -> bool:
        """Check if rsync is available."""
        return shutil.which("rsync") is not None
    
    def create_snapshot(
        self,
        source_path: Path,
        snapshot_path: Path,
        excluded_files: List[str]
    ) -> bool:
        """Create snapshot using rsync."""
        try:
            # Build rsync command
            rsync_args = [
                "rsync",
                "-a",           # Archive mode (preserves everything)
                "-c",           # Checksum mode (compare by content, not size+mtime)
                "--delete",     # Delete extraneous files from destination
            ]
            
            # Add exclusions
            for excluded in excluded_files:
                rsync_args.extend(["--exclude", excluded])
            
            # Source and destination (trailing slash is important!)
            rsync_args.extend([
                f"{source_path}/",
                f"{snapshot_path}/"
            ])
            
            # Create destination if needed
            snapshot_path.mkdir(parents=True, exist_ok=True)
            
            result = subprocess.run(
                rsync_args,
                capture_output=True,
                text=True,
                timeout=600  # 10 minute timeout for large databases
            )
            
            # rsync exit code 24 means "some files vanished" which is normal
            # for PostgreSQL WAL files that rotate during copy
            if result.returncode not in (0, 24):
                logger.error("rsync snapshot failed: %s", result.stderr)
                return False

            if result.returncode == 24:
                logger.warning("rsync completed with vanished files")

            logger.info("Created rsync snapshot: %s -> %s", source_path, snapshot_path)
            return True
            
        except subprocess.TimeoutExpired:
            logger.error("rsync snapshot timed out after 600s")
            return False
        except subprocess.SubprocessError as e:
            logger.error("rsync snapshot error: %s", e)
            return False
    
    def restore_snapshot(
        self,
        snapshot_path: Path,
        target_path: Path,
        excluded_files: List[str]
    ) -> bool:
        """Restore snapshot using rsync."""
        try:
            rsync_args = [
                "rsync",
                "-a",           # Archive mode
                "-c",           # Checksum mode (compare by content, not size+mtime)
                "--delete",     # Delete files not in source
            ]
            
            # Add exclusions (keep these files in target)
            for excluded in excluded_files:
                rsync_args.extend(["--exclude", excluded])
            
            rsync_args.extend([
                f"{snapshot_path}/",
                f"{target_path}/"
            ])
            
            result = subprocess.run(
                rsync_args,
                capture_output=True,
                text=True,
                timeout=600
            )
            
            # rsync exit code 24 means "some files vanished" - treat as success
            if result.returncode not in (0, 24):
                logger.error("rsync restore failed: %s", result.stderr)
                return False
            
            logger.debug("Restored from rsync snapshot: %s -> %s", snapshot_path, target_path)
            return True
            
        except subprocess.TimeoutExpired:
            logger.error("rsync restore timed out after 600s")
            return False
        except subprocess.SubprocessError as e:
            logger.error("rsync restore error: %s", e)
            return False


class RobocopyStrategy(SnapshotStrategy):
    """
    Windows robocopy-based snapshot strategy.
    
    Uses robocopy (Robust File Copy) for efficient file mirroring.
    Built into Windows, no additional installation required.
    
    Advantages:
    - Native Windows tool (no installation)
    - Fast mirroring with /MIR flag
    - Handles long paths and special characters
    - Automatic retry on failures
    
    Requirements:
    - Windows OS
    - robocopy (included in Windows Vista+)
    """
    
    @property
    def method(self) -> SnapshotMethod:
        return SnapshotMethod.ROBOCOPY
    
    def is_available(self) -> bool:
        """Check if robocopy is available."""
        if platform.system() != "Windows":
            return False
        return shutil.which("robocopy") is not None
    
    def create_snapshot(
        self,
        source_path: Path,
        snapshot_path: Path,
        excluded_files: List[str]
    ) -> bool:
        """Create snapshot using robocopy."""
        try:
            # Build robocopy command
            robocopy_args = [
                "robocopy",
                str(source_path),
                str(snapshot_path),
                "/MIR",         # Mirror mode (like rsync --delete)
                "/R:3",         # Retry 3 times
                "/W:1",         # Wait 1 second between retries
                "/NP",          # No progress (cleaner output)
                "/NDL",         # No directory list
                "/NFL",         # No file list
            ]
            
            # Add exclusions
            if excluded_files:
                robocopy_args.append("/XF")
                robocopy_args.extend(excluded_files)
            
            # Create destination if needed
            snapshot_path.mkdir(parents=True, exist_ok=True)
            
            result = subprocess.run(
                robocopy_args,
                capture_output=True,
                text=True,
                timeout=600
            )
            
            # robocopy exit codes: 0-7 are success, 8+ are errors
            if result.returncode >= 8:
                logger.error("robocopy snapshot failed (exit %d): %s", 
                           result.returncode, result.stderr)
                return False
            
            logger.info("Created robocopy snapshot: %s -> %s", source_path, snapshot_path)
            return True
            
        except subprocess.TimeoutExpired:
            logger.error("robocopy snapshot timed out after 600s")
            return False
        except subprocess.SubprocessError as e:
            logger.error("robocopy snapshot error: %s", e)
            return False
    
    def restore_snapshot(
        self,
        snapshot_path: Path,
        target_path: Path,
        excluded_files: List[str]
    ) -> bool:
        """Restore snapshot using robocopy."""
        try:
            robocopy_args = [
                "robocopy",
                str(snapshot_path),
                str(target_path),
                "/MIR",
                "/R:3",
                "/W:1",
                "/NP",
                "/NDL",
                "/NFL",
            ]
            
            if excluded_files:
                robocopy_args.append("/XF")
                robocopy_args.extend(excluded_files)
            
            result = subprocess.run(
                robocopy_args,
                capture_output=True,
                text=True,
                timeout=600
            )
            
            if result.returncode >= 8:
                logger.error("robocopy restore failed (exit %d): %s",
                           result.returncode, result.stderr)
                return False
            
            logger.debug("Restored from robocopy snapshot: %s -> %s", snapshot_path, target_path)
            return True
            
        except subprocess.TimeoutExpired:
            logger.error("robocopy restore timed out after 600s")
            return False
        except subprocess.SubprocessError as e:
            logger.error("robocopy restore error: %s", e)
            return False


class ShutilStrategy(SnapshotStrategy):
    """
    Python shutil-based snapshot strategy (fallback).
    
    Uses Python's shutil.copytree for cross-platform file copying.
    This is the slowest method but works everywhere.
    
    Advantages:
    - Works on all platforms
    - No external dependencies
    - Pure Python implementation
    
    Disadvantages:
    - Slower than native tools
    - Always does full copy (not incremental)
    """
    
    @property
    def method(self) -> SnapshotMethod:
        return SnapshotMethod.SHUTIL
    
    def is_available(self) -> bool:
        """shutil is always available."""
        return True
    
    def _ignore_files(self, excluded_files: List[str]) -> Callable:
        """Create ignore function for shutil.copytree."""
        def ignore(directory: str, files: List[str]) -> List[str]:
            return [f for f in files if f in excluded_files]
        return ignore
    
    def create_snapshot(
        self,
        source_path: Path,
        snapshot_path: Path,
        excluded_files: List[str]
    ) -> bool:
        """Create snapshot using shutil.copytree."""
        try:
            # Remove existing snapshot
            if snapshot_path.exists():
                shutil.rmtree(snapshot_path)
            
            # Copy with exclusions
            shutil.copytree(
                source_path,
                snapshot_path,
                ignore=self._ignore_files(excluded_files),
                dirs_exist_ok=True
            )
            
            logger.info("Created shutil snapshot: %s -> %s", source_path, snapshot_path)
            return True
            
        except (OSError, shutil.Error) as e:
            logger.error("shutil snapshot error: %s", e)
            return False
    
    def restore_snapshot(
        self,
        snapshot_path: Path,
        target_path: Path,
        excluded_files: List[str]
    ) -> bool:
        """Restore snapshot using shutil."""
        try:
            # Save excluded files
            saved_files: Dict[str, bytes] = {}
            for filename in excluded_files:
                src_file = target_path / filename
                if src_file.exists():
                    saved_files[filename] = src_file.read_bytes()
            
            # Remove existing target contents (except excluded)
            if target_path.exists():
                for item in target_path.iterdir():
                    if item.name not in excluded_files:
                        if item.is_dir():
                            shutil.rmtree(item)
                        else:
                            item.unlink()
            
            # Copy from snapshot
            for item in snapshot_path.iterdir():
                if item.name not in excluded_files:
                    dest = target_path / item.name
                    if item.is_dir():
                        shutil.copytree(item, dest, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, dest)
            
            # Restore excluded files
            for filename, content in saved_files.items():
                (target_path / filename).write_bytes(content)
            
            logger.debug("Restored from shutil snapshot: %s -> %s", snapshot_path, target_path)
            return True
            
        except (OSError, shutil.Error) as e:
            logger.error("shutil restore error: %s", e)
            return False


def detect_best_snapshot_method(target_path: Optional[Path] = None) -> SnapshotMethod:
    """
    Detect the best available snapshot method for the current platform.
    
    Checks methods in order of preference (fastest first) and returns
    the first available method.
    
    Parameters
    ----------
    target_path : Optional[Path]
        Path where snapshots will be stored. Used to check
        filesystem-specific methods like Btrfs.
        
    Returns
    -------
    SnapshotMethod
        The best available snapshot method
    """
    strategies = [
        BtrfsStrategy(),    # Linux + Btrfs (instant)
        APFSStrategy(),     # macOS (native)
        RsyncStrategy(),    # Linux/macOS (fast)
        RobocopyStrategy(), # Windows (native)
        ShutilStrategy(),   # All (fallback)
    ]

    for strategy in strategies:
        if strategy.is_available():
            # For Btrfs, also check if target path is on Btrfs
            if strategy.method == SnapshotMethod.BTRFS and target_path:
                if not strategy._is_btrfs_path(target_path):
                    continue

            logger.info("Detected best snapshot method: %s", strategy.method.value)
            return strategy.method

    # Should never reach here (ShutilStrategy is always available)
    return SnapshotMethod.SHUTIL


class SnapshotManager:
    """
    Cross-platform manager for PostgreSQL data directory snapshots.
    
    Automatically detects the best available snapshot method and provides
    a unified interface for creating and restoring snapshots.
    
    Attributes
    ----------
    config : SnapshotConfig
        Configuration for snapshot behavior
    strategy : SnapshotStrategy
        The active snapshot strategy
    baseline_created : bool
        Whether a baseline snapshot exists
        
    Example
    -------
    >>> config = SnapshotConfig(baseline_path=Path("/snapshots/baseline"))
    >>> manager = SnapshotManager(config)
    >>> 
    >>> # Create baseline from worker 0
    >>> manager.create_baseline(Path("/pg_instances/worker_0"))
    >>> 
    >>> # Before each generation, restore all workers
    >>> worker_paths = [Path(f"/pg_instances/worker_{i}") for i in range(4)]
    >>> manager.restore_all_workers(worker_paths)
    """

    # Strategy class mapping
    STRATEGY_CLASSES = {
        SnapshotMethod.BTRFS: BtrfsStrategy,
        SnapshotMethod.APFS: APFSStrategy,
        SnapshotMethod.RSYNC: RsyncStrategy,
        SnapshotMethod.ROBOCOPY: RobocopyStrategy,
        SnapshotMethod.SHUTIL: ShutilStrategy,
    }

    def __init__(self, config: SnapshotConfig):
        """
        Initialize SnapshotManager.
        
        Parameters
        ----------
        config : SnapshotConfig
            Snapshot configuration
        """
        self.config = config

        if config.method is None:
            self.method = detect_best_snapshot_method(config.baseline_path)
        else:
            self.method = config.method

        self.strategy = self.STRATEGY_CLASSES[self.method]()

        if not self.strategy.is_available():  # Fallback check
            logger.warning(
                "Specified method %s not available, falling back to shutil",
                self.method.value
            )
            self.method = SnapshotMethod.SHUTIL
            self.strategy = ShutilStrategy()

        # Check if baseline exists AND has content (not just an empty directory)
        self.baseline_created = self._is_baseline_valid()

        logger.info(
            "Initialized SnapshotManager: method=%s, baseline=%s, exists=%s",
            self.method.value,
            self.config.baseline_path,
            self.baseline_created
        )

    def _is_baseline_valid(self) -> bool:
        """
        Check if the baseline directory exists AND contains actual data.
        
        Returns
        -------
        bool
            True if baseline exists and contains PostgreSQL data files
        """
        baseline = self.config.baseline_path

        if not baseline.exists():
            return False

        if not baseline.is_dir():
            return False

        # Check for essential PostgreSQL files/directories
        essential_markers = [
            'PG_VERSION',           # PostgreSQL version file
            'base',                 # Database files directory
            'global',               # Cluster-wide tables
        ]

        for marker in essential_markers:
            if not (baseline / marker).exists():
                logger.debug(
                    "Baseline missing essential marker: %s", marker
                )
                return False

        base_dir = baseline / 'base'
        if base_dir.is_dir():
            base_contents = list(base_dir.iterdir())
            if len(base_contents) == 0:
                logger.debug("Baseline 'base' directory is empty")
                return False

        return True

    def _create_baseline_snapshot(self, source_path: Path, force: bool = False) -> bool:
        """
        Internal: Create snapshot from a STOPPED database directory.
        
        If the baseline already exists, it will be skipped unless force=True.
        """
        if force and self.baseline_created:
            logger.info("Force recreating baseline snapshot (existing will be replaced)")

        logger.info("Creating baseline snapshot from %s", source_path)

        if not source_path.exists():
            logger.error("Source path does not exist: %s", source_path)
            return False

        self.config.baseline_path.parent.mkdir(parents=True, exist_ok=True)

        start_time = time.time()
        success = self.strategy.create_snapshot(
            source_path=source_path,
            snapshot_path=self.config.baseline_path,
            excluded_files=[]  # Include everything in baseline
        )
        elapsed = time.time() - start_time

        if success:
            self.baseline_created = True
            logger.info("Baseline snapshot created in %.2fs", elapsed)
        else:
            logger.error("Failed to create baseline snapshot")

        return success

    def create_baseline(
        self,
        source_path: Path,
        instance_manager: Any,
        worker_id: int = 0,
        force: bool = False,
        wait_timeout: float = 15.0
    ) -> bool:
        """
        Create baseline snapshot from a PostgreSQL instance.
        
        Handles the full workflow:
        1. Stop the instance (required for data consistency)
        2. Create snapshot from the stopped data directory  
        3. Restart the instance
        4. Wait for the instance to accept connections
        
        Parameters
        ----------
        source_path : Path
            Path to the PostgreSQL data directory
        instance_manager : PostgresInstanceManager
            Instance manager for stopping/starting the instance
        worker_id : int, default=0
            Worker ID of the instance to snapshot
        force : bool, default=False
            If True, recreate baseline even if it already exists
        wait_timeout : float, default=15.0
            Maximum seconds to wait for instance to be ready after restart
            
        Returns
        -------
        bool
            True if baseline was created and instance restarted successfully
        """
        # Check if baseline already exists
        if self.baseline_created and not force:
            logger.info(
                "Baseline snapshot already exists at %s (skipping creation)",
                self.config.baseline_path
            )
            return True

        logger.info("Creating baseline snapshot from worker-%d...", worker_id)

        logger.debug("Stopping worker-%d for clean baseline snapshot...", worker_id)
        instance_manager.stop_instance(worker_id)

        success = self._create_baseline_snapshot(source_path, force=force)

        logger.debug("Restarting worker-%d after baseline snapshot...", worker_id)
        instance_manager.start_instance(worker_id)

        if success:
            ready = self._wait_for_instance_ready(
                instance_manager,
                worker_id,
                timeout=wait_timeout
            )
            if not ready:
                logger.warning(
                    "Worker-%d may not be fully ready after %.1fs wait",
                    worker_id, wait_timeout
                )

        return success

    def _wait_for_instance_ready(
        self,
        instance_manager: Any,
        worker_id: int,
        timeout: float = 15.0,
        check_interval: float = 0.5
    ) -> bool:
        """
        Wait for a PostgreSQL instance to accept connections.
        
        Parameters
        ----------
        instance_manager : PostgresInstanceManager
            Instance manager with instance configurations
        worker_id : int
            Worker ID to check
        timeout : float
            Maximum seconds to wait
        check_interval : float
            Seconds between connection attempts
            
        Returns
        -------
        bool
            True if instance is ready, False if timeout reached
        """

        instances = instance_manager.instances
        if worker_id not in instances:
            logger.error("Worker-%d not found in instance manager", worker_id)
            return False

        port = instances[worker_id].port

        logger.debug("Waiting for worker-%d to accept connections on port %d...", worker_id, port)

        start_time = time.time()
        while (time.time() - start_time) < timeout:
            try:
                # Try to connect
                test_config = DatabaseConfig(
                    host='localhost',
                    port=str(port),
                    dbname='postgres',
                    user='postgres',
                    password=''
                )
                conn = get_connection(config=test_config, connect_timeout=1)
                conn.close()

                elapsed = time.time() - start_time
                logger.debug("Worker-%d ready after %.1fs", worker_id, elapsed)
                return True

            except Exception:
                time.sleep(check_interval)

        return False

    def restore_worker(self, worker_path: Path) -> bool:
        """
        Restore a worker's data directory from the baseline snapshot.
        
        Parameters
        ----------
        worker_path : Path
            Path to the worker's data directory
            
        Returns
        -------
        bool
            True if restore was successful
        """
        if not self.baseline_created:
            logger.error("No baseline snapshot exists. Call create_baseline() first.")
            return False

        if not worker_path.exists():
            logger.warning("Worker path does not exist, will be created: %s", worker_path)
            worker_path.mkdir(parents=True, exist_ok=True)

        return self.strategy.restore_snapshot(
            snapshot_path=self.config.baseline_path,
            target_path=worker_path,
            excluded_files=self.config.excluded_files if self.config.exclude_configs else []
        )

    def restore_all_workers(
        self,
        worker_paths: List[Path],
        parallel: Optional[bool] = None
    ) -> Dict[Path, bool]:
        """
        Restore all workers from baseline snapshot.
        
        Parameters
        ----------
        worker_paths : List[Path]
            Paths to all worker data directories
        parallel : Optional[bool]
            Whether to restore in parallel. If None, uses config setting.
            
        Returns
        -------
        Dict[Path, bool]
            Mapping of worker path to restore success status
        """
        if not self.baseline_created:
            logger.error("No baseline snapshot exists")
            return {p: False for p in worker_paths}

        use_parallel = parallel if parallel is not None else self.config.parallel_restore
        results: Dict[Path, bool] = {}

        start_time = time.time()
        logger.info(
            "Restoring %d workers from baseline (parallel=%s)",
            len(worker_paths), use_parallel
        )

        if use_parallel and len(worker_paths) > 1:
            # Parallel restore using ThreadPoolExecutor
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(self.config.max_parallel_workers, len(worker_paths))
            ) as executor:
                future_to_path = {
                    executor.submit(self.restore_worker, path): path
                    for path in worker_paths
                }

                for future in concurrent.futures.as_completed(future_to_path):
                    path = future_to_path[future]
                    try:
                        results[path] = future.result()
                    except Exception as e:
                        logger.error("Restore failed for %s: %s", path, e)
                        results[path] = False
        else:
            # Sequential restore
            for path in worker_paths:
                results[path] = self.restore_worker(path)

        elapsed = time.time() - start_time
        success_count = sum(1 for v in results.values() if v)
        logger.info(
            "Restored %d/%d workers in %.2fs",
            success_count, len(worker_paths), elapsed
        )

        return results

    def should_restore(self, generation: int) -> bool:
        """
        Check if snapshots should be restored for this generation.
        
        Parameters
        ----------
        generation : int
            Current generation number
            
        Returns
        -------
        bool
            True if restore should be performed
        """
        if not self.baseline_created:
            return False

        # Restore at generation 0 and every N generations after
        return generation % self.config.restore_interval == 0

    def get_status(self) -> Dict[str, Any]:
        """
        Get current snapshot manager status.
        
        Returns
        -------
        Dict[str, Any]
            Status information
        """
        return {
            "method": self.method.value,
            "baseline_path": str(self.config.baseline_path),
            "baseline_exists": self.baseline_created,
            "restore_interval": self.config.restore_interval,
            "exclude_configs": self.config.exclude_configs,
            "parallel_restore": self.config.parallel_restore,
            "platform": platform.system(),
        }

    def __repr__(self) -> str:
        return (
            f"SnapshotManager(method={self.method.value}, "
            f"baseline_exists={self.baseline_created})"
        )
