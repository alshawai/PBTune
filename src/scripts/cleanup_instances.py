"""
Cleanup script for PostgreSQL instances

Stops all running instances and optionally removes data directories.
"""

import argparse
import logging
import shutil
import sys
from pathlib import Path

from src.config.database import DatabaseConfig
from src.benchmarks.executor import BenchmarkExecutor
from src.utils.environments import EnvironmentFactory, InstanceConfig
from src.utils.metrics import PerformanceMetrics

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


class _NoopBenchmarkExecutor(BenchmarkExecutor):
    """Minimal schema provider used for environment lifecycle-only cleanup."""

    def prepare(self, db_config: DatabaseConfig) -> None:
        return

    def validate(self, db_config: DatabaseConfig) -> bool:
        return True

    def execute(
        self,
        db_config: DatabaseConfig,
        worker_id: int | None = None,
        **kwargs: object,
    ) -> PerformanceMetrics:
        del db_config, worker_id, kwargs
        return PerformanceMetrics()

def main():
    parser = argparse.ArgumentParser(description='Cleanup PostgreSQL instances')
    parser.add_argument(
        '--remove-data',
        action='store_true',
        help='Remove data directories (WARNING: destroys all data)'
    )
    parser.add_argument(
        '--base-dir',
        type=str,
        default='./pg_instances',
        help='Base directory for instances (default: ./pg_instances)'
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Force removal without confirmation'
    )

    args = parser.parse_args()

    base_dir = Path(args.base_dir)

    if not base_dir.exists():
        print(f"No instances found at {base_dir}")
        return 0

    # Confirm data removal
    if args.remove_data and not args.force:
        print(f"\n⚠️  WARNING: This will PERMANENTLY DELETE all data in {base_dir}")
        response = input("Are you sure? (yes/no): ")
        if response.lower() != 'yes':
            print("Aborted.")
            return 0

    print(f"\nCleaning up instances in {base_dir}...")

    # Use environment factory so cleanup remains compatible with both backends.
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

    # Detect running instances by checking data directories
    worker_dirs = sorted(base_dir.glob('worker_*'))
    print(f"Found {len(worker_dirs)} instance directories")

    # Attempt to stop each one
    for worker_dir in worker_dirs:
        suffix = worker_dir.name.split('_', 1)[1]
        if not suffix.isdigit():
            logging.warning("Skipping unrecognized worker directory: %s", worker_dir.name)
            continue
        worker_id = int(suffix)
        instance_config = InstanceConfig(
            worker_id=worker_id,
            port=5432 + worker_id,
            data_dir=worker_dir,
            running=True,
        )
        manager.instances[worker_id] = instance_config

    # Stop all
    print("\nStopping all instances...")
    manager.stop_all(mode='immediate')
    print("✓ All instances stopped")

    # Remove data if requested
    if args.remove_data:
        print("\nRemoving data directories...")
        for worker_dir in worker_dirs:
            if worker_dir.is_dir():
                shutil.rmtree(worker_dir, ignore_errors=True)
        print("✓ Data directories removed")
    else:
        print("\nData directories preserved (use --remove-data to delete)")

    print("\n✓ Cleanup complete!")
    return 0

if __name__ == '__main__':
    sys.exit(main())
