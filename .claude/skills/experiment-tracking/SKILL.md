---
name: experiment-tracking
description: Reproducibility checklist, seed management, hardware logging, results schema design. Use when setting up experiment tracking or managing complex run structures.
---

# Experiment Tracking

Follow these guidelines for rigorous tracking and reproducibility of scientific / system benchmark experiments.

## 1. Directory Structure
A clean structure is essential for running multiple experiments without cross-contamination.

```
results/
├── {experiment_name}_{timestamp}/
│   ├── metadata.json       # Hardware, OS, Git commit hash, raw arguments
│   ├── config/             # The exact generated/tested configuration
│   ├── logs/               # Standard out/error logs
│   └── data/               # Raw output metrics (.csv, .json)
```

## 2. Seed Management
- Set seeds explicitly for all sources of randomness: `numpy.random`, `random`, `torch`, `hash` functions.
- For robust multi-seed campaigns, loop over an explicit list of prime/distinct seeds (e.g., `[42, 123, 456, 789, 1024]`).
- Log the specific seed used inside the experiment metadata.

## 3. Environment & Hardware Provenance
Every run should programmatically capture and save:
- Git commit hash of the current code.
- Whether there are uncommitted changes (git status).
- OS details, Kernel version.
- Hardware info: CPU model, Total RAM, Disk type.
- This takes 1 second to gather via `sys`, `platform`, `psutil`, and `subprocess` but saves hours of confusion months later.

## 4. Results Schema Design
- Write end-result summaries as clear JSON or CSV.
- Ensure the schema captures both:
  1. The input configuration vector (what was tested).
  2. The output metric vector (the results: throughput, latency, variance).
- Include validation/error states. If a run failed, track `{"status": "failed", "error": "timeout"}` instead of dropping the row silently.

## 5. Artifact Archival
- Treat the `results/` folder as append-only. 
- If an experiment had a bug, mark it as invalid in a README or database instead of deleting the directory, or move it to a `deprecated/` folder. This preserves history.
- Consistently bundle or tarball successful experiment groupings for backup.

## ML/System Reproducibility Checklist
Ensure these are addressed:
- [ ] Dependencies documented with exact versions (e.g., `requirements.txt` with locked versions).
- [ ] Evaluation protocol heavily scripted minimizing human manual steps.
- [ ] Hyperparameters to both the model and the optimization algorithm explicitly recorded.
