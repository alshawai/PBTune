# Visualization

> Last reviewed: 2026-06-07

See also: [Documentation Index](../README.md), [Knob Importance Analysis](../architecture/knob-importance-analysis.md), [PBT vs BO Comparison](pbt-vs-bo-comparison.md)

## Overview

The `src/visualization/` package generates publication-quality figures from PBT session and analysis artifacts. It is a self-contained pipeline that loads JSON results, applies a venue-specific matplotlib theme, and renders figures registered through a central registry.

The package is invoked via the CLI `python -m src.visualization` and is the canonical way to produce the paper figures from a results tree on disk. It is independent of the tuning loop — it never connects to PostgreSQL, never imports from `src/tuners/`, and runs offline against saved JSON.

```text
results/                              src/visualization/
├── oltp/{workload}/                       │
│   └── pbt_runs/{tier}/                   │ loaders/
│       └── tuning_sessions/         ────► │   session.py
│           pbt_results_*.json             │   baseline.py
├── olap/                            ────► │   comparison.py
│   └── comparisons/{tier}/                │   ablation.py
│       comparison_*.json                  │   importance.py
├── analysis/{workload}/             ────► │   multi_seed.py
│   importance_results.json                │
└── ...                                    │ plots/
                                           │   knob_importance.py
                                           │   knob_dependence.py
                                           │   knob_interaction_heatmap.py
                                           │
                                           │ registry.py  ◄── @register_figure
                                           │ theme.py     ◄── PBTuneTheme + VenuePreset
                                           │ export.py
                                           │
                                           ▼
                                       figures/{fig_id}.{pdf|png|svg}
```

---

## Table of Contents

1. [CLI](#cli)
2. [The figure registry](#the-figure-registry)
3. [Theme engine](#theme-engine)
4. [Loaders](#loaders)
5. [Built-in plots](#built-in-plots)
6. [Adding a new figure](#adding-a-new-figure)
7. [Related documentation](#related-documentation)

For the design rationale behind the framework (auto-discovery, loader/renderer separation, why the theme owns sizing), see [architecture/visualization](../architecture/visualization.md).

---

## CLI

```bash
# List every registered figure
python -m src.visualization --list

# Generate one figure by ID
python -m src.visualization --figure knob_importance

# Generate all figures in a category
python -m src.visualization --category importance

# Pick a venue preset (sizing + typography)
python -m src.visualization --venue pvldb           # default
python -m src.visualization --venue springer
python -m src.visualization --venue preview         # bigger, on-screen review

# Point at a non-default results tree
python -m src.visualization --data-dir results/ --output-dir figures/
```

Available venues: `pvldb` (PVLDB / VLDB single+double column widths, 9 pt serif, LaTeX), `springer` (Springer LNCS, 10 pt serif), `preview` (larger sans-serif for on-screen review). The widths and typography are encoded as `VenuePreset` records in [theme.py](../../src/visualization/theme.py).

The CLI auto-discovers plot modules: importing `src.visualization.plots` triggers each module's top-level `register_figure(...)` call. Adding a new module under `src/visualization/plots/` is enough to make it visible to `--list`.

---

## The figure registry

**Location**: [src/visualization/registry.py](../../src/visualization/registry.py)

The registry tracks every figure by a stable string ID along with its category, paper section, default loader, and renderer.

```python
@dataclass
class FigureSpec:
    fig_id: str
    title: str
    category: str
    section: str
    loader: Callable[[Path], Any]
    renderer: Callable[[Theme, Any, OutputDir], Path]
    formats: list[ExportFormat]
    venue_overrides: Optional[dict[str, Any]] = None
```

A plot module registers itself at import time:

```python
from src.visualization.registry import REGISTRY
from src.visualization.types import FigureSpec, ExportFormat

REGISTRY.register(FigureSpec(
    fig_id="knob_importance",
    title="Per-knob fANOVA + TreeSHAP importance",
    category="importance",
    section="results",
    loader=load_importance_results,
    renderer=render_knob_importance,
    formats=[ExportFormat.PDF, ExportFormat.PNG],
))
```

Public registry methods:

| Method | Purpose |
| --- | --- |
| `register(spec)` | Add a figure (warns on overwrite). |
| `get(fig_id)` | Retrieve by ID, raises `FigureRegistryError` if not found. |
| `list_all()` / `list_by_category(c)` / `list_by_section(s)` | Enumerate. |
| `_discover_plots()` | Walk `src.visualization.plots` and import every module. Triggered automatically by the CLI. |

Auto-discovery means contributors do not edit the registry directly — they write a module under `src/visualization/plots/` and the registration happens at import time.

---

## Theme engine

**Location**: [src/visualization/theme.py](../../src/visualization/theme.py)

`PBTuneTheme` enforces consistent matplotlib styling across every figure for a chosen venue.

```python
@dataclass
class VenuePreset:
    name: str
    single_col_width_in: float
    double_col_width_in: float
    base_font_size_pt: int
    font_family: str
    use_latex: bool

class PBTuneTheme:
    VENUE_PRESETS: dict[str, VenuePreset] = {
        "pvldb":    VenuePreset(name="pvldb",    single_col_width_in=3.33, ...),
        "springer": VenuePreset(name="springer", single_col_width_in=3.39, ...),
        "preview":  VenuePreset(name="preview",  ...),
    }

    def figure(self, size: FigureSize, **kwargs) -> Figure: ...
    def style_axes(self, ax: Axes, ...) -> None: ...
    def colorblind_palette(self) -> list[str]: ...

    @contextmanager
    def temporary_overrides(self, **rcparams) -> Iterator[None]: ...
```

The theme owns:

- **Figure sizing** — single-column / double-column widths are venue-specific and the renderer must request a `FigureSize` (one of `SINGLE_COL`, `DOUBLE_COL`, `SQUARE`, `WIDE_SHORT`).
- **Typography** — base font size + family. With `use_latex=True` the theme switches to LaTeX rendering for all text; the resulting PDFs embed Type 1 fonts compatible with PVLDB / Springer requirements.
- **Color palette** — the colorblind-friendly palette from [src/visualization/colors.py](../../src/visualization/colors.py). Renderers should use `theme.colorblind_palette()` rather than picking colors directly.
- **Axes styling** — uniform tick formatting, spine styles, grid behaviour.

Renderers should never mutate `plt.rcParams` directly. `theme.temporary_overrides(...)` is the escape hatch for one-off tweaks.

---

## Loaders

**Location**: [src/visualization/loaders/](../../src/visualization/loaders/)

Each loader knows how to walk a results subtree and build a typed dataclass for the renderer to consume.

| Loader | Reads | Produces |
| --- | --- | --- |
| `session.py` | `results/{workload}/pbt_runs/{tier}/tuning_sessions/pbt_results_*.json` | `TuningSession` (per-generation history, best config, score breakdown, metadata) |
| `baseline.py` | Default-PostgreSQL baseline JSONs under `results/{workload}/baselines/` | Baseline metric distributions |
| `comparison.py` | `results/{workload}/comparisons/{tier}/comparison_*.json` | `ComparisonReport` from the post-hoc evaluation suite (see [EVALUATION_SUITE.md](../architecture/evaluation-suite.md)) |
| `ablation.py` | Multi-config sweeps for ablation tables | Per-condition metric records |
| `importance.py` | `results/analysis/{workload}/importance_results.json` | fANOVA + TreeSHAP per-knob importance + pairwise interactions |
| `multi_seed.py` | Multiple seed-tagged session JSONs | Per-seed convergence curves with mean / std bands |

Loaders are deliberately schema-tolerant: they accept the current session JSON layout and the legacy `fixed_v1` layout from older runs. Version migration goes through the same scoring-policy compatibility branch as the post-hoc evaluation suite, so a figure rendered today can include data from a session recorded months ago.

---

## Built-in plots

**Location**: [src/visualization/plots/](../../src/visualization/plots/)

| Plot module | `fig_id` | What it shows |
| --- | --- | --- |
| `knob_importance.py` | `knob_importance` | Per-knob fANOVA / SHAP importance bar chart with rank-correlation diagnostic. |
| `knob_dependence.py` | `knob_dependence` | SHAP dependence plots for the top-K knobs, with hardware-feature coloring. |
| `knob_interaction_heatmap.py` | `knob_interaction_heatmap` | Pairwise interaction heatmap from fANOVA second-order terms. |

Convergence and Pareto plots for the PBT-vs-BO comparison are produced by [`src/scripts/pbt_vs_bo_comarison.py`](../../src/scripts/pbt_vs_bo_comarison.py) — see [PBT_VS_BO_COMPARISON.md](pbt-vs-bo-comparison.md). They predate the registry and are kept in `src/scripts/` for now; migrating them into the registry is tracked as a follow-up.

---

## Adding a new figure

1. **Create a loader** under `src/visualization/loaders/`. Return a dataclass; do not pass raw dicts into renderers.
2. **Create a plot module** under `src/visualization/plots/`. The module must:
   - import `REGISTRY` and `FigureSpec`,
   - define a `render_<your_figure>(theme, data, output_dir) -> Path` function that uses `theme.figure(size=...)` and `theme.style_axes(...)`,
   - call `REGISTRY.register(FigureSpec(...))` at module top level.
3. Run `python -m src.visualization --list` to confirm registration.
4. Run `python -m src.visualization --figure <your_id> --venue preview` to iterate quickly without LaTeX.

Use the existing `knob_importance` module as a template — it covers the loader → theme → render → export round-trip in a small file.

---

## Related documentation

- **[architecture/visualization](../architecture/visualization.md)** — design rationale for the framework.
- **[Knob Importance Analysis](../architecture/knob-importance-analysis.md)** — what the importance plots draw from.
- **[PBT vs BO Comparison](pbt-vs-bo-comparison.md)** — convergence / Pareto / resource-efficiency PDFs from the comparison script.
- **[Evaluation Runbook](evaluation-runbook.md)** — generating the comparison JSONs that feed the comparison loader.

### File locations

- CLI entry: [src/visualization/__main__.py](../../src/visualization/__main__.py)
- Registry: [src/visualization/registry.py](../../src/visualization/registry.py)
- Theme: [src/visualization/theme.py](../../src/visualization/theme.py)
- Types (`FigureSpec`, `VenuePreset`, `ExportFormat`, `FigureSize`): [src/visualization/types.py](../../src/visualization/types.py)
- Colors: [src/visualization/colors.py](../../src/visualization/colors.py)
- Export helpers: [src/visualization/export.py](../../src/visualization/export.py)
- Loaders: [src/visualization/loaders/](../../src/visualization/loaders/)
- Plots: [src/visualization/plots/](../../src/visualization/plots/)
- Tests: [tests/unit/visualization/test_types.py](../../tests/unit/visualization/test_types.py)
