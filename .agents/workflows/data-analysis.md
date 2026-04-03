---
description: End-to-end data analysis pipeline from raw data through modeling to publication-ready figures. Use when analyzing experimental results, building predictive models, or creating visualizations from data.
---

# /data-analysis — Data Analysis Pipeline

**Skills used:** `data-pipeline-patterns`, `sklearn-model-analysis`, `statistical-analysis`, `matplotlib-publication-figures`, `python-project-patterns`

## Steps

1. **Understand the data.** Ask the user:
   - What data files are we working with? (CSV, JSON, Parquet, database)
   - What is the analysis goal? (exploration, prediction, comparison, importance ranking)
   - What are the key columns/features?

2. **Ingest and validate.** Read the `data-pipeline-patterns` skill:
   - Load the data with explicit `dtype` specifications (no silent type inference)
   - Validate the schema: check for required columns, correct types, expected value ranges
   - Report data quality: row count, null counts per column, duplicate rows
   - Quarantine invalid rows into a separate DataFrame with reasons

3. **Exploratory analysis.** Read the `statistical-analysis` skill:
   - Compute summary statistics (mean, median, std, min, max, quartiles)
   - Check distributions (Shapiro-Wilk normality test, histograms)
   - Identify outliers (IQR method or z-score)
   - Compute correlation matrix for numeric features
   - Report findings before proceeding to modeling

4. **Modeling (if applicable).** Read the `sklearn-model-analysis` skill:
   - Split data with `train_test_split` (stratify if classification)
   - Build a `Pipeline` with preprocessing + model (prevent data leakage)
   - Cross-validate with `StratifiedKFold` or `TimeSeriesSplit`
   - Report mean ± std across folds
   - Compare against `DummyClassifier`/`DummyRegressor` baseline
   - Extract feature importance (permutation importance + SHAP if applicable)

5. **Statistical testing (if comparing groups).** Read the `statistical-analysis` skill:
   - Choose parametric vs non-parametric test based on distribution check from step 3
   - Apply multiple comparison correction if 3+ groups
   - Report p-values AND effect sizes
   - Report confidence intervals for key metrics

6. **Visualization.** Read the `matplotlib-publication-figures` skill:
   - Set figure sizes appropriate for the target (paper column width, presentation, report)
   - Use colorblind-friendly palettes with distinct line styles/markers
   - Label all axes with metric name and unit
   - Add error bars (mean ± std) or confidence interval shading
   - Export as PDF (vector) and PNG (300 DPI)
   - Remove unnecessary chart junk (top/right spines, excessive gridlines)

7. **Write the analysis script.** Read the `python-project-patterns` skill:
   - Type hints on all functions
   - Docstrings explaining what each analysis step does
   - Save all outputs (tables, figures, intermediate data) to a structured results directory
   - Make the script reproducible: explicit seeds, pinned library versions, CLI arguments
