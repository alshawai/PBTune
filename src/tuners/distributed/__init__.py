# Copyright (C) 2026 Ibrahim Al-Shawa and PBTune contributors
# Licensed under the GNU General Public License v3.0
# See LICENSE file for details

"""
Distributed Multi-Device Tuning
===============================

A NEW execution mode (alongside the existing single-device / ``local`` mode)
in which every population worker runs on its own dedicated physical device.

Motivation — *fairness*: in single-device mode all workers share one machine,
so the B1–B17 lockstep barriers exist only to equalise noisy-neighbour
contention. With one worker per identical, dedicated device there is no
co-tenancy at all, so fairness becomes *structural* rather than something the
algorithm has to fight for.

Topology
--------
- **Coordinator** (control plane, one process): runs the unchanged PBT
  algorithm (:class:`~src.tuners.pbt.population.Population`, evolution,
  central :class:`CompositeScorer`) and talks to devices only through a
  :class:`~src.tuners.distributed.remote_environment.RemoteEnvironment` that
  implements the existing ``DatabaseEnvironment`` ABC.
- **Device agent** (one per device): a long-running HTTP/JSON server that owns
  exactly one local PostgreSQL instance and runs today's ``WorkloadOrchestrator``
  pipeline locally, next to its database, returning raw metrics.

This package is entirely additive — importing it has no effect on the local
mode, and the local code paths are never modified.
"""

from src.tuners.distributed.inventory import (
    DeviceSpec,
    FleetInventory,
    load_inventory,
)

__all__ = [
    "DeviceSpec",
    "FleetInventory",
    "load_inventory",
]

AGENT_PROTOCOL_VERSION = "1.0"
"""Wire-protocol version. Coordinator and agent must agree on the major
version; a mismatch is a hard error surfaced by the /health handshake."""
