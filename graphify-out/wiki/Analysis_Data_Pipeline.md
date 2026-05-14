# Analysis Data Pipeline

> 8 nodes · cohesion 0.32

## Key Concepts

- **TuningRun** (7 connections) — `src/scripts/pbt_vs_bo_comarison.py`
- **._parse_json()** (6 connections) — `src/scripts/pbt_vs_bo_comarison.py`
- **EvaluationPoint** (5 connections) — `src/scripts/pbt_vs_bo_comarison.py`
- **pbt_vs_bo_comarison.py** (5 connections) — `src/scripts/pbt_vs_bo_comarison.py`
- **_extract_seed()** (2 connections) — `src/scripts/pbt_vs_bo_comarison.py`
- **Represents a single evaluation point during tuning.** (1 connections) — `src/scripts/pbt_vs_bo_comarison.py`
- **Parses and encapsulates the data of a single tuning run (one seed).** (1 connections) — `src/scripts/pbt_vs_bo_comarison.py`
- **Loads JSON and parses the sequence of evaluations.** (1 connections) — `src/scripts/pbt_vs_bo_comarison.py`

## Relationships

- [[PBT vs BO Comparison]] (18 shared connections)
- [[Cross-Module Rationale]] (3 shared connections)
- [[Evaluator Fault Injection]] (3 shared connections)
- [[Docker Manifest Tests]] (2 shared connections)
- [[Cleanup Scripts]] (2 shared connections)

## Source Files

- `src/scripts/pbt_vs_bo_comarison.py`

## Audit Trail

- EXTRACTED: 25 (89%)
- INFERRED: 3 (11%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*