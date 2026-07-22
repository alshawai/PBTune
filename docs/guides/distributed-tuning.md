# Distributed Multi-Device Tuning — Complete Guide

A practical, end-to-end reference for the **distributed** execution mode: what
was built, how the pieces fit, how to run it against a device fleet, and how to
test it. For the design rationale and diagrams see the architecture reference,
[distributed-tuning.md](../architecture/distributed-tuning.md).

---

## 1. What this feature does

By default (`local` mode) every population worker runs as a **co-tenant**
PostgreSQL instance on one machine. Distributed mode instead places **one worker
on its own dedicated device**, so measurements are free of noisy-neighbour
contention. Fairness becomes *structural* rather than something the algorithm
has to correct for.

**Goal (per requirements): 100% fair tuning.** Four properties deliver it:

1. **No co-tenancy** — one worker per device.
2. **Identical hardware** — a config's score reflects the config, not the host.
3. **Byte-identical start state** — each device holds its own base snapshot and
   resets to it before every evaluation.
4. **Local benchmark client** — the benchmark runs on the device next to its DB,
   so no network latency enters the measurement window.

The existing `local` code paths are **never modified**; distributed mode is
entirely additive and selected with `--distributed`.

---

## 2. Architecture at a glance

```
┌─────────────────────────────────────────────┐
│  COORDINATOR (control plane, one process)     │
│  Population loop · evolution · central scorer  │
│  RemoteEnvironment  ── HTTP/JSON RPC ──┐       │
│  RemoteWorkloadOrchestrator ───────────┤       │
└─────────────────────────────────────────┼──────┘
        ┌────────────┬────────────┬────────┼────────────┐
        ▼            ▼            ▼         ▼            ▼
   ┌─────────┐  ┌─────────┐  ┌─────────┐        ┌─────────┐
   │ Device 0│  │ Device 1│  │ Device 2│  …     │ Device N│
   │ agent   │  │ agent   │  │ agent   │        │ agent   │
   │ +1 PG   │  │ +1 PG   │  │ +1 PG   │        │ +1 PG   │
   │ +bench  │  │ +bench  │  │ +bench  │        │ +bench  │
   └─────────┘  └─────────┘  └─────────┘        └─────────┘
```

- The **coordinator** runs the *unchanged* PBT algorithm. It only swaps two
  objects: `env` → `RemoteEnvironment`, `orchestrator` →
  `RemoteWorkloadOrchestrator`. `Population`, `evolution`, and the `CompositeScorer`
  are untouched.
- Each **device agent** is a long-running HTTP/JSON server that owns one local
  PostgreSQL instance and runs today's real `WorkloadOrchestrator` pipeline
  locally, returning **raw metrics**.
- **Scoring is central**: devices return `PerformanceMetrics`; the coordinator
  computes the score with the same `engine.compute_breakdown` call local mode
  uses, so adaptive normalisation spans the whole population identically.
- Distributed runs set `synchronize_workers=False`: the B1–B17 substep barriers
  run *locally* on each device; the coordinator's only sync point is the
  generation boundary.

---

## 3. Code inventory — what we changed and added

### 3.1 New package: `src/tuners/distributed/`

| File | Responsibility |
|---|---|
| `__init__.py` | Package docstring, re-exports, `AGENT_PROTOCOL_VERSION` |
| `inventory.py` | Parse/validate `devices.yaml`; `DeviceSpec`, `FleetInventory`, `load_inventory`, `parse_inventory`; binds devices→workers by list order |
| `config.py` | `ExecutionMode` (LOCAL/DISTRIBUTED) enum + `DistributedConfig` (timeouts, inventory) |
| `agent_api.py` | JSON wire schemas: `SetupRequest/Response`, `RunEvalRequest/Response`, `HealthResponse`, `ResetRequest`, `SnapshotResponse`, `CleanupRequest`, `AckResponse`, `ErrorResponse`, `ROUTES` |
| `transport.py` | Stdlib HTTP client `AgentClient` + server helpers `read_json_body`/`write_json`; `AgentRPCError` |
| `device_agent.py` | Device HTTP server (`DeviceAgent`), request handler, `EvaluationBackend` seam, real `LocalDeviceBackend`, `python -m` CLI |
| `remote_environment.py` | `RemoteEnvironment(DatabaseEnvironment)` — proxies lifecycle to agents; **config-only clone**; fan-out snapshot |
| `remote_orchestrator.py` | `RemoteWorkloadOrchestrator(WorkloadOrchestrator)` — RPC eval + **central scoring**; `metrics_from_dict` |
| `coordinator.py` | `Coordinator` — builds clients from inventory, health handshake + protocol check, factories for env/orchestrator, fleet shutdown |
| `bootstrap.py` | `FleetBootstrapper` + pure command builders (`rsync_command`, `ssh_command`, `launch_agent_command`, `install_deps_command`, `stop_agent_command`, `RemoteLayout`) |

### 3.2 Edits to existing files (additive, backward-compatible)

| File | Change |
|---|---|
| `src/utils/environments/base.py` | Added defaulted `host: str = "127.0.0.1"` to `InstanceConfig` |
| `src/tuners/pbt/population.py` | Worker binding reads `getattr(instance, "host", "127.0.0.1")` instead of a hardcoded literal — local mode still resolves to loopback |
| `src/tuners/pbt/tuner.py` | New `--distributed`/`--inventory`/… args; `_build_distributed_stack()` + `_start_distributed_fleet()`; distributed branch in `__init__`; bootstrap/health in `run()`; fleet shutdown in cleanup. All new `run()` references are `getattr`-guarded so partial-construction tests still pass |

### 3.3 Supporting files

| Path | Purpose |
|---|---|
| `configs/distributed/devices.example.yaml` | Example fleet inventory |
| `docs/architecture/distributed-tuning.md` | Design reference |
| `docs/guides/distributed-tuning.md` | This guide |
| `tests/unit/tuners/distributed/` | Test suite (see §7) |

---

## 4. The wire protocol

JSON over HTTP (stdlib only — no web framework), rooted at each agent's
`http://<host>:<agent_port>`:

| Method & Route | Request → Response | Purpose |
|---|---|---|
| `GET /health` | — → `HealthResponse` | Liveness + protocol version + hardware handshake |
| `POST /setup` | `SetupRequest` → `SetupResponse` | Stand up the device's single local PG instance |
| `POST /snapshot` | — → `SnapshotResponse` | Create the device's local base snapshot |
| `POST /reset` | `ResetRequest` → `AckResponse` | Restore data to the local base snapshot (exploit target) |
| `POST /run_eval` | `RunEvalRequest` → `RunEvalResponse` | Run one apply→run→measure locally; return raw metrics |
| `POST /cleanup` | `CleanupRequest` → `AckResponse` | Tear down the instance |
| `POST /shutdown` | — → `AckResponse` | Stop the agent |

On any error the agent returns a non-2xx status with an `ErrorResponse` body.
An RPC timeout or transport error is treated by the coordinator as a **dead
worker** — it gets a failure metric and is handed to the standard population
rescue path (resample / config-clone).

### Config-only exploit clone

PBT *exploit* copies an elite worker onto poor ones. Across devices this is
**config-only**: the elite's knobs are copied in coordinator RAM by the
evolution step (tiny), and `RemoteEnvironment.clone_instances` merely tells each
target device to `/reset` its data to the byte-identical local baseline. **No
gigabyte-scale PGDATA moves over the network.**

---

## 5. Prerequisites

**Coordinator machine**
- The repo + Python env (this project's `requirements.txt`).
- Network reachability to each device's `agent_port`.
- For bootstrap: `ssh` and `rsync` on PATH, plus SSH-key access to the devices.

**Each device (identical hardware recommended)**
- A Python 3.11 interpreter able to import this project.
- Docker (default backend) or a local PostgreSQL for `--no-docker`.
- For sysbench/TPC-H: the benchmark tooling the local mode already needs.
- `DB_PASSWORD` available to the agent process (the bootstrap exports it; the
  agent reads `os.getenv("DB_PASSWORD", "")`).

> **Note on dependencies.** `src/__init__.py` eagerly imports the analysis chain,
> so the agent process needs the full environment (including `shap`) present on
> each device — the bootstrap's `pip install -r requirements.txt` handles this
> unless you pass `--no-remote-deps`.

---

## 6. How to run

### 6.1 Fleet inventory (`devices.yaml`)

Devices bind to workers by **list order** (device 0 → worker 0). The fleet must
have at least `--population` devices. See
`configs/distributed/devices.example.yaml`.

```yaml
# Fleet-wide defaults (each device may override any of these)
fleet:
  agent_port: 8770          # HTTP port each device agent listens on
  ssh_user: pbt             # SSH user for bootstrap
  ssh_key: ~/.ssh/id_rsa    # SSH private key (~ and $VARS expanded)
  data_dir: /var/lib/pbt    # remote base dir for the instance's data
  python: python3           # remote interpreter used to launch the agent

devices:
  - host: 10.0.0.11         # -> worker 0
  - host: 10.0.0.12         # -> worker 1
    agent_port: 8771        # per-device override
  - host: 10.0.0.13         # -> worker 2
    ssh_user: ubuntu
  - host: 10.0.0.14         # -> worker 3
    label: rack-a-node-4    # optional friendly name for logs
```

### 6.2 Full run (tool bootstraps the fleet, then tunes)

```bash
python -m src.tuners.pbt --distributed \
    --inventory configs/distributed/devices.example.yaml \
    --benchmark sysbench --tier core --config standard --population 4
```

What happens, in order:
1. `__init__` builds the coordinator + `RemoteEnvironment` +
   `RemoteWorkloadOrchestrator` (no agents contacted yet) and sets
   `synchronize_workers=False`.
2. `run()` **bootstraps** each device over SSH (rsync repo → optional
   `pip install` → launch agent detached), then **waits for health** (with a
   protocol-version handshake).
3. `setup_instances` fans `/setup` out to every agent; `create_snapshot` fans
   `/snapshot` out so every device holds its baseline.
4. The normal PBT loop runs; each `evaluate_worker` is an RPC to the owning
   device, scored centrally. Exploit uses config-only clone.
5. On exit, the coordinator `/shutdown`s the agents (and the bootstrap tears
   down launched processes).

### 6.3 Agents already running (skip SSH bootstrap)

```bash
python -m src.tuners.pbt --distributed --inventory devices.yaml --no-bootstrap \
    --benchmark sysbench --tier core --config standard --population 4
```

### 6.4 Coordinator CLI flags

| Flag | Default | Meaning |
|---|---|---|
| `--distributed` | off | Enable distributed mode (requires `--inventory`) |
| `--inventory PATH` | — | Path to `devices.yaml` |
| `--no-bootstrap` | off | Assume agents already running; skip SSH bootstrap |
| `--no-remote-deps` | off | During bootstrap, skip `pip install -r requirements.txt` |
| `--eval-timeout SECS` | 1800 | Per-worker `run_eval` RPC timeout |
| `--agent-timeout SECS` | 60 | Per-agent control RPC timeout |

All existing local-mode flags (`--tier`, `--config`, `--population`,
`--benchmark`, `--generations`, …) apply unchanged.

### 6.5 Running a single device agent by hand

Useful for debugging one device, or for the manual smoke test in §7.3.

```bash
python -m src.tuners.distributed.device_agent \
    --worker-id 0 --host 0.0.0.0 --port 8770 \
    --knob-tier core --knob-source expert \
    --base-dir ./.instances --log-level INFO
```

Agent CLI flags: `--worker-id` (required), `--host`, `--port`, `--knob-tier`,
`--knob-source`, `--base-dir`, `--log-level`.

---

## 7. How to test

All commands assume the project virtualenv is active:

```bash
source .venv/bin/activate
```

> If test collection fails with `ModuleNotFoundError: No module named 'shap'`,
> install the declared deps (`pip install shap ruff`) — `src/__init__.py`
> imports the analysis chain at import time, which the whole suite depends on.

### 7.1 The distributed test suite (no Docker/PG needed)

```bash
python -m pytest tests/unit/tuners/distributed -q
```

Expect **27 passing**. The suite has three files:

| File | Covers |
|---|---|
| `test_inventory.py` | `devices.yaml` parsing: defaults/overrides, worker binding, validation errors (missing host, duplicate endpoint, bad port, unknown keys), SSH-key expansion |
| `test_rpc_roundtrip.py` | End-to-end: spins up **two real agent HTTP servers on localhost** backed by an in-memory `FakeBackend`, then drives the full coordinator stack — health handshake, `setup`/`snapshot` fan-out, `run_eval` with **central scoring** (higher throughput ⇒ ≥ score), **config-only clone** (only the target resets), and RPC-failure → dead-worker fallback |
| `test_bootstrap.py` | Pure SSH command builders: `RemoteLayout` paths, `ssh`/`rsync` argv (key, target, excludes, trailing slashes), agent launch command (worker/port/base-dir, `nohup`, pidfile), env exports, install-deps, stop-via-pidfile |

The RPC round-trip test is the key integration proof: it exercises transport,
dispatch, `RemoteEnvironment`, and `RemoteWorkloadOrchestrator` exactly as a
real fleet would — **without Docker or PostgreSQL**.

### 7.2 Regression check (existing suites)

Confirms the `InstanceConfig`/`population.py`/`tuner.py` edits didn't break local
mode:

```bash
python -m pytest tests/unit/tuners -q
```

Expect **150 passing**.

### 7.3 Manual single-device smoke test (real DB)

On a machine with Docker (or `--no-docker` + local PG):

```bash
# Terminal 1 — start an agent
source .venv/bin/activate
python -m src.tuners.distributed.device_agent --worker-id 0 --port 8770 \
    --knob-tier core --base-dir ./.instances

# Terminal 2 — talk to it
curl -s localhost:8770/health | python -m json.tool

curl -s -X POST localhost:8770/setup -H 'Content-Type: application/json' \
  -d '{"run_id":"smoke","benchmark":"sysbench","workload_type":"oltp_read_write",
       "tables":4,"table_size":10000}' | python -m json.tool

curl -s -X POST localhost:8770/snapshot | python -m json.tool

curl -s -X POST localhost:8770/run_eval -H 'Content-Type: application/json' \
  -d '{"knob_config":{"shared_buffers":"256MB"},"generation":0}' | python -m json.tool

curl -s -X POST localhost:8770/shutdown | python -m json.tool
```

### 7.4 Lint

```bash
python -m ruff check src/tuners/distributed/ tests/unit/tuners/distributed/
```

---

## 8. Fault handling

- **Agent unreachable / RPC timeout** → the worker is marked dead (failure
  metric) and enters the standard rescue path (resample or config-clone).
  Recovery re-runs `/setup` on the device.
- **Health/protocol mismatch at startup** → `wait_for_agents` fails fast with the
  offending workers listed, before any long run begins.
- **Cleanup** always attempts `/shutdown` on every agent (and bootstrap
  teardown) so devices are left clean for the next run.

---

## 9. Assumptions & current limitations

- **Identical hardware fleet.** Heterogeneous fleets are out of scope. The
  **coordinator can be any light machine**: each agent reports its detected
  `WorkerResources` from `/setup`, and the coordinator resolves hardware-aware
  knob ranges against **device** hardware (identical fleet ⇒ one device is
  representative). `--worker-ram`/`--worker-cpus` are the manual override if no
  device reports resources.
- **Version-based knob pruning is skipped** in distributed mode — the coordinator
  has no direct TCP path to the remote instances, so it relies on the
  identical-fleet assumption (every device runs the same PostgreSQL).
- **Synchronized measurement windows** are approximated by an agent-side
  "don't start before epoch" gate (the `measurement_start_epoch` field). A full
  two-phase prepare→go protocol that aligns the *measurement sub-window* (rather
  than the eval start) would require splitting the shared
  `WorkloadOrchestrator.evaluate_worker` and is deferred as future work.
- **`LocalDeviceBackend`** wires `sysbench` and `tpch`; custom-workload wiring is
  a clearly-marked `NotImplementedError`.
- **Trusted network + SSH-key access.** Secrets pass via SSH/env, never the repo.

---

## 10. Extending the feature

- **Custom workloads on devices** — implement the `else` branch in
  `LocalDeviceBackend.setup` (`device_agent.py`) mirroring the PBT tuner's
  custom-executor wiring.
- **True synchronized measurement** — split the agent's `run_eval` into
  `prepare` (through warmup) and `measure` (on a coordinator "go"), and add a
  distributed barrier in the coordinator between the two phases.
- **New RPC endpoints** — add the schema to `agent_api.py`, a route to `ROUTES`,
  a handler branch in `device_agent._dispatch_post`, and a client call.

---

## 11. Quick reference

```bash
# Run distributed (bootstrap + tune)
python -m src.tuners.pbt --distributed --inventory configs/distributed/devices.example.yaml \
    --benchmark sysbench --tier core --config standard --population 4

# Run distributed against already-running agents
python -m src.tuners.pbt --distributed --inventory devices.yaml --no-bootstrap ...

# Start one agent by hand
python -m src.tuners.distributed.device_agent --worker-id 0 --port 8770 --knob-tier core

# Test everything (no Docker needed)
python -m pytest tests/unit/tuners/distributed -q          # 27 tests
python -m pytest tests/unit/tuners -q   # 150 tests
python -m ruff check src/tuners/distributed/
```
