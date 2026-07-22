# Distributed Multi-Device Tuning

> Status: coordinator, device agent, remote environment, SSH bootstrap, and CLI
> wiring implemented (Phases 0–5). Runs against a real fleet require identical
> devices reachable over a trusted network with SSH-key access.

## Why this mode exists — fairness

In the default **`local`** mode every population worker runs as a co-tenant
PostgreSQL instance on one machine. The [B1–B17 lockstep barriers](generation-barriers.md)
exist specifically to make noisy-neighbour contention *identical* across
workers — fairness by cancellation.

**`distributed`** mode assigns **one worker per dedicated, identical device**.
With no co-tenancy there is no contention to cancel, so fairness becomes
**structural**:

1. **No co-tenancy** — one worker per device.
2. **Identical hardware** — a config's score reflects the config, not the host.
3. **Byte-identical start state** — each device holds its own base snapshot and
   resets to it before every evaluation.
4. **Local benchmark client** — the benchmark runs on the device next to its DB,
   so no network latency ever enters the measurement window.

The existing `local` code paths are never modified; distributed mode is entirely
additive and selected with `--distributed`.

## Topology

```
┌─────────────────────────────────────────────┐
│  COORDINATOR (control plane, 1 process)       │
│  Population loop · evolution (exploit/explore) │
│  central CompositeScorer · generation barrier  │
│  RemoteEnvironment ── HTTP/JSON RPC ──┐        │
└───────────────────────────────────────┼───────┘
         ┌───────────────┬───────────────┼───────────────┐
         ▼               ▼               ▼               ▼
    ┌─────────┐     ┌─────────┐     ┌─────────┐     ┌─────────┐
    │ Device 0│     │ Device 1│     │ Device 2│ …   │ Device N│
    │ agent   │     │ agent   │     │ agent   │     │ agent   │
    │ +1 PG   │     │ +1 PG   │     │ +1 PG   │     │ +1 PG   │
    │ +bench  │     │ +bench  │     │ +bench  │     │ +bench  │
    └─────────┘     └─────────┘     └─────────┘     └─────────┘
```

- **Coordinator** runs the *unchanged* PBT algorithm. It only swaps two
  components: `env` → `RemoteEnvironment` and `orchestrator` →
  `RemoteWorkloadOrchestrator`. `Population`, `evolution`, and the scorer are
  untouched.
- **Device agent** (`src/tuners/distributed/device_agent.py`) is a long-running
  HTTP/JSON server that owns one local PostgreSQL instance and runs today's real
  `WorkloadOrchestrator` pipeline locally, returning **raw metrics**.
- **Scoring is central.** Devices return `PerformanceMetrics`; the coordinator
  scores with `engine.compute_breakdown`, so adaptive normalisation spans the
  whole population exactly as in local mode.

Because there is no co-tenancy, distributed runs set
`synchronize_workers=False`: the B1–B17 substep barriers run *locally* on each
device, and the coordinator's only synchronisation point is the generation
boundary (the `ThreadPoolExecutor` join in `Population.evaluate_generation`).

## Wire protocol

JSON over HTTP (stdlib only — no web framework), defined in
`src/tuners/distributed/agent_api.py`:

| Route | Purpose |
|---|---|
| `GET /health` | Liveness + protocol/version + hardware handshake |
| `POST /setup` | Stand up the device's single local PG instance |
| `POST /snapshot` | Create the device's local base snapshot |
| `POST /reset` | Restore data to the local base snapshot (exploit target) |
| `POST /run_eval` | Run one apply→run→measure locally; return raw metrics |
| `POST /cleanup` | Tear down the instance |
| `POST /shutdown` | Stop the agent |

## Config-only exploit clone

PBT *exploit* copies an elite worker onto poor workers. Across devices this is
**config-only**: the elite's knobs are copied in coordinator RAM by the
evolution step (tiny), and `RemoteEnvironment.clone_instances` merely tells each
target device to `/reset` its data to the byte-identical local baseline. **No
gigabyte-scale PGDATA is transferred over the network.**

## Fleet inventory

Devices are described by a `devices.yaml` (see
`configs/distributed/devices.example.yaml`). Devices are bound to workers by
list order (device 0 → worker 0). The fleet must have at least `--population`
devices.

```yaml
fleet:
  agent_port: 8770
  ssh_user: pbt
  ssh_key: ~/.ssh/id_rsa
  data_dir: /var/lib/pbt
  python: python3
devices:
  - host: 10.0.0.11
  - host: 10.0.0.12
  - host: 10.0.0.13
  - host: 10.0.0.14
```

## Bootstrap (SSH)

`src/tuners/distributed/bootstrap.py` provisions each device: `rsync` the repo,
optionally `pip install -r requirements.txt`, then launch the agent detached
(`nohup`, recording a pidfile + logfile). Teardown stops each agent via its
pidfile. Command construction is pure/unit-tested; execution is a thin
`subprocess` wrapper.

## Usage

```bash
# Distributed run (tool bootstraps the fleet, then tunes)
python -m src.tuners.pbt --distributed \
    --inventory configs/distributed/devices.example.yaml \
    --benchmark sysbench --tier core --config standard --population 4

# Agents already running (skip SSH bootstrap)
python -m src.tuners.pbt --distributed --inventory devices.yaml --no-bootstrap ...

# Run a device agent by hand (for debugging on one box)
python -m src.tuners.distributed.device_agent --worker-id 0 --port 8770 \
    --knob-tier core --base-dir ./.instances
```

Relevant flags: `--distributed`, `--inventory`, `--no-bootstrap`,
`--no-remote-deps`, `--eval-timeout`, `--agent-timeout`.

## Fault handling

A `run_eval` RPC timeout or transport error is treated as a dead worker: the
worker gets a failure metric and is handed to the standard population rescue
path (resample / config-clone). Recovery re-runs `/setup` on the device.

## Assumptions & current limitations

- **Identical hardware fleet.** Heterogeneous fleets are out of scope. Note the
  coordinator itself can be any light machine: agents report their detected
  `WorkerResources` from `/setup` and the coordinator resolves hardware-aware
  knob ranges against **device** hardware (see `_resolve_device_hardware_ranges`
  in `src/tuners/pbt/tuner.py`), not its own. `--worker-ram`/`--worker-cpus` remain a manual
  override if no device reports resources.
- **Version-based knob pruning is skipped** in distributed mode (the coordinator
  has no direct TCP path to remote instances); it relies on the identical-fleet
  assumption that every device runs the same PostgreSQL.
- **Synchronized measurement windows** are approximated by an agent-side
  "don't start before epoch" gate; a full two-phase prepare→go protocol (to
  align the *measurement* sub-window rather than the eval start) is future work.
- **`LocalDeviceBackend`** wires `sysbench` and `tpch`; custom-workload wiring
  is a marked `NotImplementedError`.
- Trusted network + SSH-key access; secrets pass via SSH, never the repo.

## Code map

| File | Role |
|---|---|
| `src/tuners/distributed/inventory.py` | Parse/validate `devices.yaml` |
| `src/tuners/distributed/config.py` | `ExecutionMode`, `DistributedConfig` |
| `src/tuners/distributed/agent_api.py` | Wire schemas |
| `src/tuners/distributed/transport.py` | Stdlib HTTP client + server helpers |
| `src/tuners/distributed/device_agent.py` | Device HTTP server + `LocalDeviceBackend` |
| `src/tuners/distributed/remote_environment.py` | `DatabaseEnvironment` RPC proxy |
| `src/tuners/distributed/remote_orchestrator.py` | RPC eval + central scoring |
| `src/tuners/distributed/coordinator.py` | Client mgmt, health handshake, factories |
| `src/tuners/distributed/bootstrap.py` | SSH provisioning + agent launch/teardown |
