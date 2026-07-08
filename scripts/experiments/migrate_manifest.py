"""One-shot helper: split the legacy single experiment_manifest.json into
per-experiment manifest files under results/manifests/.

Run once after upgrading to the per-experiment manifest scheme. Safe to
re-run: it never overwrites a per-experiment manifest that already
contains the same key (peer machines may have written newer state).

Usage
-----
    python -m scripts.experiments.migrate_manifest
    python -m scripts.experiments.migrate_manifest --dry-run
    python -m scripts.experiments.migrate_manifest --delete-legacy

The legacy file is left in place by default; pass --delete-legacy after
confirming the per-experiment files are correct and pushed.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from scripts.experiments.runner import (
    DEFAULT_MANIFEST_DIR,
    LEGACY_MANIFEST_PATH,
    _empty_manifest,
)

LOGGER = logging.getLogger("MigrateManifest")


def _experiment_id_from_key(key: str) -> str | None:
    """Extract the experiment id from a manifest run key.

    Run keys are formatted as ``<exp_id>/seed_<n>/<phase>``. Returns
    ``None`` for malformed keys so the caller can skip them rather
    than abort the whole migration.
    """
    head, _, _ = key.partition("/")
    return head or None


def split_manifest(
    legacy_path: Path,
    manifest_dir: Path,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """Split ``legacy_path`` into per-experiment manifests under
    ``manifest_dir``. Returns a count of runs written per experiment.

    Existing per-experiment manifests are preserved: only run keys
    missing from them are added. This way a re-run after partial
    progress doesn't clobber state a peer machine wrote first.
    """
    if not legacy_path.exists():
        LOGGER.info("No legacy manifest at %s — nothing to migrate.", legacy_path)
        return {}

    legacy = json.loads(legacy_path.read_text())
    legacy_started = legacy.get("started_at")
    runs = legacy.get("runs", {})

    grouped: dict[str, dict] = {}
    skipped_keys: list[str] = []
    for key, run_data in runs.items():
        exp_id = _experiment_id_from_key(key)
        if exp_id is None:
            skipped_keys.append(key)
            continue
        grouped.setdefault(exp_id, {})[key] = run_data

    if skipped_keys:
        LOGGER.warning(
            "Skipping %d malformed key(s): %s",
            len(skipped_keys),
            ", ".join(skipped_keys[:5]),
        )

    counts: dict[str, int] = {}
    for exp_id, exp_runs in grouped.items():
        target = manifest_dir / f"{exp_id}.json"
        if target.exists():
            existing = json.loads(target.read_text())
        else:
            existing = _empty_manifest()
            if legacy_started:
                existing["started_at"] = legacy_started

        added = 0
        for key, run_data in exp_runs.items():
            if key not in existing["runs"]:
                existing["runs"][key] = run_data
                added += 1

        counts[exp_id] = added
        if dry_run:
            LOGGER.info(
                "DRY RUN: would write %s (+%d runs, total %d)",
                target,
                added,
                len(existing["runs"]),
            )
            continue

        if added == 0 and target.exists():
            LOGGER.info("%s: no new runs to add (already up to date)", target.name)
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(existing, indent=2))
        LOGGER.info(
            "Wrote %s (+%d runs, total %d)",
            target,
            added,
            len(existing["runs"]),
        )

    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--manifest-dir",
        type=Path,
        default=DEFAULT_MANIFEST_DIR,
        help=f"Target directory (default: {DEFAULT_MANIFEST_DIR})",
    )
    parser.add_argument(
        "--legacy-path",
        type=Path,
        default=LEGACY_MANIFEST_PATH,
        help=f"Source file (default: {LEGACY_MANIFEST_PATH})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print intended writes without modifying files.",
    )
    parser.add_argument(
        "--delete-legacy",
        action="store_true",
        help=(
            "Delete the legacy file after splitting. Only use after "
            "verifying the per-experiment files locally and pushing."
        ),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    counts = split_manifest(
        args.legacy_path,
        args.manifest_dir,
        dry_run=args.dry_run,
    )

    if not counts:
        return

    LOGGER.info(
        "Migration summary: %d experiments, %d runs added across all files",
        len(counts),
        sum(counts.values()),
    )

    if args.delete_legacy and not args.dry_run:
        args.legacy_path.unlink()
        LOGGER.info("Deleted legacy manifest %s", args.legacy_path)


if __name__ == "__main__":
    main()
