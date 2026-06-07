# Visualization Framework

> Last reviewed: 2026-06-07

See also: [Documentation index](../README.md), [knob-importance-analysis](knob-importance-analysis.md), [evaluation-suite](evaluation-suite.md), [guides/visualization](../guides/visualization.md)

## Overview

The `src/visualization/` package generates publication-quality figures from PBT session and analysis artefacts. This document covers **why the framework is shaped the way it is** — the design decisions behind the registry, theme, and loader/renderer split. For the API surface, the available figures, and how to author a new one, read [guides/visualization](../guides/visualization.md).

## Design decisions

### 1. Auto-discovery via package import

Plot modules register themselves at import; the registry never holds a hard list of figures. Adding a new figure is one new file under `src/visualization/plots/`, and it appears in `--list` automatically.

This keeps the CLI flat — there is no `--config figures.yaml`, no separate registration step, no central catalog file to keep in sync. It also makes it trivial to develop a single figure in isolation: import the module, and only that module's figures register.

The cost is that import-time side effects can hide (a typo in a plot module silently fails to register itself). Mitigation: the auto-discovery walker logs a warning when a module under `src/visualization/plots/` imports cleanly but doesn't call `REGISTRY.register()`.

### 2. Loader / renderer separation

A loader returns a typed dataclass; a renderer consumes it. Both halves have one responsibility each:

- The loader walks a results subtree and produces a typed view of the data. It can be unit-tested with a JSON fixture without any matplotlib imports.
- The renderer takes the typed view, the active theme, and an output path, and produces files. It can be unit-tested with a constructed dataclass instance, without any filesystem-dependent JSON parsing.

The contrast is with monolithic plot scripts (one function reads JSON and renders matplotlib in the same call) — those are nearly impossible to test, because the test setup either fakes matplotlib or fakes JSON parsing or both. The split makes both halves cheap to test independently.

### 3. Theme owns sizing, not the renderer

Renderers ask the theme for a `FigureSize` enum value (`SINGLE_COL`, `DOUBLE_COL`, `SQUARE`, `WIDE_SHORT`); the theme converts that to inches given the active venue. A figure rendered with `--venue pvldb` and `FigureSize.SINGLE_COL` is 3.33 inches wide; the same figure rendered with `--venue springer` is 3.39 inches wide. Renderers never see the inch values.

This is what makes the figure set retargetable across venues. The earlier, pre-framework one-off plot scripts had inch values hard-coded inside each script — switching venues required editing every file. With the theme as the single source of truth, the same figure module produces venue-correct output for any registered venue.

### 4. Colorblind-friendly palette by default

The palette in [colors.py](../../src/visualization/colors.py) is ordered so the first 4–5 series are distinguishable both in colour and in monochrome print. The default applies before any renderer code runs.

Renderers that need more series than the palette supports must also set distinct line styles or markers — colour alone is not sufficient for accessibility. The palette helper is `theme.colorblind_palette()`; renderers should call it rather than hard-coding hex values.

### 5. Independent of `src/tuner/`

The visualization package never imports from the tuning engine. The dependency graph runs `tuner` → result JSON → `visualization`, never directly. Two payoffs:

- The package can run in CI as a self-contained job against checked-in result fixtures, without bringing up PostgreSQL or Docker.
- A breaking change in the tuner's internal types is invisible to visualization — the contract between them is the JSON schema in [reference/session-json-schema](../reference/session-json-schema.md), not the Python types.

### 6. Output formats per figure

`FigureSpec.formats` is a list — typically `[PDF, PNG]`. PDFs go into papers; PNGs go into slide decks and previews. The registry honours each figure's preferred formats by default; the CLI's `--format` flag can override.

The split between vector and raster is intentional: line plots and bar charts go to vector PDFs (small file size, infinite resolution); densely-sampled heatmaps and SHAP dependence plots use raster PNGs at 300 DPI (avoids huge PDF files with millions of vector points). Each plot module decides which is appropriate for its content.

## Related documentation

- **[guides/visualization](../guides/visualization.md)** — the API and the recipe for adding a figure.
- **[knob-importance-analysis](knob-importance-analysis.md)** — feeds the importance, dependence, and interaction plots.
- **[evaluation-suite](evaluation-suite.md)** — produces the comparison JSON consumed by the comparison loader.
- **[reference/session-json-schema](../reference/session-json-schema.md)** — schema of the JSON the loaders parse.
