---
name: results-and-visualization
description: >
  Publication-quality visualization patterns using matplotlib for convergence curves,
  worker trajectories, performance breakdowns, population diversity plots, and BO
  comparison charts. Use this skill when creating visualizations, plots, figures for
  the paper, analyzing results JSON files, rendering multi-seed error bands, or
  designing any figure for academic publication or presentation.
---

# Results and Visualization

## Results JSON Schema

Every tuning session (PBT, BO, LHS) writes one session JSON under
`results/sessions/{workload}/{strategy}/{tier}/traces/`. The canonical reference is
`docs/reference/session-json-schema.md`; the abridged PBT shape is:

```json
{
  "tuning_session": { "tuning_strategy": "pbt", "workload_type": "oltp", ... },
  "best_configuration": { "score": 85.3, "knobs": { ... }, "metrics": { ... } },
  "history": [
    {
      "generation": 0,
      "best_score": 72.5,
      "mean_score": 45.3,
      "std_score": 12.1,
      "wall_clock_seconds": 287.3,
      "worker_scores": [
        { "worker_id": 0, "score": 72.5, "metrics": { ... }, "score_breakdown": { ... } }
      ],
      "worker_configs": [ { "worker_id": 0, "config": { ... } } ],
      "exploitations": [ ... ],
      "num_exploited": 1
    }
  ],
  "system_info": { ... }
}
```

The per-round key names differ by strategy (`generation` / `iteration` / `batch`,
and the matching `*_elapsed_seconds` / `*_overhead_seconds`). `mean_score` and
`std_score` exist only on PBT records.

### Parsing Pattern
```python
import json

from src.tuners.utils.session_schema import get_history, get_iteration_index

with open('results/path/to/trace.json') as f:
    data = json.load(f)

history = get_history(data)  # also reads legacy generation_history traces
rounds = [get_iteration_index(g) for g in history]
best_scores = [g['best_score'] for g in history]
mean_scores = [g.get('mean_score') for g in history]  # PBT only
std_scores = [g.get('std_score') for g in history]    # PBT only
worker_scores = [[w['score'] for w in g.get('worker_scores', [])] for g in history]
exploitations = [g.get('exploitations', []) for g in history]  # PBT only
```

## Approved Plot Types

### Plot 1: Convergence Curve
- Running-best score + per-generation best/mean ± std band
- Baseline horizontal line for reference
- Exploit-event markers (vertical dashed lines or scatter points)
- Data source: `history` → `best_score`, `mean_score`, `std_score`

### Plot 2: Wall-Clock Time Comparison (PBT vs BO)
- Score vs elapsed time (seconds)
- Shows PBT's parallelism advantage
- Both methods on same axes for direct comparison

### Plot 3: Sample Efficiency Comparison (PBT vs BO)
- Score vs total evaluations (not wall-clock)
- Shows BO's potential sample efficiency advantage
- Total evals = population_size × generations for PBT

### Plot 4: Per-Worker Trajectory
- Individual worker scores per generation (thin lines)
- Exploit-event markers showing when workers were replaced
- Best worker highlighted with thick line
- Data source: `worker_scores` in `history`

### Plot 5: Performance Improvement Breakdown
- Grouped bar chart: latency improvement, throughput improvement, memory savings
- Multi-seed error bars (mean ± std across 5 seeds)
- Relative to baseline (0% = default PG config)

### Plot 6: Population Diversity Over Time
- `std_score` plotted over generations
- Shows convergence behavior — diversity should decrease over time
- Sharp drops indicate successful exploit events

## Academic Figure Standards

```python
import matplotlib.pyplot as plt
import matplotlib as mpl

# Recommended style setup
plt.style.use('seaborn-v0_8-paper')
mpl.rcParams.update({
    'font.size': 10,
    'axes.labelsize': 12,
    'axes.titlesize': 14,
    'xtick.labelsize': 10,
    'ytick.labelsize': 10,
    'legend.fontsize': 10,
    'figure.dpi': 300,
    'savefig.dpi': 300,
    'savefig.bbox': 'tight',
})

# Use LaTeX labels if available
try:
    plt.rc('text', usetex=True)
    plt.rc('font', family='serif')
except:
    pass  # Fall back to matplotlib's mathtext
```

### Figure Sizing
| Layout | Width | Use Case |
|--------|-------|----------|
| Single-column | 3.5 in | Conference papers |
| Double-column | 7.0 in | Wide figures, comparison charts |
| Full-page | 7.0 × 9.0 in | Multi-panel figures |

### Color Palette
Use colorblind-friendly schemes:
```python
COLORS = {
    'pbt': '#2196F3',      # Blue
    'bo': '#FF9800',       # Orange
    'baseline': '#9E9E9E', # Gray
    'best': '#4CAF50',     # Green
    'exploit': '#F44336',  # Red (markers)
}
```

### Save Format
```python
fig.savefig('figure_1.pdf')   # Vector (for paper)
fig.savefig('figure_1.png')   # Raster (for preview)
```

## Multi-Seed Error Band Rendering

```python
import numpy as np

# all_seeds_scores: shape (n_seeds, n_generations)
mean_scores = np.mean(all_seeds_scores, axis=0)
std_scores = np.std(all_seeds_scores, axis=0)
generations = np.arange(1, len(mean_scores) + 1)

fig, ax = plt.subplots(figsize=(7, 4))
ax.fill_between(generations,
    mean_scores - std_scores,
    mean_scores + std_scores,
    alpha=0.3, color=COLORS['pbt'])
ax.plot(generations, mean_scores, linewidth=2,
    color=COLORS['pbt'], label='PBT (mean ± std)')
ax.axhline(y=baseline_score, color=COLORS['baseline'],
    linestyle='--', label='Default PG')
ax.set_xlabel('Generation')
ax.set_ylabel('Performance Score')
ax.legend()
```
