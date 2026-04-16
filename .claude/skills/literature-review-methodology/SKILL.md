---
name: literature-review-methodology
description: Systematic search strategy, inclusion/exclusion criteria, evidence synthesis, gap identification. Use when searching for papers, summarizing related work, or structuring a literature review.
---

# Literature Review Methodology

When conducting literature reviews, summarizing related work, or structuring research gaps, use a systematic rather than ad-hoc approach.

## 1. Defining the Search Strategy

- Formulate specific search strings (e.g., `"federated learning" AND ("privacy" OR "differential privacy")`).
- Use high-quality academic indexes (e.g., ACM Digital Library, IEEE Xplore, Google Scholar, DBLP).
- Track the venues (e.g., SIGMOD, VLDB, OSDI, NeurIPS) to prioritize high-impact peer-reviewed work.

## 2. Inclusion & Exclusion Criteria

Document precisely why papers are included or excluded stringently.
- *Inclusion*: Papers published in the last 10 years, focusing on automated tuning, published in Tier-1 databases or ML venues.
- *Exclusion*: Papers focused solely on tangential sub-problems, non-peer-reviewed preprints (unless highly cited).

## 3. The Extraction Process

Do not read papers blindly from start to finish. Scan deliberately:
1. **Title & Abstract**: Determine relevance.
2. **Introduction & Conclusion**: Understand the core claim and takeaway.
3. **Figures & Evaluation**: How do they prove their claim? What baselines did they use?
4. **Methodology**: Read thoroughly only if the paper is highly relevant.

Create a synthesis matrix (e.g., in a spreadsheet or markdown table) tracking: `[Paper Citation] | [Target Domain] | [Core Technique] | [Key Limitation / Gap]`.

## 4. Writing the Synthesis

- **DO NOT write a chronological or paper-by-paper list** ("Alice did X. Bob did Y. Charlie did Z.").
- **DO write a thematic synthesis**. Group literature by approach or problem angle.
  - *Example*: "While centralized approaches to model training [Alice, Bob] achieve strong accuracy, they require aggregating sensitive data. In contrast, federated methods [Charlie, Dave] offer better privacy guarantees but struggle with heterogeneous data distributions..."
- **Identify the Gap**: The entire purpose of the related work section is to highlight the specific gap that *your* work fills. Conclude the section by explicitly contrasting your approach with the limitations of the reviewed literature.
