---
name: matplotlib-publication-figures
description: Academic figure standards using matplotlib, color accessibility, DPI settings, LaTeX integration. Use when creating charts, plots, and figures for publication.
---

# Matplotlib Publication Figures

Create publication-ready, high-quality figures using `matplotlib`. Follow these guidelines.

## Sizing and Layout

- Determine the target column width of the academic template (e.g., ACM or IEEE two-column is usually ~3.3 to 3.5 inches per column).
- Set the figure size explicitly in inches to avoid scaling artifacts when inserted into the paper.
  ```python
  fig, ax = plt.subplots(figsize=(3.5, 2.5)) # Single column
  fig, ax = plt.subplots(figsize=(7.0, 3.0)) # Double column (span)
  ```
- Use `fig.tight_layout()` or `constrained_layout=True` to minimize whitespace and ensure labels aren't clipped.

## Typography and LaTeX Integration

- Fonts in the figure should ideally match the paper's font style and size (e.g., size 9 or 10).
- Avoid fonts that are too small to read when printed. Minimum font size is usually 8pt.
- Enable LaTeX text rendering if equations or specific fonts are needed:
  ```python
  plt.rcParams.update({
      "text.usetex": True,
      "font.family": "serif",
      "font.serif": ["Times", "Palatino", "Computer Modern Roman"]
  })
  ```

## Colors and Accessibility

- **Never rely on color alone** to distinguish data. Use varying line styles (`-`, `--`, `-.`, `:`) or markers (`o`, `s`, `^`, `x`).
- Use colorblind-friendly palettes. Consider palettes like `viridis`, `plasma`, `cividis`, or specific categorical palettes designed for accessibility (e.g., seaborn's `colorblind` palette).
- Ensure high contrast between text/lines and the background.

## Styling Elements

- Include a clear, descriptive legend. For complex charts, place the legend outside the plot box to avoid overlapping data:
  ```python
  ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.15), ncol=3)
  ```
- Label all axes with both the metric and its unit (e.g., `Throughput (Tx/sec)`).
- Turn on appropriate gridlines (`ax.grid(True, linestyle='--', alpha=0.6)`) to help the reader estimate values, but ensure they don't overpower the data series.
- Remove top and right spines if they are not necessary to reduce chart junk:
  ```python
  ax.spines['top'].set_visible(False)
  ax.spines['right'].set_visible(False)
  ```

## Exporting for Publication

- Export vector graphics for plots (PDF or EPS) so they scale infinitely without pixelation.
- If raster images must be used (e.g., for massive scatter scale or heatmaps), use a high DPI:
  ```python
  plt.savefig("figure.pdf", format="pdf", bbox_inches="tight")
  plt.savefig("retina_figure.png", dpi=300, bbox_inches="tight")
  ```
