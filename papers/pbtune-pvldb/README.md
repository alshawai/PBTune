---
post_title: "PBTune PVLDB Paper Workspace"
author1: "TBD"
post_slug: "pbtune-pvldb-paper-workspace"
microsoft_alias: "TBD"
featured_image: "https://example.com/placeholder.png"
categories:
  - paper
tags:
  - pbtune
  - pvldb
  - paper
ai_note: "AI-assisted"
summary: "PVLDB paper workspace layout and build steps."
post_date: "2026-05-04"
---

## PBTune PVLDB Paper Workspace

This folder contains the PVLDB paper source for PBTune.

## Getting Started

Prerequisites:

- PVLDB template files (`acmart.cls`, `ACM-Reference-Format.bst`, and any required assets).
- LaTeX distribution (TeX Live 2024+ or MikTeX).
- `latexmk` and `perl`.

Setup steps:

1. Download the official PVLDB template.
2. Copy `acmart.cls` into this folder.
3. Copy `ACM-Reference-Format.bst` into this folder.

## PVLDB Template Files

Use the official PVLDB sources:

- Formatting guidelines: https://www.vldb.org/pvldb/volumes/19/formatting
- LaTeX template zip: https://github.com/cwida/pvldbstyle/archive/master.zip
- Overleaf template: https://www.overleaf.com/latex/templates/template-for-proceedings-of-the-vldb-endowment/krfrpvrbbvfj

Required files from the zip:

- `acmart.cls`
- `ACM-Reference-Format.bst`

## Minimal Example

Build the paper:

```bash
latexmk -pdf -interaction=nonstopmode main.tex
```

The build uses `latexmkrc` in this folder for consistent options.

Clean build artifacts:

```bash
latexmk -c
```

## PVLDB Metadata Checklist

Update these fields in `main.tex` before submission:

- `\vldbdoi`
- `\vldbpages`
- `\vldbvolume`
- `\vldbissue`
- `\vldbyear`
- `\vldbavailabilityurl` (replace `URL_TO_ARTIFACTS` or leave empty)
- `\title` and `\shorttitle`
- Author blocks (each author in its own `\author` entry)

If submitting an Experiment, Analysis and Benchmark paper, append the category
suffix to the paper title for review (it is removed in camera-ready).

## Architecture Overview

- `main.tex` is the paper entry point.
- `macros.tex` defines reusable commands such as the system name.
- `sections/` contains one file per major section.
- `references.bib` holds BibTeX entries.

## Development Setup

For the full contributor environment, see
[CONTRIBUTOR_ENVIRONMENT.md](CONTRIBUTOR_ENVIRONMENT.md).
