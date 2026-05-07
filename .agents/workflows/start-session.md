---
description: Run this workflow immediately when starting a new session to load the complete project context and relevant skills.
---

# Start Session / Load Context

Welcome to the **Population-Based Training for PostgreSQL Configuration Tuning** repository!

This is a complex research project with a layered architecture, a custom multi-objective scoring pipeline, multi-instance PostgreSQL management, and a rigorous post-hoc evaluation suite.

To be an effective agent in this repository, you must avoid guessing how the system works and instead load the curated domain knowledge (skills). Follow this workflow to bootstrap your context.

## 1. Load the Universal Architecture Map (Always Do This)

Before answering questions or looking at code, you MUST understand the high-level data flow and package structure.

**Action:** Read the `codebase-architecture` skill.
- This gives you the map of all `src/` packages, file responsibilities, and how the tuning engine orchestrates runs.

## 2. Load the Relevant Domain Skills (Task-Dependent)

Based on the user's initial prompt, identify which subsystems you'll be touching and load the corresponding skills. You can load multiple skills if the task spans multiple areas.

### The Tuning Engine
If the task involves the PBT algorithm, exploit/explore logic, worker management, convergence, or the `src/tuner/core/` package:
- **Action:** Read the `pbt-algorithm-patterns` skill.

### Configuration & Parameter Space
If the task involves adding new PostgreSQL knobs, parameter bounds, hardware-aware fractional representation, or code in `src/tuner/config/` and `src/knobs/`:
- **Action:** Read the `postgresql-knob-tuning` skill.

### The Scoring Pipeline (v2)
If the task involves how performance metrics are normalized, weighted, or scored, or code in `src/utils/scoring/` and `src/utils/metrics.py`:
- **Action:** Read the `scoring-pipeline` skill.

### Execution & Instances
If the task involves Docker vs. bare-metal instances, benchmark execution (Sysbench/TPC-H), snapshot restoration, or code in `src/utils/environments/` and `src/benchmarks/`:
- **Action:** Read the `benchmark-orchestration` skill AND the `environment-backends` skill.

### Evaluation & Statistics
If the task involves comparing tuned configs against defaults, statistical significance, the `src/evaluation/` package, or the `python -m src.evaluation` CLI:
- **Action:** Read the `evaluation-suite` skill AND the `statistical-analysis` skill.

### Analysis & Experimentation
If the task involves fANOVA/TreeSHAP knob importance, running multi-seed campaigns, or preparing plots:
- **Action:** Read the `knob-importance-analysis` skill AND the `scientific-experiment-runner` skill.

## 3. Verify Development Environment

If you are going to write code, you need to know the CI gates and testing standards.
- **Action:** Read the `dev-workflow` skill to learn about `make check-all`, pytest, ruff, and mypy constraints.

## 4. Execute Context Bootstrap

Once you've identified and read the necessary skills:
1. Summarize back to the user your understanding of their task and which subsystems/skills are involved.
2. Check the project git status (`git status`, `git log -n 3`) to see if there is uncommitted work in progress.
3. State your plan for execution or ask clarifying questions based on the loaded domain knowledge.

> **Note to Agent:** Do not hallucinate PostgreSQL tuning rules or standard PBT algorithms. The custom implementations, scoring formulas, and hardware representations defined in these skills are the absolute ground truth for this codebase.
