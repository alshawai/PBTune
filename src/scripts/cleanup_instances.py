"""
Cleanup script for PostgreSQL instances

Stops all running instances and optionally removes data directories.
"""

import argparse
import logging
import sys
from pathlib import Path

from src.tuner.utils import PostgresInstanceManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

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

    # Create manager (will find existing instances)
    manager = PostgresInstanceManager(
        base_dir=base_dir,
        base_port=5432
    )

    # Detect running instances by checking data directories
    worker_dirs = sorted(base_dir.glob('worker_*'))
    print(f"Found {len(worker_dirs)} instance directories")

    # Attempt to stop each one
    for worker_dir in worker_dirs:
        worker_id = int(worker_dir.name.split('_')[1])
        instance_config = type('Config', (), {
            'worker_id': worker_id,
            'port': 5432 + worker_id,
            'data_dir': worker_dir,
            'running': True
        })()
        manager.instances[worker_id] = instance_config

    # Stop all
    print("\nStopping all instances...")
    manager.stop_all(mode='immediate')
    print("✓ All instances stopped")

    # Remove data if requested
    if args.remove_data:
        print("\nRemoving data directories...")
        manager.cleanup(remove_data=True)
        print("✓ Data directories removed")
    else:
        print("\nData directories preserved (use --remove-data to delete)")

    print("\n✓ Cleanup complete!")
    return 0

if __name__ == '__main__':
    sys.exit(main())
