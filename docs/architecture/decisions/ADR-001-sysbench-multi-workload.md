# ADR-001: Sysbench Multi-Workload Integration

- Status: Accepted
- Date: 2026-04-25

## Context

The project previously treated Sysbench OLTP as a single benchmark profile (`oltp_read_write`).
This created ambiguity when comparing sessions that are semantically different (`oltp_read_only`,
`oltp_read_write`, `oltp_write_only`) but share the same output path and session metadata shape.

The ambiguity impacted:

- Reproducibility of tuning and evaluation artifacts.
- Fair comparison between default, BO, and PBT under different transaction mixes.
- Downstream automation (analysis, aggregation, and reporting).

## Decision

We introduce explicit Sysbench workload mode handling end-to-end:

1. Canonical modes:

- `oltp_read_only`
- `oltp_read_write`
- `oltp_write_only`

1. Session metadata:

- Add `tuning_session.sysbench_workload` for Sysbench runs.
- Backward-compatible fallback for legacy sessions: `oltp_read_write`.

1. CLI contracts:

- Add `--sysbench-workload` to tuner, BO runner, and evaluation CLI.
- Use precedence in evaluation: CLI override -> session metadata -> default.

1. Output partitioning:

- PBT: `results/oltp/{sysbench_workload}/pbt_runs/{tier}/...`
- BO: `results/oltp/{sysbench_workload}/bo_runs/{tier}/...`
- Evaluation: `results/oltp/{sysbench_workload}/comparisons/{tier}/...`

1. Executor behavior:

- `SysbenchExecutor` validates script mode and uses it as Sysbench Lua script selector.

## Consequences

Positive:

- Clear, machine-readable differentiation between Sysbench workload classes.
- Reduced risk of cross-mode artifact contamination.
- Stronger methodological reproducibility for research comparisons.

Trade-offs:

- Result directory hierarchy becomes deeper for Sysbench workflows.
- Historical scripts that hardcode old paths may need updates.

## Compatibility and Migration

- Legacy sessions lacking `sysbench_workload` continue to load with default `oltp_read_write`.
- Tier resolution from legacy path layout remains supported.
- Non-Sysbench benchmarks (TPC-H/custom workloads) keep existing behavior.

## Rejected Alternatives

1. Path-only encoding of workload mode without session metadata.

- Rejected because session files must remain self-describing.

1. Separate executors per Sysbench mode.

- Rejected because one validated executor with script selection is simpler and less error-prone.

1. Numeric mode IDs.

- Rejected because script names are more readable and map directly to Sysbench CLI semantics.
