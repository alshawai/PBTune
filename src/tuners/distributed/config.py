# Copyright (C) 2026 Ibrahim Al-Shawa and PBTune contributors
# Licensed under the GNU General Public License v3.0
# See LICENSE file for details

"""
Distributed Execution Configuration
===================================

Defines the ``ExecutionMode`` selector and the ``DistributedConfig`` that
parameterises a distributed run. Kept in the distributed package (rather than
``tuner_config.py``) so that enabling this feature is purely additive: the
existing single-device configuration is untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.tuners.distributed.inventory import FleetInventory, load_inventory


class ExecutionMode(str, Enum):
    """How the population is physically executed.

    ``LOCAL``
        The existing behaviour: all workers run as co-tenant PostgreSQL
        instances on a single machine (ports 5440+). This is the default and
        is never altered by the distributed feature.
    ``DISTRIBUTED``
        One worker per dedicated device; the coordinator drives device agents
        over HTTP/JSON RPC.
    """

    LOCAL = "local"
    DISTRIBUTED = "distributed"


@dataclass
class DistributedConfig:
    """Runtime settings for :class:`ExecutionMode.DISTRIBUTED`.

    Attributes
    ----------
    inventory:
        The parsed device fleet (one device per worker).
    request_timeout_s:
        Per-RPC timeout for ordinary control calls (setup, reset, health).
    eval_timeout_s:
        Timeout for a ``run_eval`` RPC. Must comfortably exceed
        warmup + measurement + restart time; a breach marks the worker dead
        and hands it to the standard rescue path.
    health_poll_interval_s, health_timeout_s:
        Polling cadence / overall deadline when waiting for agents to become
        healthy after (bootstrap and) setup.
    synchronized_measurement:
        When True (Phase 4), the coordinator issues a common measurement-start
        epoch so every device begins its timed window at the same wall-clock —
        an extra fairness guard against any shared infrastructure. Ignored
        until the Phase 4 barrier lands.
    """

    inventory: FleetInventory
    request_timeout_s: float = 60.0
    eval_timeout_s: float = 1800.0
    health_poll_interval_s: float = 2.0
    health_timeout_s: float = 120.0
    synchronized_measurement: bool = False

    @classmethod
    def from_inventory_path(cls, path: str, **kwargs: object) -> "DistributedConfig":
        """Build a config by loading and validating ``devices.yaml`` from disk."""
        inventory = load_inventory(path)
        return cls(inventory=inventory, **kwargs)  # type: ignore[arg-type]

    def validate_for_population(self, population_size: int) -> None:
        self.inventory.validate_for_population(population_size)
