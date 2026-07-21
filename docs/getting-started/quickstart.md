# Quickstart


See also: [setup](setup.md), [overview](../architecture/overview.md), [guides/evaluation-runbook](../guides/evaluation-runbook.md)

This guide walks you through your first PBT tuning session and explains what's happening at each step. Targeted at someone who has just run `setup.md` successfully and wants to confirm the system works.

**Time required:** about 5 minutes for a minimal run on modern hardware.

**What you'll learn:** how to launch a tuning run, where the artefacts go, and how to read the session JSON without parsing it programmatically.

---

## Prerequisites

You have:

- Run [setup](setup.md) successfully (`.venv` active, `.env` configured, sysbench 1.1.0 in `PATH`).
- Either Docker running (recommended) or a local PostgreSQL 14+ accepting connections.

Verify:

```bash
source .venv/bin/activate
python -m src.tuners pbt --help     # should print the CLI help
docker info >/dev/null && echo OK   # should print "OK"
```

If `docker info` fails, the run will automatically fall back to bare-metal PostgreSQL with a reduced-isolation warning. That's fine for a first quickstart.

## Step 1 — Launch the smallest possible PBT run

```bash
python -m src.tuners pbt \
    --tier minimal \
    --config rapid \
    --population 2 \
    --generations 5
```

What this does, line by line:

| Argument | Effect |
| --- | --- |
| `--tier minimal` | Tunes only the 5 highest-impact knobs (`shared_buffers`, `effective_cache_size`, `work_mem`, `random_page_cost`, `max_parallel_workers_per_gather`). The other tiers (`core`, `standard`, `extensive`) are larger search spaces with longer runtimes. |
| `--config rapid` | A pre-configured `PBTConfig` profile with short evaluation durations. The other profiles (`standard`, `thorough`) are slower but produce stronger results. |
| `--population 2` | Two workers in parallel. Each gets its own PostgreSQL instance on its own port (5440, 5441). |
| `--generations 5` | Maximum five generations. The session may stop earlier if convergence or early-stopping triggers. |

The CLI prints a startup banner and begins evaluating. On a typical laptop you should see the first generation complete within roughly a minute.

## Step 2 — What's happening during a generation

The terminal output is colour-coded; the same content goes to an HTML log under `results/`. A generation looks like this:

```text
═══════ Generation 1 / 5 ═══════
  ▶ Worker 0  |  applying config (B2)
  ▶ Worker 1  |  applying config (B2)
  ▶ Worker 0  |  config_applied — wait barrier (B2)
  ▶ Worker 1  |  config_applied — wait barrier (B2)
  ▶ Worker 0  |  measurement window 30s (B9)
  ▶ Worker 1  |  measurement window 30s (B9)
  ...
  ▶ Worker 0  |  score: 0.642  (lat_p95=43ms tps=1820 mem=0.31)
  ▶ Worker 1  |  score: 0.715  (lat_p95=39ms tps=1980 mem=0.35)
  ✓ Generation 1 complete  best=0.715 mean=0.679 std=0.052
```

The `B2`, `B9` etc. markers are the lockstep [generation barriers](../architecture/generation-barriers.md). Every worker waits at every marker until all workers have arrived — that's how the framework guarantees the workers' measurement windows overlap, eliminating scheduling artefacts from the score.

If you only see one worker, the population is too small for barriers to do anything useful — that's fine for a quickstart but always run `--population >= 4` for a real campaign.

## Step 3 — Find the session artefacts

When the run finishes (or hits convergence), the CLI prints the output paths:

```text
✓ Session complete — best score 0.842 (Worker 1, generation 4)
  JSON:  results/oltp/oltp_read_write/pbt_runs/minimal/tuning_sessions/pbt_results_20260607_1842.json
  HTML:  results/oltp/oltp_read_write/pbt_runs/minimal/logs/pbt_session_20260607_1842.html
```

The HTML log is the easiest way to inspect what happened — it's the same colour-coded transcript you saw on the terminal, but persistent and shareable.

The JSON is the canonical artefact. Open it in any JSON viewer:

```bash
python -m json.tool results/oltp/oltp_read_write/pbt_runs/minimal/tuning_sessions/pbt_results_*.json | less
```

Look for these top-level keys:

| Key | What it tells you |
| --- | --- |
| `tuning_session.benchmark` / `workload_type` / `seed` | What the session was, deterministically. |
| `tuning_session.scoring_policy` / `scoring_policy_version` | Which scoring formula evaluated this session. |
| `best_configuration.knobs` | The configuration that won. **This is the actionable output.** |
| `best_configuration.score_breakdown` | Resolved weights + per-metric utilities + reliability gate. Read this to understand *why* the winning config won. |
| `generation_history[]` | Per-generation snapshot — best/mean/std scores, exploit count, per-worker metrics. |
| `system_info`, `worker_resources` | Reproducibility metadata: host CPU/RAM, per-worker resource slice, Python and PostgreSQL versions. |

The schema is documented exhaustively in [reference/session-json-schema](../reference/session-json-schema.md).

## Step 4 — Verify the tuned config beats the default

A tuning session by itself only tells you which configurations PBT explored — it doesn't tell you whether the winner is *statistically* better than the PostgreSQL default under controlled conditions. The post-hoc evaluation suite answers that:

```bash
python -m src.evaluation \
    --session results/oltp/oltp_read_write/pbt_runs/minimal/tuning_sessions/pbt_results_<timestamp>.json \
    --repetitions 5
```

This runs the default and tuned configurations against the same workload with paired seeds, then computes Wilcoxon, bootstrap CI, and Cohen's d on the differences. The output JSON in `results/oltp/oltp_read_write/comparisons/minimal/` is what you'd cite in a paper or pull request.

For the runbook including all flags, scoring-policy overrides, and reproducibility checklist, see [guides/evaluation-runbook](../guides/evaluation-runbook.md).

## Common first-run hiccups

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `Docker unavailable, falling back to Bare Metal` warning | Docker daemon not running or not reachable. | Start Docker, or accept reduced isolation (fine for development; not for publication). |
| `connection refused` on port 5440 | A previous session left an instance behind. | `python -m src.scripts.cleanup_instances` |
| `DB_PASSWORD environment variable is required` | `.env` not loaded or missing the variable. | Re-check [setup §3](setup.md#3-install-dependencies); confirm the `.env` exists in the project root. |
| `sysbench: command not found` | sysbench 1.1.0 not installed (the prepackaged 1.0.20 is **not sufficient**). | Follow [setup §Sysbench](setup.md). |
| First generation takes far longer than later ones | TPC-H `dbgen` is compiling and generating data the first time. | Expected — only happens once per scale factor. |

## Where to go next

- **Run something more realistic.** `--tier core --config standard --population 4 --generations 30` is a typical configuration. Expect ~15-20 minutes on modern hardware.
- **Tune for your own workload.** Author a JSON template — see [guides/adding-workloads](../guides/adding-workloads.md).
- **Compare PBT against a Bayesian-Optimisation baseline.** See [guides/bo-baseline](../guides/bo-baseline.md) and [guides/pbt-vs-bo-comparison](../guides/pbt-vs-bo-comparison.md).
- **Understand what just happened.** Read [architecture/overview](../architecture/overview.md), then drill down into specific subsystems.
