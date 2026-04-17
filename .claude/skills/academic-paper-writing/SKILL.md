---
name: academic-paper-writing
description: LaTeX conventions, paper structure (IMRaD), citation management, related work synthesis. Use when drafting papers, formatting LaTeX, or writing academic content.
---

# Academic Paper Writing

Follow these conventions when drafting academic papers (especially in Computer Science / Systems).

## Document Structure (IMRaD)
Typical systems and ML papers follow standard structure:
1. **Introduction**: Context, Motivation, Problem Statement, Contributions.
2. **Background / Related Work**: Placing the work in literature (can also be at the end).
3. **Methodology / System Design**: Architecture, core algorithms, and implementation.
4. **Evaluation**: Experimental setup, results, and discussion (answering specific research questions).
5. **Conclusion**: Summary and future work.

## LaTeX Best Practices

- **Separation of Concerns**: Use separate `.tex` files for large sections (e.g., `\input{sections/evaluation}`).
- **Macros**: Define macros for system names or heavily used terms in a `macros.tex` file to ensure consistency (`\newcommand{\sysname}{\textsc{MySystem}\xspace}`).
- **Formatting**:
  - Avoid hard-coding formatting (`\textbf`, `\textit`) for recurring semantic concepts. Map them to a macro.
  - Break lines at the end of sentences (one sentence per line). This makes git diffs much cleaner.
  - Use `\label` immediately after `\caption`, `\section`, etc. Follow a naming convention like `sec:intro`, `fig:architecture`, `tbl:results`.

## Citation Management

- Use BibTeX (`.bib` files).
- Citation keys should follow a consistent format, e.g., `[AuthorYearVenue]` like `smith2024sigmod`.
- Use `\cite`, or when using `natbib`/`biblatex`, differentiate between text citations `\citet` (Smith et al. show...) and parenthetical citations `\citep` (... as shown previously [Smith 2024]).

## Related Work Synthesis

- Do not just list papers ("A did X. B did Y."). Group them by theme or approach.
- Clearly state how the current work differs or improves upon previous work.
- Be respectful to prior work; avoid aggressive or purely negative framing.

## Style and Tone

- Use formal, objective, and precise scientific language.
- Prefer active voice where it adds clarity ("We implemented..." instead of "It was implemented...").
- Keep paragraphs focused on a single main idea with a clear topic sentence.
