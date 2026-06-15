import argparse
import sys

from scripts.experiments.experiment_matrix import (
    build_all_experiments,
    get_experiment_by_id,
    get_experiments_by_tier,
)
from scripts.experiments.runner import MANIFEST_PATH, ExperimentRunner


def main():
    parser = argparse.ArgumentParser(description="Cloud Experiment Suite Runner")
    parser.add_argument("--tier", type=int, nargs="+", choices=[1, 2, 3], help="Run specific tiers")
    parser.add_argument("--experiment", type=str, help="Run a specific experiment by ID")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing")
    parser.add_argument("--no-push", action="store_true", help="Skip git commit/push to results repo")
    parser.add_argument("--resume", action="store_true", help="Resume from experiment_manifest.json (default behavior, skips done)")
    parser.add_argument("--retry-failed", action="store_true", help="Retry failed runs from manifest")
    parser.add_argument("--status", action="store_true", help="Print current status from manifest and exit")
    
    args = parser.parse_args()
    
    if args.status:
        if not MANIFEST_PATH.exists():
            print("No manifest found.")
            return
        import json
        manifest = json.loads(MANIFEST_PATH.read_text())
        print(f"Started at: {manifest.get('started_at')}")
        runs = manifest.get("runs", {})
        for key, data in sorted(runs.items()):
            print(f"{key:50} | {data.get('status', 'unknown'):10} | {data.get('duration_s', 0):.1f}s")
        return

    experiments_to_run = []
    
    if args.experiment:
        exp = get_experiment_by_id(args.experiment)
        if not exp:
            print(f"Experiment {args.experiment} not found.")
            sys.exit(1)
        experiments_to_run.append(exp)
    elif args.tier:
        for t in args.tier:
            experiments_to_run.extend(get_experiments_by_tier(t))
    else:
        # Default: run all
        experiments_to_run = build_all_experiments()
        
    runner = ExperimentRunner(dry_run=args.dry_run, no_push=args.no_push)
    
    for exp in experiments_to_run:
        runner.run_experiment(exp, retry_failed=args.retry_failed)

if __name__ == "__main__":
    main()
