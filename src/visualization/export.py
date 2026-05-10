"""
Multi-format figure export with metadata sidecar generation.
"""

import json
import datetime
from pathlib import Path
from typing import Optional

from matplotlib.figure import Figure

from src.visualization.types import ExportFormat
from src.utils.logger import get_logger

LOGGER = get_logger("Export")


def _get_git_commit() -> Optional[str]:
    """Attempt to get the current git commit hash."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


def export_figure(
    fig: Figure,
    output_dir: Path | str,
    fig_id: str,
    formats: list[ExportFormat] | None = None,
    dpi: int = 300,
    metadata: dict | None = None,
) -> list[Path]:
    """
    Save a figure in multiple formats along with a metadata sidecar file.

    Args:
        fig: The matplotlib Figure to save.
        output_dir: Directory to save the outputs.
        fig_id: Base name for the saved files.
        formats: List of formats to export (defaults to PDF and PNG).
        dpi: Resolution for raster formats.
        metadata: Additional metadata to include in the sidecar JSON.

    Returns:
        List of paths to the successfully saved files (including the sidecar).
    """
    if formats is None:
        formats = [ExportFormat.PDF, ExportFormat.PNG]

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = []

    # Save image formats
    for fmt in formats:
        try:
            out_path = out_dir / f"{fig_id}.{fmt.value}"
            # Pass dpi for raster formats, vector formats will ignore it
            # bbox_inches="tight" ensures no clipping
            fig.savefig(out_path, dpi=dpi, bbox_inches="tight")
            saved_paths.append(out_path)
            LOGGER.debug("Saved figure to %s", out_path)
        except Exception as e:
            LOGGER.error("Failed to save %s as %s: %s", fig_id, fmt.value, e)

    # Generate metadata sidecar
    try:
        meta_path = out_dir / f"{fig_id}.meta.json"

        base_meta = {
            "fig_id": fig_id,
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "formats_exported": [f.value for f in formats],
            "git_commit": _get_git_commit(),
            "figure_size_inches": list(fig.get_size_inches()),
        }

        if metadata:
            base_meta.update(metadata)

        with open(meta_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(base_meta, indent=2))

        saved_paths.append(meta_path)
        LOGGER.debug("Saved metadata to %s", meta_path)
    except Exception as e:
        LOGGER.error("Failed to save metadata for %s: %s", fig_id, e)

    return saved_paths
