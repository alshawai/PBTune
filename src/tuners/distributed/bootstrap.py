# Copyright (C) 2026 Ibrahim Al-Shawa and PBTune contributors
# Licensed under the GNU General Public License v3.0
# See LICENSE file for details

"""
Fleet Bootstrap
===============

Provisions and launches the device agents over SSH (the user chose "tool
bootstraps them"). Per device the bootstrap:

1. **syncs the repository** to the device (``rsync`` over SSH),
2. optionally **installs Python dependencies** (``pip install -r requirements.txt``),
3. **launches the device agent** detached (``nohup``), recording a pidfile and
   logfile so it can be stopped later, and
4. leaves health-checking to :meth:`Coordinator.wait_for_agents`.

Teardown stops each agent via its recorded pidfile.

Design
------
Command *construction* is factored into pure functions
(:func:`rsync_command`, :func:`ssh_command`, :func:`launch_agent_command`,
:func:`stop_agent_command`) so it is unit-testable without touching the network.
Execution is a thin :class:`subprocess`-based wrapper (:class:`FleetBootstrapper`).

Assumptions (per the agreed design): a trusted LAN, SSH-key access, and a remote
``python3`` capable of importing this project. Devices are identical hardware.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import List, Optional, Sequence

from src.tuners.distributed.inventory import DeviceSpec, FleetInventory
from src.utils.logger import get_logger

LOGGER = get_logger("FleetBootstrap")

# Paths excluded from the code sync — large, host-specific, or regenerated.
DEFAULT_RSYNC_EXCLUDES = (
    ".git",
    ".venv",
    "__pycache__",
    "*.pyc",
    ".instances",
    "pg_instances",
    "results",
    "smac3_output",
    "graphify-out",
    "notebooks",
    "papers",
    ".pytest_cache",
)


@dataclass
class RemoteLayout:
    """Resolved remote paths for one device, derived from its ``data_dir``."""

    root: str  # base data dir (DeviceSpec.data_dir)
    code_dir: str  # where the repo is synced
    instances_dir: str  # PG instance data (agent --base-dir)
    log_file: str
    pid_file: str

    @classmethod
    def for_device(cls, device: DeviceSpec) -> "RemoteLayout":
        root = PurePosixPath(device.data_dir)
        return cls(
            root=str(root),
            code_dir=str(root / "code"),
            instances_dir=str(root / "instances"),
            log_file=str(root / f"agent-worker-{device.worker_id}.log"),
            pid_file=str(root / f"agent-worker-{device.worker_id}.pid"),
        )


# --------------------------------------------------------------------------- #
# Pure command builders (unit-testable, no I/O)
# --------------------------------------------------------------------------- #
def _ssh_target(device: DeviceSpec) -> str:
    return f"{device.ssh_user}@{device.host}" if device.ssh_user else device.host


def _ssh_opts(device: DeviceSpec) -> List[str]:
    opts = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"]
    if device.ssh_key:
        opts += ["-i", device.ssh_key]
    return opts


def ssh_command(device: DeviceSpec, remote_cmd: str) -> List[str]:
    """Build an ``ssh ... 'remote_cmd'`` argv for a device."""
    return ["ssh", *_ssh_opts(device), _ssh_target(device), remote_cmd]


def rsync_command(
    device: DeviceSpec,
    local_dir: str,
    remote_dir: str,
    excludes: Sequence[str] = DEFAULT_RSYNC_EXCLUDES,
) -> List[str]:
    """Build an ``rsync -az --delete`` argv that syncs the repo to a device."""
    ssh_parts = " ".join(["ssh", *_ssh_opts(device)])
    local = local_dir if local_dir.endswith("/") else local_dir + "/"
    argv = ["rsync", "-az", "--delete", "-e", ssh_parts]
    for pattern in excludes:
        argv += ["--exclude", pattern]
    argv += [local, f"{_ssh_target(device)}:{remote_dir}/"]
    return argv


def launch_agent_command(
    device: DeviceSpec,
    layout: RemoteLayout,
    *,
    knob_tier: str,
    knob_source: str = "expert",
    log_level: str = "INFO",
    env_exports: Optional[dict] = None,
) -> str:
    """Build the remote shell command that launches the agent detached.

    The agent is started with ``nohup`` from the synced code dir; its PID is
    written to ``layout.pid_file`` and stdout/stderr to ``layout.log_file``.
    """
    exports = ""
    if env_exports:
        exports = (
            " ".join(f"{k}={shlex.quote(str(v))}" for k, v in env_exports.items())
            + " "
        )
    agent = (
        f"{exports}{device.python} -m src.tuners.distributed.device_agent "
        f"--worker-id {device.worker_id} "
        f"--host 0.0.0.0 --port {device.agent_port} "
        f"--knob-tier {shlex.quote(knob_tier)} "
        f"--knob-source {shlex.quote(knob_source)} "
        f"--base-dir {shlex.quote(layout.instances_dir)} "
        f"--log-level {log_level}"
    )
    # mkdir -p, cd into code, launch detached, record pid.
    return (
        f"mkdir -p {shlex.quote(layout.root)} {shlex.quote(layout.instances_dir)} && "
        f"cd {shlex.quote(layout.code_dir)} && "
        f"nohup {agent} > {shlex.quote(layout.log_file)} 2>&1 & "
        f"echo $! > {shlex.quote(layout.pid_file)}"
    )


def install_deps_command(layout: RemoteLayout, device: DeviceSpec) -> str:
    """Build the remote command that installs requirements into the device env."""
    return (
        f"cd {shlex.quote(layout.code_dir)} && "
        f"{device.python} -m pip install -q -r requirements.txt"
    )


def stop_agent_command(layout: RemoteLayout) -> str:
    """Build the remote command that stops the agent via its pidfile."""
    pid = shlex.quote(layout.pid_file)
    return (
        f"if [ -f {pid} ]; then kill \"$(cat {pid})\" 2>/dev/null || true; "
        f"rm -f {pid}; fi"
    )


# --------------------------------------------------------------------------- #
# Executor
# --------------------------------------------------------------------------- #
class BootstrapError(RuntimeError):
    """Raised when a bootstrap step fails on a device."""


class FleetBootstrapper:
    """Runs the bootstrap/teardown steps against a fleet over SSH."""

    def __init__(
        self,
        inventory: FleetInventory,
        population_size: int,
        local_repo_dir: str,
        *,
        knob_tier: str,
        knob_source: str = "expert",
        install_deps: bool = True,
        command_timeout_s: float = 900.0,
        env_exports: Optional[dict] = None,
    ):
        inventory.validate_for_population(population_size)
        self.inventory = inventory
        self.population_size = population_size
        self.local_repo_dir = local_repo_dir
        self.knob_tier = knob_tier
        self.knob_source = knob_source
        self.install_deps = install_deps
        self.command_timeout_s = command_timeout_s
        self.env_exports = env_exports or {}
        self.devices: List[DeviceSpec] = [
            inventory.device_for_worker(wid) for wid in range(population_size)
        ]

    # -- low-level runner ------------------------------------------------- #
    def _run(self, argv: Sequence[str], *, device: DeviceSpec, step: str) -> None:
        LOGGER.info("[%s] %s: %s", device.display_name, step, " ".join(argv))
        try:
            result = subprocess.run(
                list(argv),
                capture_output=True,
                text=True,
                timeout=self.command_timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise BootstrapError(
                f"{device.display_name}: {step} timed out after {self.command_timeout_s}s"
            ) from exc
        if result.returncode != 0:
            raise BootstrapError(
                f"{device.display_name}: {step} failed (rc={result.returncode})\n"
                f"stdout: {result.stdout.strip()}\nstderr: {result.stderr.strip()}"
            )

    # -- per-device steps ------------------------------------------------- #
    def bootstrap_device(self, device: DeviceSpec) -> None:
        layout = RemoteLayout.for_device(device)
        # Ensure the code dir exists, then sync.
        self._run(
            ssh_command(device, f"mkdir -p {shlex.quote(layout.code_dir)}"),
            device=device,
            step="mkdir code dir",
        )
        self._run(
            rsync_command(device, self.local_repo_dir, layout.code_dir),
            device=device,
            step="sync code",
        )
        if self.install_deps:
            self._run(
                ssh_command(device, install_deps_command(layout, device)),
                device=device,
                step="install deps",
            )
        # Stop any stale agent, then launch fresh.
        self._run(
            ssh_command(device, stop_agent_command(layout)),
            device=device,
            step="stop stale agent",
        )
        self._run(
            ssh_command(
                device,
                launch_agent_command(
                    device,
                    layout,
                    knob_tier=self.knob_tier,
                    knob_source=self.knob_source,
                    env_exports=self.env_exports,
                ),
            ),
            device=device,
            step="launch agent",
        )

    def teardown_device(self, device: DeviceSpec) -> None:
        layout = RemoteLayout.for_device(device)
        self._run(
            ssh_command(device, stop_agent_command(layout)),
            device=device,
            step="stop agent",
        )

    # -- fleet-wide ------------------------------------------------------- #
    def bootstrap_all(self) -> None:
        LOGGER.info("Bootstrapping %d device(s)...", len(self.devices))
        for device in self.devices:
            self.bootstrap_device(device)
        LOGGER.info("Bootstrap complete; agents launching in the background.")

    def teardown_all(self) -> None:
        for device in self.devices:
            try:
                self.teardown_device(device)
            except BootstrapError as exc:
                LOGGER.warning("Teardown issue: %s", exc)
