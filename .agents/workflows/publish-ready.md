---
description: Pre-publication checklist combining reproducibility verification, figure quality, code cleanup, and documentation standards. Use before submitting a paper, releasing a repository, or archiving research artifacts.
---

# /publish-ready — Pre-Publication Checklist

**Skills used:** `reproducibility-checklist`, `academic-benchmarking`, `matplotlib-publication-figures`, `documentation-as-code`, `academic-paper-writing`, `python-project-patterns`

## Steps

1. **Paper completeness.** Read the `academic-paper-writing` skill. Verify:
   - [ ] Abstract is self-contained (no undefined acronyms, no citations)
   - [ ] All sections present (Introduction, Background, Methodology, Evaluation, Conclusion)
   - [ ] Every figure and table is referenced in the text
   - [ ] Every `\label` has at least one `\ref`
   - [ ] Contributions listed in Introduction match what Evaluation demonstrates
   - [ ] Related work is thematic synthesis, not a paper list

2. **Benchmark integrity.** Read the `academic-benchmarking` skill. Verify:
   - [ ] Trivial baseline included (random, default, no-op)
   - [ ] All methods compared under equal conditions (same budget, pipeline, hardware)
   - [ ] Baselines re-run on your hardware (or explicitly labeled as "reported" numbers)
   - [ ] Hyperparameters tuned with equal effort across all methods
   - [ ] Results reported on ALL benchmarks run (no cherry-picking)
   - [ ] Ablation study included if the method has multiple components

3. **Statistical rigor.** Read the `statistical-analysis` skill (reference only). Verify:
   - [ ] Multiple seeds used (minimum 5)
   - [ ] Mean ± std reported (not single-run numbers)
   - [ ] Significance tests applied with appropriate method
   - [ ] Multiple comparison correction applied if 3+ methods
   - [ ] Effect sizes reported alongside p-values
   - [ ] Confidence intervals shown where applicable

4. **Figure quality.** Read the `matplotlib-publication-figures` skill. For every figure:
   - [ ] Size matches venue column width (not stretched/squashed)
   - [ ] Fonts are legible at print size (minimum 8pt)
   - [ ] Colors are colorblind-friendly (or line styles/markers distinguish series)
   - [ ] Axes labeled with metric name AND unit
   - [ ] Exported as vector (PDF/EPS) for line plots, 300 DPI PNG for raster
   - [ ] Error bars or shaded confidence bands visible

5. **Code quality.** Read the `python-project-patterns` skill. Verify:
   - [ ] No hardcoded paths or credentials
   - [ ] Type hints on public APIs
   - [ ] Dependencies locked with exact versions
   - [ ] Entry-point script documented in README

6. **Reproducibility.** Read the `reproducibility-checklist` skill. The full Pineau-style checklist:
   - [ ] All hyperparameters documented (including non-tuned ones)
   - [ ] Evaluation metrics mathematically defined
   - [ ] Hardware specs included (CPU, RAM, GPU, disk)
   - [ ] Software environment listed (OS, runtime versions, framework versions)
   - [ ] Random seeds reported
   - [ ] Total compute budget reported
   - [ ] Locked dependencies provided (requirements.txt / Dockerfile)
   - [ ] Single-command reproduction script exists

7. **Documentation.** Read the `documentation-as-code` skill. Verify:
   - [ ] README with installation, quick-start, and reproduction instructions
   - [ ] ADRs for major design decisions
   - [ ] Results directory structure documented
   - [ ] License file present

8. **Archive.** Prepare artifacts for persistent archival:
   - Tag the repository (e.g., `v1.0-paper-submission`)
   - Upload to Zenodo/Figshare for a DOI
   - Include the paper PDF, code, data, and trained models/configs
