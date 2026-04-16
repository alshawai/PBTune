---
name: academic-benchmarking
description: Fair comparison methodology, controlling for hardware, citing published baselines, statistical significance in benchmarks. Use when designing experiments that compare systems or algorithms, or when reviewing benchmark results for publication.
---

# Academic Benchmarking

Follow these principles when designing, running, or reporting benchmarks that compare systems, algorithms, or configurations in an academic context.

## 1. Fair Comparison Methodology

- **Same Budget**: Compare methods using the same computational budget (wall-clock time, number of function evaluations, or FLOPs) — not just "number of iterations," which can vary wildly in cost.
- **Same Evaluation Protocol**: All compared methods must use the identical evaluation pipeline, datasets, splits, and metrics. Any difference invalidates the comparison.
- **Same Hardware**: Run all methods on the same machine (or at minimum, equivalent hardware). Report the hardware specification alongside results.
- **Same Software Stack**: Pin library versions, compiler flags, and runtime settings. A benchmark run on PyTorch 2.0 is not directly comparable to one on PyTorch 1.13.

## 2. Baseline Selection

- **Always include a trivial baseline**: Random search, default configuration, or a simple heuristic. This establishes the floor and prevents over-claiming.
- **Include the state-of-the-art**: Cite and reproduce (or use official implementations of) the current best-known method. If you cannot reproduce it, state this explicitly and cite the published numbers.
- **Re-run, don't just cite**: Published numbers from other papers were collected under different conditions. Whenever possible, re-run baselines on your hardware with your evaluation protocol. If citing published numbers, clearly label them as "reported" vs. "reproduced."

## 3. Controlling for Randomness

- Run every method with **multiple seeds** (minimum 5, ideally 10+).
- Report **mean ± standard deviation** or **median with interquartile range** — never a single run.
- Use statistical significance tests (e.g., Wilcoxon signed-rank for paired comparisons, Mann-Whitney U for unpaired) and report p-values.
- Report **effect sizes** (e.g., Cohen's d, Cliff's delta) alongside p-values. A statistically significant but tiny improvement may not be practically meaningful.

## 4. Reporting Results

- **Tables**: Show mean ± std for each method on each benchmark. Bold the best result. Use underline or a different marker for second-best.
- **Critical difference diagrams**: For comparing many methods across many benchmarks, use Nemenyi post-hoc test with critical difference plots (see `autorank` or `Orange` libraries).
- **Convergence curves**: Show performance over time or iterations, not just final values. This reveals sample efficiency and convergence behavior.
- **Ablation studies**: When proposing a method with multiple components, show the contribution of each component by removing them one at a time.

## 5. Common Pitfalls to Avoid

- **Cherry-picking benchmarks**: Report results on all datasets you ran, not just the ones where your method wins.
- **Overfitting to the test set**: Never tune hyperparameters on the test set. Use a held-out validation set for tuning and report test set results exactly once.
- **Ignoring wall-clock time**: A method that achieves 1% better accuracy but takes 100× longer may not be a meaningful improvement. Report both quality and efficiency.
- **Unfair hyperparameter tuning**: If you extensively tune your method but use default hyperparameters for baselines, the comparison is unfair. Tune all methods with equivalent effort.

## 6. Reproducibility

- Release code, data, and configuration files needed to reproduce results.
- Include a single-command reproduction script (e.g., `make reproduce` or `./run_all.sh`).
- Archive artifacts on a persistent platform (Zenodo, Figshare) with a DOI, not just a GitHub link.
