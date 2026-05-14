# Cleanup Scripts

> 19 nodes · cohesion 0.13

## Key Concepts

- **._default_snapshot_id()** (8 connections) — `src/utils/environments/docker.py`
- **._remove_baseline_snapshot()** (8 connections) — `src/utils/environments/docker.py`
- **.snapshot_exists()** (7 connections) — `src/utils/environments/docker.py`
- **._snapshot_manifest_path()** (6 connections) — `src/utils/environments/docker.py`
- **._snapshot_profile_signature()** (6 connections) — `src/utils/environments/docker.py`
- **._write_snapshot_manifest()** (6 connections) — `src/utils/environments/docker.py`
- **._read_snapshot_manifest()** (4 connections) — `src/utils/environments/docker.py`
- **._remove_snapshot_manifest()** (4 connections) — `src/utils/environments/docker.py`
- **._snapshot_profile_context()** (4 connections) — `src/utils/environments/docker.py`
- **Remove the baseline snapshot directory if it exists.** (1 connections) — `src/utils/environments/bare_metal.py`
- **Build a stable benchmark-profile payload used for snapshot identity.** (1 connections) — `src/utils/environments/docker.py`
- **Compute a compact signature for the current benchmark schema profile.** (1 connections) — `src/utils/environments/docker.py`
- **Build a Docker-safe snapshot repository name for this profile.** (1 connections) — `src/utils/environments/docker.py`
- **Path to snapshot metadata persisted in the project tree.** (1 connections) — `src/utils/environments/docker.py`
- **Persist snapshot metadata for traceability within the project workspace.** (1 connections) — `src/utils/environments/docker.py`
- **Load snapshot metadata manifest, returning None when missing or invalid.** (1 connections) — `src/utils/environments/docker.py`
- **Remove local snapshot metadata manifest if present.** (1 connections) — `src/utils/environments/docker.py`
- **Remove existing baseline snapshot image and metadata.** (1 connections) — `src/utils/environments/docker.py`
- **Check whether the baseline snapshot image already exists.** (1 connections) — `src/utils/environments/docker.py`

## Relationships

- [[TPC-H Query Executor]] (55 shared connections)
- [[Bare Metal Environment]] (3 shared connections)
- [[Workload README]] (2 shared connections)
- [[Scoring Policies]] (2 shared connections)
- [[BO Config & Worker]] (1 shared connections)

## Source Files

- `src/utils/environments/bare_metal.py`
- `src/utils/environments/docker.py`

## Audit Trail

- EXTRACTED: 62 (98%)
- INFERRED: 1 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [[index]] to navigate.*