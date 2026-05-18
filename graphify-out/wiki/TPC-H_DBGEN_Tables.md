# TPC-H DBGEN Tables

> 10 nodes · cohesion 0.24

## Key Concepts

- **compare_to_expert()** (6 connections) — `src/analysis/tier_generator.py`
- **get_tier_rank_map()** (5 connections) — `src/analysis/tier_generator.py`
- **TuningMetadata** (5 connections) — `src/knobs/knob_metadata.py`
- **test_agreement_report_demotion()** (4 connections) — `tests/unit/analysis/test_tier_generator.py`
- **test_agreement_report_promotion()** (4 connections) — `tests/unit/analysis/test_tier_generator.py`
- **AgreementReport** (4 connections) — `src/analysis/tier_generator.py`
- **Build a ranking map for tier names.      Args:         tier_names: Ordered tier** (1 connections) — `src/analysis/tier_generator.py`
- **Compare data-driven tiers against expert tiers.      Args:         tier_assignme** (1 connections) — `src/analysis/tier_generator.py`
- **Comparison between expert tiers and data-driven tiers.      Attributes:** (1 connections) — `src/analysis/tier_generator.py`
- **Tuning-specific metadata for a knob.      Attributes     ----------     tuning_m** (1 connections) — `src/knobs/knob_metadata.py`

## Relationships

- [[Query Pattern Analysis]] (22 shared connections)
- [[Metric Validation Docs]] (4 shared connections)
- [[Instance Management]] (3 shared connections)
- [[Snapshot & Persistence]] (1 shared connections)
- [[Knob Metadata]] (1 shared connections)
- [[Session Tests]] (1 shared connections)

## Source Files

- `src/analysis/tier_generator.py`
- `src/knobs/knob_metadata.py`
- `tests/unit/analysis/test_tier_generator.py`

## Audit Trail

- EXTRACTED: 20 (62%)
- INFERRED: 12 (38%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*