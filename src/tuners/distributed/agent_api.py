# Copyright (C) 2026 Ibrahim Al-Shawa and PBTune contributors
# Licensed under the GNU General Public License v3.0
# See LICENSE file for details

"""
Device-Agent Wire Protocol
==========================

JSON request/response schemas exchanged between the **coordinator** and each
**device agent**. Kept dependency-free (plain dataclasses + ``to_dict`` /
``from_dict``) so both sides can (de)serialise with the stdlib ``json`` module —
matching this repo's minimal-dependency philosophy (no web framework).

Endpoints (all POST unless noted), rooted at ``{agent_base_url}``:

======================  ================================================
Route                   Payload  ->  Result
======================  ================================================
``GET /health``         --                    -> :class:`HealthResponse`
``POST /setup``         :class:`SetupRequest`  -> :class:`SetupResponse`
``POST /snapshot``      --                    -> :class:`SnapshotResponse`
``POST /reset``         :class:`ResetRequest`  -> :class:`AckResponse`
``POST /run_eval``      :class:`RunEvalRequest`-> :class:`RunEvalResponse`
``POST /cleanup``       :class:`CleanupRequest`-> :class:`AckResponse`
``POST /shutdown``      --                    -> :class:`AckResponse`
======================  ================================================

On any handler error the agent replies with a non-2xx status and an
:class:`ErrorResponse` body.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


# --------------------------------------------------------------------------- #
# Generic envelopes
# --------------------------------------------------------------------------- #
@dataclass
class ErrorResponse:
    """Uniform error body returned with any non-2xx agent response."""

    error: str
    detail: Optional[str] = None
    worker_id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ErrorResponse":
        return cls(
            error=d.get("error", "unknown error"),
            detail=d.get("detail"),
            worker_id=d.get("worker_id"),
        )


@dataclass
class AckResponse:
    """Simple success acknowledgement for side-effecting endpoints."""

    ok: bool = True
    detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AckResponse":
        return cls(ok=bool(d.get("ok", False)), detail=d.get("detail"))


# --------------------------------------------------------------------------- #
# /health
# --------------------------------------------------------------------------- #
@dataclass
class HealthResponse:
    """Liveness + capability handshake for a device agent."""

    status: str  # "ok" | "starting" | "error"
    protocol_version: str
    agent_version: str
    worker_id: int
    pg_running: bool = False
    pg_server_version: Optional[str] = None
    backend: Optional[str] = None  # "docker" | "bare_metal"
    hardware: Dict[str, Any] = field(default_factory=dict)
    detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "HealthResponse":
        return cls(
            status=d.get("status", "error"),
            protocol_version=d.get("protocol_version", ""),
            agent_version=d.get("agent_version", ""),
            worker_id=int(d.get("worker_id", -1)),
            pg_running=bool(d.get("pg_running", False)),
            pg_server_version=d.get("pg_server_version"),
            backend=d.get("backend"),
            hardware=d.get("hardware", {}) or {},
            detail=d.get("detail"),
        )


# --------------------------------------------------------------------------- #
# /setup
# --------------------------------------------------------------------------- #
@dataclass
class SetupRequest:
    """Instruct the agent to create/prepare its single local PG instance.

    Mirrors the parameters the local :class:`EnvironmentFactory` needs so the
    agent can stand up a functionally identical single-worker environment.
    """

    run_id: str
    benchmark: str  # "sysbench" | "tpch" | "custom"
    workload_type: str  # e.g. "oltp_read_write", "olap"
    use_docker: bool = True
    force_recreate_baseline: bool = False
    # Benchmark shaping — only the fields relevant to ``benchmark`` are honoured.
    tables: Optional[int] = None
    table_size: Optional[int] = None
    scale_factor: Optional[float] = None
    image_name: Optional[str] = None
    dbname: str = "test_dataset"
    db_user: str = "postgres"
    # Free-form extras for forward-compat without a protocol bump.
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SetupRequest":
        known = {f: d.get(f) for f in _SETUP_FIELDS if f in d}
        return cls(**known)  # type: ignore[arg-type]


_SETUP_FIELDS = {f for f in SetupRequest.__dataclass_fields__}  # noqa: C416


@dataclass
class SetupResponse:
    """Result of standing up the device's instance.

    ``resources`` carries the device's detected ``WorkerResources`` (serialised)
    so the coordinator can resolve hardware-aware knob ranges against the
    *device* hardware rather than its own — letting the coordinator run on any
    light machine.
    """

    ok: bool
    port: int
    data_dir: str
    backend: str
    detail: Optional[str] = None
    resources: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SetupResponse":
        return cls(
            ok=bool(d.get("ok", False)),
            port=int(d.get("port", 0)),
            data_dir=d.get("data_dir", ""),
            backend=d.get("backend", ""),
            detail=d.get("detail"),
            resources=d.get("resources", {}) or {},
        )


# --------------------------------------------------------------------------- #
# /snapshot
# --------------------------------------------------------------------------- #
@dataclass
class SnapshotResponse:
    """Identifier of the base snapshot the device now holds locally."""

    ok: bool
    snapshot_id: str
    detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SnapshotResponse":
        return cls(
            ok=bool(d.get("ok", False)),
            snapshot_id=d.get("snapshot_id", ""),
            detail=d.get("detail"),
        )


# --------------------------------------------------------------------------- #
# /reset  (config-only clone target: restore device to its local base snapshot)
# --------------------------------------------------------------------------- #
@dataclass
class ResetRequest:
    """Reset the device's data directory to its local base snapshot.

    This is how a distributed *exploit* lands: the elite worker's knob config
    is copied (cheaply, over the wire) and the poor worker's device resets its
    data to the byte-identical benchmark baseline it already holds — no
    gigabyte-scale PGDATA transfer across the network.
    """

    snapshot_id: str = ""  # empty => the agent's default base snapshot

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ResetRequest":
        return cls(snapshot_id=d.get("snapshot_id", ""))


# --------------------------------------------------------------------------- #
# /run_eval  (the core of the fairness story — runs entirely on-device)
# --------------------------------------------------------------------------- #
@dataclass
class RunEvalRequest:
    """Run one full apply -> run -> measure evaluation on the device.

    The benchmark client executes locally, next to the DB, so no network
    latency ever enters the measurement window.
    """

    knob_config: Dict[str, Any]
    generation: int
    apply_config: bool = True
    restore_due: bool = False
    next_eval_will_restore: bool = False
    # Optional coordinator-issued synchronised measurement start (Phase 4).
    # ``None`` => start measuring immediately after warmup.
    measurement_start_epoch: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RunEvalRequest":
        return cls(
            knob_config=d.get("knob_config", {}) or {},
            generation=int(d.get("generation", 0)),
            apply_config=bool(d.get("apply_config", True)),
            restore_due=bool(d.get("restore_due", False)),
            next_eval_will_restore=bool(d.get("next_eval_will_restore", False)),
            measurement_start_epoch=d.get("measurement_start_epoch"),
        )


@dataclass
class RunEvalResponse:
    """Raw evaluation result. Scoring happens *centrally* on the coordinator so
    adaptive metric normalisation spans the whole population identically to
    single-device mode."""

    ok: bool
    # Serialised PerformanceMetrics (see src/utils/metrics.py). None on failure.
    metrics: Optional[Dict[str, Any]] = None
    restart_occurred: bool = False
    # SHOW-verified knob values actually in effect on the instance.
    actual_config: Dict[str, Any] = field(default_factory=dict)
    # Serialised per-worker timing record (schema v1.1). Optional.
    timing: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RunEvalResponse":
        return cls(
            ok=bool(d.get("ok", False)),
            metrics=d.get("metrics"),
            restart_occurred=bool(d.get("restart_occurred", False)),
            actual_config=d.get("actual_config", {}) or {},
            timing=d.get("timing"),
            error=d.get("error"),
        )


# --------------------------------------------------------------------------- #
# /cleanup
# --------------------------------------------------------------------------- #
@dataclass
class CleanupRequest:
    remove_data: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CleanupRequest":
        return cls(remove_data=bool(d.get("remove_data", False)))


# Routes recognised by the agent HTTP server. Kept here so client and server
# share a single source of truth.
ROUTES = {
    "health": "/health",
    "setup": "/setup",
    "snapshot": "/snapshot",
    "reset": "/reset",
    "run_eval": "/run_eval",
    "cleanup": "/cleanup",
    "shutdown": "/shutdown",
}
