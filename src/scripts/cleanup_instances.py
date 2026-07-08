"""
Cleanup script for PostgreSQL instances

Stops all running instances and optionally removes data directories.
"""

from __future__ import annotations
import argparse
import logging
import shutil
import sys
from pathlib import Path

from src.config.data_root import resolve_data_root
from src.config.database import DatabaseConfig
from src.benchmarks.executor import BenchmarkExecutor
from src.utils.environments import EnvironmentFactory, InstanceConfig
from src.utils.metrics import PerformanceMetrics

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


class _NoopBenchmarkExecutor(BenchmarkExecutor):
    """Minimal schema provider used for environment lifecycle-only cleanup."""

    def prepare(self, db_config: DatabaseConfig) -> None:
        """No-op prepare method."""
        return

    def validate(self, db_config: DatabaseConfig) -> bool:
        """No-op validate method."""
        return True

    def execute(
        self,
        db_config: DatabaseConfig,
        worker_id: int | None = None,
        **kwargs: object,
    ) -> PerformanceMetrics:
        """No-op execute method."""
        del db_config, worker_id, kwargs
        return PerformanceMetrics()


def _docker_force_remove(path: Path) -> None:
    """Uses a root Docker container to bypass permission boundaries and rm -rf a path."""
    if not path.exists():
        return
    abs_path = path.resolve()
    parent = str(abs_path.parent)
    child = abs_path.name
    try:
        import docker

        client = docker.from_env()
        client.containers.run(
            "alpine",
            entrypoint=["rm", "-rf", f"/host/{child}"],
            volumes={parent: {"bind": "/host", "mode": "rw"}},
            network_mode="none",
            remove=True,
        )
    except Exception as e:
        logging.warning("Failed to force-remove %s via Docker: %s", abs_path, e)
        # Fallback: try sudo rm -rf (common on Linux dev machines)
        import subprocess

        try:
            subprocess.run(
                ["sudo", "rm", "-rf", str(abs_path)],
                check=True,
                capture_output=True,
                timeout=30,
            )
        except Exception:
            # Final fallback to standard shutil (best-effort)
            shutil.rmtree(abs_path, ignore_errors=True)


def _docker_cleanup_trash(base_dir: Path | None = None) -> None:
    """Aggressively hunts down Docker-owned .instances folders that got stuck in KDE/Gnome trash."""
    trash_locations = [
        Path.home() / ".local" / "share" / "Trash" / "files",
    ]

    if base_dir:
        import os

        # Find the mount point of the active data directory to handle external HDDs
        mount_point = base_dir
        # Walk up the tree until we hit a mount point or the root
        while not os.path.ismount(mount_point) and mount_point.parent != mount_point:
            mount_point = mount_point.parent

        # Add any .Trash-*/files directories found on that mount point
        for trash_dir in mount_point.glob(".Trash-*/files"):
            if trash_dir.is_dir() and trash_dir not in trash_locations:
                trash_locations.append(trash_dir)

    import docker

    try:
        client = docker.from_env()
    except Exception:
        return  # Docker not available

    for trash_dir in trash_locations:
        if trash_dir.exists():
            try:
                client.containers.run(
                    "alpine",
                    entrypoint=["sh", "-c"],
                    command=["rm -rf /host/.instances*"],
                    volumes={str(trash_dir): {"bind": "/host", "mode": "rw"}},
                    network_mode="none",
                    remove=True,
                )
            except Exception as e:
                logging.debug("Failed to clear Trash %s via Docker: %s", trash_dir, e)


def main():
    """Main entry point for cleanup script."""
    parser = argparse.ArgumentParser(description="Cleanup PostgreSQL instances")
    parser.add_argument(
        "--remove-data",
        action="store_true",
        help="Remove data directories (WARNING: destroys all data)",
    )
    parser.add_argument(
        "--remove-snapshots",
        action="store_true",
        help="Remove global baseline snapshots and Docker images",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="Base directory for instances. Overrides PBT_DATA_ROOT (default: ./.instances)",
    )
    parser.add_argument(
        "--force", action="store_true", help="Force removal without confirmation"
    )

    args = parser.parse_args()

    base_dir = resolve_data_root(cli_override=args.data_dir)

    base_dir_exists = base_dir.exists()

    # Confirm data removal
    if (args.remove_data or args.remove_snapshots) and not args.force:
        print(
            f"\n⚠️  WARNING: You requested destructive removal (data and/or snapshots)."
        )
        response = input("Are you sure? (yes/no): ")
        if response.lower() != "yes":
            print("Aborted.")
            return 0

    print(f"\nCleaning up instances in {base_dir}...")

    try:
        import docker

        client = docker.from_env()
        docker_available = True
    except Exception:
        docker_available = False

    if docker_available:
        print("\nCleaning up Docker containers...")
        for container in client.containers.list(all=True):
            name = getattr(container, "name", "")
            if name.startswith("pbt-worker-") or name.startswith("eval-worker-"):
                print(f"  Stopping and removing container {name}...")
                try:
                    container.stop(timeout=5)
                    container.remove(force=True)
                except Exception as e:
                    logging.warning(f"Failed to remove {name}: {e}")
        print("✓ Docker containers cleaned")

        if args.remove_snapshots:
            print("\nCleaning up Docker snapshot images...")
            for image in client.images.list():
                for tag in image.tags:
                    if "pg-snapshot-baseline-" in tag:
                        print(f"  Removing image {tag}...")
                        try:
                            client.images.remove(tag, force=True)
                        except Exception as e:
                            logging.warning(f"Failed to remove image {tag}: {e}")
            print("✓ Docker snapshots cleaned")

    # Detect running bare-metal instances by checking data directories
    worker_dirs = sorted(base_dir.rglob("worker_*")) if base_dir_exists else []
    if worker_dirs:
        print(
            f"\nFound {len(worker_dirs)} instance directories to stop via base environment"
        )

        try:
            db_config = DatabaseConfig.from_env()
        except ValueError:
            db_config = DatabaseConfig(
                user="postgres",
                password="",
                host="127.0.0.1",
                port=5432,
                dbname="postgres",
            )

        manager = EnvironmentFactory.create(
            schema_provider=_NoopBenchmarkExecutor(),
            use_docker=False,
            db_config=db_config,
            base_dir=base_dir,
            base_port=5432,
            run_id="cleanup",
            container_prefix="cleanup-worker",
        )

        for worker_dir in worker_dirs:
            suffix = worker_dir.name.split("_", 1)[1]
            if suffix.isdigit():
                worker_id = int(suffix)
                manager.instances[worker_id] = InstanceConfig(
                    worker_id=worker_id,
                    port=5432 + worker_id,
                    data_dir=worker_dir,
                    running=True,
                )

        manager.stop_all(mode="immediate")
        print("✓ All bare-metal instances stopped")

    if args.remove_data:
        print("\nRemoving data directories...")
        # First clear out stuck instance folders in the system Trash to free up space
        if docker_available:
            _docker_cleanup_trash(base_dir=base_dir)
            print("✓ System Trash checked for stuck instance directories")

        if base_dir_exists:
            for worker_dir in worker_dirs:
                if worker_dir.is_dir():
                    if docker_available:
                        _docker_force_remove(worker_dir)
                    else:
                        shutil.rmtree(worker_dir, ignore_errors=True)
            print("✓ Data directories removed")
        else:
            print("✓ No local data directories found")
    else:
        print("\nData directories preserved (use --remove-data to delete)")

    if args.remove_snapshots:
        project_root = Path(__file__).resolve().parent.parent.parent
        snapshots_dir = project_root / ".snapshots"
        if snapshots_dir.exists():
            print("\nRemoving global snapshot manifests...")
            shutil.rmtree(snapshots_dir, ignore_errors=True)
            print("✓ Global snapshot manifests removed")

        base_snapshots_dir = base_dir / ".snapshots"
        if base_snapshots_dir.exists():
            print(
                f"\nRemoving host-level database snapshots at {base_snapshots_dir}..."
            )
            if docker_available:
                _docker_force_remove(base_snapshots_dir)
            else:
                shutil.rmtree(base_snapshots_dir, ignore_errors=True)
            print("✓ Host-level database snapshots removed")

    print("\n✓ Cleanup complete!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
