# Timing Instrumentation — Contributor Guide

> Last reviewed: 2026-06-15
> Schema version this guide describes: **v1.1**
> See also: [session JSON schema (timing section)](session-json-schema.md#timing-instrumentation-v11), [timing instrumentation plan](../research/timing-instrumentation-plan.md)

This guide explains how to add a new component bracket to the PBTune timing instrumentation and how to read the timing data the system emits. The schema is documented in the [session JSON schema](session-json-schema.md#timing-instrumentation-v11); this page is about the *code* side.

## The primitives

Two small classes in [`src/utils/timing.py`](../../src/utils/timing.py) do all the work:

- `TimingRecord` — frozen dataclass with `component: str`, `seconds: float`, `metadata: dict[str, Any]`.
- `TimingRecorder` — collects records via a `span()` context manager (or `add()` for externally-measured durations), exposes `aggregate()` and `to_dict()`.

The clock is `time.monotonic()`. Never use `time.time()` for durations — it's not monotonic and can move backward under NTP corrections.

## Where the recorders live

There are three recorders, one per layer of the hierarchy. Add your bracket to the recorder that matches the scope of the work you're timing:

| Recorder | Lives on | Covers | Add new components for... |
| --- | --- | --- | --- |
| `tuner.bootstrap_timing` | `PBTTuner` | One-shot setup before the gen loop. | New bootstrap phases (schema load, snapshot warmup, etc.). |
| `population.generation_timing` | `Population` | Whole-generation work not attributable to a single worker. | New PBT-step phases (evolve, replication, etc.). |
| Per-worker recorder | Local in `WorkloadOrchestrator.evaluate_worker` | Per-worker apply → activate → workload → score. | New worker-side phases (e.g. cache warming). |

The per-worker recorder is returned out of `evaluate_worker` as part of the 5-tuple and attached to the worker as `worker.last_eval_timing` (see [`src/tuner/main.py`](../../src/tuner/main.py) around the `eval_timing` capture in `_evaluate_worker_wrapper`), which is the path it takes into the JSON.

## Adding a new bracket

1. **Pick the recorder** based on scope (see table above). For a per-worker bracket inside the orchestrator, use the local `recorder` already created at the top of `evaluate_worker`.

2. **Pick a component name.** Snake_case, stable, descriptive of the *operation*, not the call site (`apply_only`, not `applicator_step1`). Don't reuse names already in the [component reference](session-json-schema.md#component-reference) for unrelated work. Names are the dimension key in the cost-decomposition table; renaming one silently invalidates older sessions through the analysis script.

3. **Wrap the work** in a `span` context manager:

   ```python
   with recorder.span("my_component"):
       result = do_the_thing()
   ```

4. **Attach structured metadata** when the same component has variant semantics that downstream tooling needs to disambiguate:

   ```python
   with recorder.span("activate_reload", strategy="reload"):
       applicator.activate(...)
   ```

   Use metadata sparingly — every distinct `(component, metadata)` shape is something the analysis script has to understand. Prefer a new component name over a busy metadata bag when the operations are genuinely different.

5. **For externally-measured durations** (e.g. the C-binary reports its own wall-clock), use `add()` rather than `span()`:

   ```python
   recorder.add("sysbench_measurement", measured_seconds, observed=False)
   ```

6. **Update the component reference** in [session-json-schema.md](session-json-schema.md#component-reference) with the new entry: name, layer, semantics, source file. The table is the contract between code and the analysis script.

7. **Add a test.** At minimum, assert that a representative run produces a record with the new component name. The orchestrator-stub-based integration test pattern in `tests/unit/tuner/` is a good template.

## How aggregation works

`TimingRecorder.aggregate()` returns per-component summary statistics:

```python
{
    "my_component": {
        "n": 3.0,
        "mean": 0.42,
        "std": 0.07,    # statistics.pstdev — 0.0 when n == 1
        "min": 0.35,
        "max": 0.50,
        "total": 1.26,
    },
    ...
}
```

`std` is the **population** standard deviation (`statistics.pstdev`), not the sample standard deviation. The intent is descriptive (range across observed runs), not inferential (estimating a hidden distribution), so `pstdev` is the right choice.

Empty recorders aggregate to `{}`.

`TimingRecorder.to_dict(include_summary=True)` emits `{"records": [...], "summary": aggregate()}` for JSON serialization. Pass `include_summary=False` at non-aggregating layers (per-worker, per-gen) — each component appears once at that scope, so the summary block has `n=1` and is pure noise. Use the default (`True`) at session level (`bootstrap_breakdown`, `timing_summary`) where pooling produces non-trivial aggregates.

The session-level `timing_summary` is computed by `PBTTuner._aggregate_session_timing()`, which walks `generation_history`, merges every per-worker and per-generation `timing.records` into a single recorder, and emits its `aggregate()`. This means `timing_summary` reflects *all* observations of each component across the session — typically `n = population_size × total_generations` for worker-level components.

## Algorithm-overhead mapping (PBT vs BO)

The cost decomposition has one component per algorithm that captures the *non-evaluation* work the optimizer does between evaluations. These play the same role and should be compared directly:

| Algorithm | Component | Source | What it covers |
| --- | --- | --- | --- |
| PBT | `evolve` (per-generation) | `Population.train_generation` — `execute_exploit_explore` + `env.clone_instances` | Exploit/explore decisions, per-worker config perturbation, physical PGDATA cloning of elite→poor workers |
| BO (parallel) | `bo_overhead_ask` + `bo_overhead_tell` | `BORunner._run_parallel_optimization` — explicit brackets around `facade.ask()` and `facade.tell()` | Surrogate model query (ask) + observation update (tell) for each iteration |
| BO (sequential) | `bo_overhead_seconds` (per-iteration) | `objective.py` — gap between successive `objective()` invocations | Same as parallel but measured indirectly: `t_iterN+1_start - t_iterN_end` is the time `facade.optimize()` spent on ask + tell + bookkeeping |

For a fair PBT-vs-BO cost comparison, sum:

- PBT side: `timing_summary["evolve"]["total"]`
- BO parallel: `timing_summary["bo_overhead_ask"]["total"] + timing_summary["bo_overhead_tell"]["total"]`, OR equivalently `tuning_session.bo_overhead_total_seconds`
- BO sequential: `tuning_session.bo_overhead_total_seconds` (sum of per-iteration `bo_overhead_seconds`; the last iteration's overhead is 0.0 because it has no successor — this is a known boundary effect, ≤1 iteration's worth of overhead)

The two sides are not strictly identical work — PBT pays for physical data cloning while BO pays for surrogate-model fitting — but they sit at the same level in the cost-decomposition table: *non-evaluation algorithmic overhead per iteration*.

## Reading the JSON

The full schema lives in [session-json-schema.md](session-json-schema.md#timing-instrumentation-v11). The shortest path from JSON to a per-component summary across a session:

```python
import json
data = json.loads(path.read_text())
for component, stats in data["timing_summary"].items():
    print(f"{component:24s} n={stats['n']:.0f} mean={stats['mean']:.3f}s "
          f"std={stats['std']:.3f}s total={stats['total']:.2f}s")
```

For per-generation or per-worker drill-down, walk `data["generation_history"][i]["timing"]` and `data["generation_history"][i]["worker_scores"][j]["timing"]` respectively. The bootstrap line items are at `data["bootstrap_breakdown"]`.

## Schema compatibility

When you add a new component:

- **You do not need to bump `timing_schema_version`.** Component names are an open vocabulary inside the existing schema. The version bumps for *structural* changes (new fields, semantic redefinition of an existing component), not for new entries in the `summary` dict.
- **You do need to update the component reference table** in [session-json-schema.md](session-json-schema.md#component-reference).
- **You do need to handle older sessions in any consumer** that depends on the component being present — `data["timing_summary"].get("my_component")` returns `None` for sessions written before the bracket existed.

If you find yourself wanting to change the semantics of an existing component name (e.g. `workload` is split into `warmup` + `measurement`), do **not** redefine the existing name. Add new components alongside and update consumers to prefer the new ones when both are present. This keeps older sessions interpretable.
