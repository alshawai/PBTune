import argparse
import json
import sys
from pathlib import Path

from scripts.experiments.experiment_matrix import (
    build_all_experiments,
    build_smoke_experiments,
    get_experiment_by_id,
    get_experiments_by_tier,
)
from scripts.experiments.runner import (
    DEFAULT_MANIFEST_DIR,
    LEGACY_MANIFEST_PATH,
    ExperimentRunner,
)


def _print_aggregate_status(manifest_dir: Path, manifest_path: Path | None) -> None:
    """Print run statuses across one or many manifest files."""
    paths: list[Path] = []
    if manifest_path is not None:
        paths.append(manifest_path)
    else:
        if LEGACY_MANIFEST_PATH.exists():
            paths.append(LEGACY_MANIFEST_PATH)
        if manifest_dir.exists():
            paths.extend(sorted(manifest_dir.glob("*.json")))

    if not paths:
        print("No manifest found.")
        return

    rows: list[tuple[str, str, str, float]] = []
    for path in paths:
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            print(f"# warning: could not read {path}: {exc}")
            continue
        started = data.get("started_at")
        if started:
            print(f"Started ({path.name}): {started}")
        for key, run_data in data.get("runs", {}).items():
            rows.append(
                (
                    path.name,
                    key,
                    run_data.get("status", "unknown"),
                    float(run_data.get("duration_s", 0) or 0),
                )
            )

    for manifest_name, key, status, duration in sorted(rows):
        print(f"{manifest_name:30} | {key:50} | {status:10} | {duration:.1f}s")


def main():
    parser = argparse.ArgumentParser(description="Cloud Experiment Suite Runner")
    parser.add_argument("--tier", type=int, nargs="+", choices=[1, 2, 3], help="Run specific tiers")
    parser.add_argument("--experiment", type=str, help="Run a specific experiment by ID")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help=(
            "Run the minimal end-to-end smoke suite (rapid PBT+BO at 1 "
            "generation + eval) for both benchmark families, committing/"
            "pushing like any other run. Use before a real cloud campaign."
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing")
    parser.add_argument("--no-push", action="store_true", help="Skip git commit/push to results repo")
    parser.add_argument("--resume", action="store_true", help="Resume from manifest (default behavior, skips done)")
    parser.add_argument("--retry-failed", action="store_true", help="Retry failed runs from manifest")
    parser.add_argument("--status", action="store_true", help="Print aggregate status across all manifests and exit")
    parser.add_argument(
        "--manifest-dir",
        type=Path,
        default=None,
        help=(
            "Directory holding per-experiment manifests "
            f"(default: {DEFAULT_MANIFEST_DIR})"
        ),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help=(
            "Explicit single manifest file path. When set, every "
            "experiment in this invocation shares this file. Defaults "
            "to per-experiment files derived from --manifest-dir."
        ),
    )

    args = parser.parse_args()
    manifest_dir = args.manifest_dir or DEFAULT_MANIFEST_DIR

    if args.status:
        _print_aggregate_status(manifest_dir, args.manifest)
        return

    experiments_to_run = []

    if args.smoke:
        experiments_to_run = build_smoke_experiments()
    elif args.experiment:
        exp = get_experiment_by_id(args.experiment)
        if not exp:
            print(f"Experiment {args.experiment} not found.")
            sys.exit(1)
        experiments_to_run.append(exp)
    elif args.tier:
        for t in args.tier:
            experiments_to_run.extend(get_experiments_by_tier(t))
    else:
        experiments_to_run = build_all_experiments()

    runner = ExperimentRunner(
        dry_run=args.dry_run,
        no_push=args.no_push,
        manifest_dir=manifest_dir,
        manifest_path=args.manifest,
    )

    with runner.cpu_performance_session():
        for exp in experiments_to_run:
            runner.run_experiment(exp, retry_failed=args.retry_failed)


if __name__ == "__main__":
    main()
