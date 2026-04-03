---
description: Academic paper drafting pipeline from outline through writing to figure generation. Combines paper structure, literature synthesis, project management, and publication-quality visualization. Use when starting or continuing work on a research paper.
---

# /paper-draft — Academic Paper Pipeline

**Skills used:** `academic-paper-writing`, `literature-review-methodology`, `research-project-management`, `matplotlib-publication-figures`, `documentation-as-code`, `reproducibility-checklist`

This workflow has three modes. Ask the user which phase they're in.

---

## Phase A: Planning & Outline

1. **Assess readiness.** Read the `research-project-management` skill. Check:
   - Are experiments complete? (Required for Evaluation section)
   - Is the related work surveyed? (Required for Background section)
   - What's the submission deadline? (Work backwards with buffer rule: experiments done 2 weeks before writing)

2. **Create the paper skeleton.** Read the `academic-paper-writing` skill. Set up the IMRaD structure:
   - `sections/introduction.tex` — Context, motivation, problem, contributions
   - `sections/background.tex` — Related work (thematic, not chronological)
   - `sections/methodology.tex` — System design, algorithms, implementation
   - `sections/evaluation.tex` — Setup, results, discussion
   - `sections/conclusion.tex` — Summary, limitations, future work
   - `macros.tex` — System name macro, recurring terms
   - `references.bib` — BibTeX entries

3. **Define research questions.** Help the user articulate 3–5 specific research questions (RQs). Each RQ should map to a specific experiment and subsection in the Evaluation.

---

## Phase B: Writing & Literature

1. **Related work synthesis.** Read the `literature-review-methodology` skill. Guide the user through:
   - Defining search strings for their domain
   - Creating a synthesis matrix: `[Paper] | [Approach] | [Key Result] | [Limitation]`
   - Writing thematic groups (NOT paper-by-paper lists)
   - Identifying the specific gap this work fills

2. **Section drafting.** Read the `academic-paper-writing` skill. For each section:
   - Use formal, objective, precise language
   - Active voice where it adds clarity
   - One sentence per line (clean git diffs)
   - `\label` immediately after `\caption`, `\section`
   - Consistent citation style (`\citet` vs `\citep`)

3. **Cross-reference documentation.** Read the `documentation-as-code` skill. Ensure:
   - The README explains how to reproduce each table/figure in the paper
   - ADRs exist for major design decisions referenced in the paper
   - Code comments explain the *why* for any non-obvious implementation choices

---

## Phase C: Figures & Finalization

1. **Generate figures.** Read the `matplotlib-publication-figures` skill. For each figure:
   - Set explicit figure size matching the venue's column width
   - Use colorblind-friendly palettes with varying line styles/markers
   - Label axes with metric name AND unit
   - Export as PDF (vector) for plots, PNG at 300 DPI for raster
   - Remove unnecessary spines, add subtle gridlines

2. **Pre-submission checklist.** Read the `reproducibility-checklist` skill. Verify:
   - [ ] All hyperparameters documented in the paper
   - [ ] Evaluation metrics defined (not just named)
   - [ ] Baselines described with sufficient detail to reproduce
   - [ ] Hardware specs and software versions listed
   - [ ] Random seeds reported
   - [ ] Error bars / confidence intervals shown
   - [ ] Code and data archival plan (Zenodo DOI, GitHub tag)

3. **Final formatting pass.** Check:
   - No orphaned `\label` without a `\ref`
   - All figures and tables are referenced in the text
   - Abstract is self-contained (no citations, no acronyms without expansion)
   - Page limit is met
