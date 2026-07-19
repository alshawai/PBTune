# Copyright (C) 2026 Ibrahim Al-Shawa and PBTune contributors
# Licensed under the GNU General Public License v3.0
# See LICENSE file for details

"""
Fleet Inventory
===============

Parses the ``devices.yaml`` inventory that describes the physical device fleet
used by distributed tuning. Each device hosts exactly one PostgreSQL instance
and is mapped to exactly one population worker (by list order).

Example ``devices.yaml``
------------------------

.. code-block:: yaml

    # Fleet-wide defaults (each device may override any of these).
    fleet:
      agent_port: 8770          # HTTP port the device agent listens on
      ssh_user: pbt             # SSH user used for bootstrap (Phase 3)
      ssh_key: ~/.ssh/id_rsa    # SSH private key path
      data_dir: /var/lib/pbt    # remote base dir for PG instance data
      python: python3           # remote interpreter used to launch the agent

    devices:
      - host: 10.0.0.11
      - host: 10.0.0.12
        agent_port: 8771        # per-device override
      - host: 10.0.0.13
        ssh_user: ubuntu

Design notes
------------
- ``worker_id`` is assigned implicitly from list order (0, 1, 2, …) so a device's
  identity is stable across a run without the user having to number them.
- Validation is intentionally strict and fails fast: a malformed inventory is a
  configuration error the operator should fix before a (potentially long) run.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


class InventoryError(ValueError):
    """Raised when a ``devices.yaml`` inventory is missing or malformed."""


# Fleet-level default keys and their built-in fallbacks. Per-device entries may
# override any of these; anything absent everywhere falls back to these values.
_FLEET_DEFAULTS: Dict[str, Any] = {
    "agent_port": 8770,
    "ssh_user": None,
    "ssh_key": None,
    "data_dir": "/var/lib/pbt",
    "python": "python3",
    "agent_scheme": "http",
}

# Keys a per-device entry is allowed to carry (besides the mandatory ``host``).
_DEVICE_OVERRIDE_KEYS = frozenset(
    {"agent_port", "ssh_user", "ssh_key", "data_dir", "python", "agent_scheme", "label"}
)


@dataclass
class DeviceSpec:
    """A single device in the fleet, bound to one worker.

    Attributes
    ----------
    worker_id:
        Population worker index this device is responsible for (list order).
    host:
        Hostname or IP the coordinator uses to reach the device agent (and,
        transitively, its PostgreSQL instance).
    agent_port:
        TCP port the device's HTTP agent listens on.
    ssh_user, ssh_key:
        Credentials used by the Phase 3 bootstrap to provision/launch the agent
        over SSH. Unused by the coordinator's runtime RPC path.
    data_dir:
        Remote base directory under which the agent stores the instance's data.
    python:
        Remote interpreter used to launch the agent during bootstrap.
    agent_scheme:
        ``http`` (default) or ``https``.
    label:
        Optional human-friendly name for logs/reports (defaults to ``host``).
    """

    worker_id: int
    host: str
    agent_port: int = 8770
    ssh_user: Optional[str] = None
    ssh_key: Optional[str] = None
    data_dir: str = "/var/lib/pbt"
    python: str = "python3"
    agent_scheme: str = "http"
    label: Optional[str] = None

    @property
    def display_name(self) -> str:
        """Human-friendly identifier for logs and reports."""
        return self.label or self.host

    @property
    def agent_base_url(self) -> str:
        """Base URL of the device agent, e.g. ``http://10.0.0.11:8770``."""
        return f"{self.agent_scheme}://{self.host}:{self.agent_port}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "host": self.host,
            "agent_port": self.agent_port,
            "ssh_user": self.ssh_user,
            "ssh_key": self.ssh_key,
            "data_dir": self.data_dir,
            "python": self.python,
            "agent_scheme": self.agent_scheme,
            "label": self.label,
        }


@dataclass
class FleetInventory:
    """An ordered fleet of devices, one per worker."""

    devices: List[DeviceSpec] = field(default_factory=list)
    source_path: Optional[Path] = None

    def __len__(self) -> int:
        return len(self.devices)

    @property
    def size(self) -> int:
        """Number of devices == maximum supported population size."""
        return len(self.devices)

    def device_for_worker(self, worker_id: int) -> DeviceSpec:
        """Return the device bound to ``worker_id``.

        Raises
        ------
        InventoryError
            If ``worker_id`` has no device (population larger than the fleet).
        """
        if worker_id < 0 or worker_id >= len(self.devices):
            raise InventoryError(
                f"No device for worker_id={worker_id}; fleet has "
                f"{len(self.devices)} device(s) (valid worker ids 0..{len(self.devices) - 1})"
            )
        return self.devices[worker_id]

    def validate_for_population(self, population_size: int) -> None:
        """Ensure the fleet can host ``population_size`` workers.

        One worker per device is a hard requirement of distributed mode
        (fairness: no co-tenancy). The fleet may be *larger* than the
        population — extra devices are simply unused.

        Raises
        ------
        InventoryError
            If there are fewer devices than workers.
        """
        if population_size > len(self.devices):
            raise InventoryError(
                f"Distributed mode needs one device per worker: population_size="
                f"{population_size} but the inventory only lists {len(self.devices)} "
                f"device(s). Add more devices to {self.source_path or 'the inventory'} "
                f"or reduce --population."
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_path": str(self.source_path) if self.source_path else None,
            "devices": [d.to_dict() for d in self.devices],
        }


def _coerce_agent_port(value: Any, *, context: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise InventoryError(f"{context}: agent_port must be an integer, got {value!r}") from exc
    if not (1 <= port <= 65535):
        raise InventoryError(f"{context}: agent_port {port} is out of range 1..65535")
    return port


def _expand_key_path(value: Optional[str]) -> Optional[str]:
    """Expand ``~`` and env vars in an SSH key path, leaving None untouched."""
    if value is None:
        return None
    return os.path.expandvars(os.path.expanduser(str(value)))


def parse_inventory(raw: Dict[str, Any], *, source_path: Optional[Path] = None) -> FleetInventory:
    """Build a :class:`FleetInventory` from an already-loaded YAML mapping.

    Separated from :func:`load_inventory` so it can be unit-tested without disk.
    """
    if not isinstance(raw, dict):
        raise InventoryError("Inventory root must be a mapping with a 'devices' key")

    fleet_raw = raw.get("fleet", {}) or {}
    if not isinstance(fleet_raw, dict):
        raise InventoryError("'fleet' section must be a mapping")

    unknown_fleet = set(fleet_raw) - set(_FLEET_DEFAULTS)
    if unknown_fleet:
        raise InventoryError(
            f"Unknown key(s) in 'fleet' section: {sorted(unknown_fleet)}. "
            f"Allowed: {sorted(_FLEET_DEFAULTS)}"
        )

    defaults = dict(_FLEET_DEFAULTS)
    defaults.update(fleet_raw)

    devices_raw = raw.get("devices")
    if not isinstance(devices_raw, list) or not devices_raw:
        raise InventoryError("'devices' must be a non-empty list")

    seen_endpoints: set[tuple[str, int]] = set()
    devices: List[DeviceSpec] = []
    for worker_id, entry in enumerate(devices_raw):
        if not isinstance(entry, dict):
            raise InventoryError(
                f"devices[{worker_id}] must be a mapping (got {type(entry).__name__})"
            )
        host = entry.get("host")
        if not host or not isinstance(host, str):
            raise InventoryError(f"devices[{worker_id}] is missing a valid 'host'")

        unknown = set(entry) - _DEVICE_OVERRIDE_KEYS - {"host"}
        if unknown:
            raise InventoryError(
                f"devices[{worker_id}] ({host}) has unknown key(s): {sorted(unknown)}. "
                f"Allowed: {sorted(_DEVICE_OVERRIDE_KEYS | {'host'})}"
            )

        merged = {k: entry.get(k, defaults.get(k)) for k in _DEVICE_OVERRIDE_KEYS}
        agent_port = _coerce_agent_port(
            merged["agent_port"], context=f"devices[{worker_id}] ({host})"
        )

        endpoint = (host, agent_port)
        if endpoint in seen_endpoints:
            raise InventoryError(
                f"Duplicate device endpoint {host}:{agent_port} — each device must "
                f"expose a unique host:agent_port"
            )
        seen_endpoints.add(endpoint)

        devices.append(
            DeviceSpec(
                worker_id=worker_id,
                host=host,
                agent_port=agent_port,
                ssh_user=merged["ssh_user"],
                ssh_key=_expand_key_path(merged["ssh_key"]),
                data_dir=str(merged["data_dir"]),
                python=str(merged["python"]),
                agent_scheme=str(merged["agent_scheme"]),
                label=merged.get("label"),
            )
        )

    return FleetInventory(devices=devices, source_path=source_path)


def load_inventory(path: str | os.PathLike[str]) -> FleetInventory:
    """Load and validate a ``devices.yaml`` inventory from disk.

    Raises
    ------
    InventoryError
        If the file is missing, unreadable, or malformed.
    """
    p = Path(path).expanduser()
    if not p.is_file():
        raise InventoryError(f"Inventory file not found: {p}")
    try:
        with p.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise InventoryError(f"Failed to parse YAML inventory {p}: {exc}") from exc
    if raw is None:
        raise InventoryError(f"Inventory file {p} is empty")
    return parse_inventory(raw, source_path=p)
