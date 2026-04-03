---
description: Full experiment lifecycle from design through execution to analysis. Covers setup, tracking, benchmarking fairness, statistical analysis, and reproducibility. Use when planning, running, or analyzing a scientific or ML experiment.
---

# /experiment-cycle — Full Experiment Lifecycle

**Skills used:** `experiment-tracking`, `academic-benchmarking`, `statistical-analysis`, `reproducibility-checklist`, `python-project-patterns`, `data-pipeline-patterns`

This workflow has three modes. Ask the user which phase they're in, or infer from context.

---

## Phase A: Design & Setup

1. **Define the hypothesis.** Ask the user: What are you testing? What is the expected outcome? What metric will you measure?

2. **Design for fairness.** Read the `academic-benchmarking` skill. Ensure:
   - The comparison methodology is fair (same budget, same evaluation pipeline, same hardware)
   - A trivial baseline is included (random, default, etc.)
   - State-of-the-art baselines are identified (or justified as absent)

3. **Set up tracking infrastructure.** Read the `experiment-tracking` skill. Create the directory structure:
   ```
   results/{experiment_name}_{timestamp}/
   ├── metadata.json       # Hardware, OS, git hash, arguments
   ├── config/             # Exact configs being tested
   ├── logs/               # stdout/stderr
   └── data/               # Raw metrics
   ```

4. **Pre-flight reproducibility check.** Read the `reproducibility-checklist` skill. Verify before running:
   - [ ] All hyperparameters documented
   - [ ] Evaluation metrics mathematically defined
   - [ ] Random seeds explicitly set (suggest: `[42, 123, 456, 789, 1024]`)
   - [ ] Dependencies locked (`requirements.txt` or `pyproject.toml`)
   - [ ] Hardware info capture script ready

5. **Write the runner script.** Read the `python-project-patterns` skill. The script should:
   - Accept CLI arguments for seeds, configs, and output directory
   - Capture environment metadata automatically (git hash, hardware, OS)
   - Handle failures gracefully (log and continue, don't crash the campaign)
   - Write results as structured JSON/CSV

---

## Phase B: Execution & Monitoring

1. **Launch the campaign.** Run the experiment across all seeds and configurations. Log each run's status.

2. **Monitor progress.** Periodically check for:
   - Failed runs (mark as failed with reason, don't delete)
   - Anomalous results (values outside expected range)
   - Resource issues (disk space, memory)

3. **Validate output.** Read the `data-pipeline-patterns` skill. After each run completes:
   - Validate the output schema matches the expected structure
   - Check for null/missing values in critical metrics
   - Verify row counts match expectations

---

## Phase C: Analysis & Reporting

1. **Aggregate results.** Read the `data-pipeline-patterns` skill. Load all result files, validate schemas, and merge into a single analysis DataFrame.

2. **Statistical analysis.** Read the `statistical-analysis` skill:
   - Check distribution assumptions (Shapiro-Wilk for normality)
   - Select the appropriate test (parametric vs non-parametric)
   - Run significance tests between methods
   - Apply multiple comparison correction if testing 3+ methods
   - Calculate effect sizes (Cohen's d or Cliff's delta)
   - Report: mean ± std, p-values, effect sizes, confidence intervals

3. **Generate summary table.** Format results as a publication-ready table:
   ```
   | Method   | Metric (mean ± std) | vs Baseline p-value | Effect Size |
   |----------|--------------------|--------------------|-------------|
   | Baseline | X.XX ± X.XX        | —                  | —           |
   | Method A | X.XX ± X.XX        | 0.XXX              | X.XX (large)|
   ```

4. **Archive experiment.** Record what was tried, what happened, and what was learned. Tag the experiment as `exploratory`, `confirmatory`, or `failed`.
