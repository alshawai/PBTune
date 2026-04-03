---
description: Plan and execute a focused research sprint combining project management, experiment design, optimization methodology, and result tracking. Use when starting a new phase of research, planning a multi-day experiment campaign, or preparing for a deadline.
---

# /research-sprint — Research Sprint Planning

**Skills used:** `research-project-management`, `experiment-tracking`, `academic-benchmarking`, `hyperparameter-optimization`, `reproducibility-checklist`, `statistical-analysis`

## Steps

1. **Define the sprint goal.** Read the `research-project-management` skill. Establish:
   - What is the specific research question for this sprint?
   - What is the delivery deadline?
   - What does "done" look like? (e.g., "Table 3 filled with all baseline comparisons")

2. **Map dependencies.** Identify blockers before starting:
   - **Data**: Are all datasets/workloads ready?
   - **Compute**: Is hardware available? How long will the campaign take?
   - **Knowledge**: Do previous experiments inform this sprint's design?
   - **External**: Waiting on collaborators, access, or licenses?
     Highlight the critical path and identify what can run in parallel.

3. **Design the experiment matrix.** Read the `academic-benchmarking` skill and `hyperparameter-optimization` skill:
   - Define the methods to compare (proposed + baselines)
   - Define the search space for any tunable parameters
   - Ensure fair comparison: same budget, same evaluator, same hardware
   - Plan the seed list (minimum 5 seeds per configuration)
   - Estimate total runs: `methods × configurations × seeds × benchmarks`
   - Estimate total wall-clock time

4. **Set up tracking.** Read the `experiment-tracking` skill:
   - Create the results directory structure
   - Prepare the metadata capture script (git hash, hardware, dependencies)
   - Set up the experiment log (markdown or spreadsheet)

5. **Pre-flight check.** Read the `reproducibility-checklist` skill:
   - [ ] Dependencies locked
   - [ ] Seeds explicitly set
   - [ ] Evaluation pipeline tested end-to-end on a single short run
   - [ ] Results schema documented
   - [ ] Failure handling tested (what happens if a run crashes mid-way?)

6. **Create the sprint plan.** Present a day-by-day plan:

   ```
   Day 1: Launch exploratory runs (2 seeds, verify pipeline works)
   Day 2: Launch full campaign (all seeds, all methods)
   Day 3-4: Monitor, fix failures, re-run if needed
   Day 5: Aggregate results, statistical analysis
   Day 6: Generate figures, draft results section
   Day 7: Buffer for unexpected issues
   ```

   Adjust timeline to the actual deadline using the buffer rule (finish 2 weeks before writing).

7. **Analysis plan.** Read the `statistical-analysis` skill. Pre-commit to:
   - Which statistical test will be used
   - What significance threshold (α = 0.05)
   - What effect size measure
   - What correction for multiple comparisons
     Deciding this BEFORE seeing results prevents p-hacking.

8. **Sprint review.** At sprint end, document:
   - What was accomplished vs planned
   - Key findings and surprises
   - Open questions for the next sprint
   - Any changes to the research direction based on results
