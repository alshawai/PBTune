---
name: statistical-analysis
description: Statistical analysis practices, hypothesis testing, effect sizes, non-parametric tests, multiple comparison correction. Use when analyzing experiment results or running statistical tests.
---

# Statistical Analysis

Follow these practices when analyzing data, especially experimental performance metrics.

## Exploring the Data First

- Always visualize the distribution before running tests (e.g., histograms, raincloud plots, boxplots).
- Check assumptions: Are the samples normally distributed? (Use Shapiro-Wilk or look at Q-Q plots). 
- Are variances equal? (Levene's test).

## Hypothesis Testing

- Clearly define the Null Hypothesis ($H_0$) and Alternative Hypothesis ($H_1$).
- Set the significance level ($\alpha$, usually 0.05) before analyzing the data.

### Selecting the Right Test
- **Parametric** (assumes normality):
  - 2 Groups: Independent t-test (or Paired t-test if dependent).
  - 3+ Groups: ANOVA.
- **Non-Parametric** (does not assume normality - *common in systems benchmarking*):
  - 2 Groups: Mann-Whitney U test (or Wilcoxon signed-rank for paired).
  - 3+ Groups: Kruskal-Wallis test.

## Multiple Comparisons

- When performing multiple statistical tests, the chance of a false positive increases.
- Always apply a correction method (e.g., Bonferroni correction for strict control, or Benjamini-Hochberg for False Discovery Rate).

## Reporting Effect Sizes and Uncertainty

- A p-value only tells you if an effect exists, not how large it is. Always report effect sizes!
  - Cohen's *d* for t-tests.
  - Cliff's Delta or Vargha-Delaney $A$ for non-parametric tests. 
- Use Confidence Intervals (CIs) to show the range of plausible values (e.g., "The system improved throughput by 15% (95% CI: [12%, 18%])").
- When using randomness (e.g., varied seeds), always report the Mean $\pm$ Standard Deviation across seeds.

## Code Libraries

- Use `scipy.stats` for foundational statistical tests.
- Use `statsmodels` for advanced modeling and multiple comparison correction.
- Use `numpy` / `pandas` for aggregations and rolling calculations.
