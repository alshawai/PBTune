---
post_title: "PBTune Paper Contributor Environment"
author1: "TBD"
post_slug: "pbtune-paper-contributor-environment"
microsoft_alias: "TBD"
featured_image: "https://example.com/placeholder.png"
categories:
  - development
tags:
  - pbtune
  - pvldb
  - latex
ai_note: "AI-assisted"
summary: "Contributor setup for editing and building the PVLDB paper."
post_date: "2026-05-04"
---

## Overview

This guide covers the tools and extensions needed to edit and build the
PVLDB paper source.

## Required Packages

- LaTeX distribution (TeX Live 2024+ or MikTeX).
- `latexmk` (build automation).
- `perl` (required by `latexmk`).
- BibTeX (included in most LaTeX distributions).

Example install (Debian or Ubuntu):

```bash
sudo apt-get update
sudo apt-get install texlive-full latexmk perl
```

## Recommended Tools

- `latexindent` for formatting LaTeX sources.
- `chktex` for linting common LaTeX issues.

## PVLDB Template Sources

- Formatting guidelines: https://www.vldb.org/pvldb/volumes/19/formatting
- LaTeX template zip: https://github.com/cwida/pvldbstyle/archive/master.zip

## VS Code Extensions

- `James-Yu.latex-workshop` for LaTeX build and preview.
- `streetsidesoftware.code-spell-checker` for spelling.
- `eamodio.gitlens` for change history.

## Suggested VS Code Settings

These settings improve LaTeX feedback and reduce build friction:

```json
{
  "latex-workshop.latex.autoBuild.run": "onFileChange",
  "latex-workshop.latex.outDir": "build",
  "latex-workshop.view.pdf.viewer": "tab"
}
```

## Validate the Setup

From this directory, build the paper:

```bash
latexmk -pdf -interaction=nonstopmode main.tex
```

This uses the local `latexmkrc` for build options.

If the build fails with a missing class error, ensure `acmart.cls` and
`ACM-Reference-Format.bst` are present as described in
[README.md](README.md).
