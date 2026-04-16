---
name: research-project-management
description: Work plan tracking, dependency management, milestone planning, reviewer response strategy. Use when organizing research timelines, tracking experiment progress, planning thesis or paper milestones, or preparing reviewer rebuttals.
---

# Research Project Management

Follow these practices when planning, tracking, and managing academic or applied research projects.

## 1. Work Plan Structure

Decompose the project into phases with clear deliverables:

```
Phase 1: Foundation (Weeks 1–3)
  ├── Literature review complete
  ├── Baseline implementation verified
  └── Evaluation pipeline tested end-to-end

Phase 2: Core Contribution (Weeks 4–8)
  ├── Proposed method implemented
  ├── Initial results on primary benchmark
  └── Ablation study designed

Phase 3: Evaluation & Writing (Weeks 9–12)
  ├── Full experimental campaign (multi-seed, all benchmarks)
  ├── Paper draft complete
  └── Figures finalized (camera-ready quality)
```

- Each phase should have **entry criteria** (what must be true before starting) and **exit criteria** (what must be delivered to move on).
- Track progress with a simple checklist or kanban board, not heavyweight project management tools.

## 2. Dependency Management

Research tasks often have hidden dependencies. Map them explicitly:

- **Data dependencies**: Which experiments require which datasets to be prepared?
- **Compute dependencies**: Which experiments need GPU access, cluster time, or long-running slots?
- **Knowledge dependencies**: Which experiments require results from earlier experiments to inform design decisions?
- **External dependencies**: Waiting on access to a system, a collaborator's code, or a dataset license?

Identify the **critical path** — the longest chain of sequential dependencies — and prioritize unblocking it.

## 3. Milestone Planning

Align milestones with external deadlines:

| Milestone | Typical Deadline |
|-----------|-----------------|
| Paper submission | Conference deadline (hard) |
| Thesis chapter draft | Supervisor review (soft) |
| Experiment completion | 2–3 weeks before writing begins |
| Camera-ready figures | 1 week before submission |
| Code cleanup & release | Before or at publication |

- **Buffer rule**: Plan to finish experiments at least 2 weeks before the writing deadline. Writing always takes longer than expected.
- **Parallel tracks**: While experiments run (often overnight or over days), use that time for writing, literature review, or code cleanup.

## 4. Experiment Tracking Integration

- Maintain a running **experiment log** (markdown, spreadsheet, or notebook) that records:
  - What was tried (hypothesis, configuration)
  - What happened (result, observation)
  - What was learned (insight, next step)
- Tag experiments as: `exploratory`, `confirmatory`, or `failed` (with reason).
- Never delete failed experiments — they inform future decisions.

## 5. Reviewer Response Strategy

When responding to peer reviews:

- **Acknowledge every point**: Even if you disagree, show you understood the reviewer's concern.
- **Be concrete**: "We added experiment X (Table 3) which shows Y" is better than "We addressed this concern."
- **Diff the paper**: Provide a marked-up version showing exactly what changed.
- **Prioritize**: Address major concerns first. Minor formatting or typo fixes can be grouped.
- **Stay respectful**: Reviewers volunteer their time. Thank them, even for harsh reviews.

## 6. Collaboration Practices

- Use version control for everything: code, paper source (LaTeX), and experiment configurations.
- Establish a shared naming convention for experiments, branches, and result directories.
- Schedule regular syncs (weekly for active projects) with a fixed agenda: progress, blockers, next steps.
- Keep a shared "decisions log" for design choices made during meetings, so they don't get lost.
