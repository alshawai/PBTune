import atexit
import fnmatch
import json
import logging
import shlex
import signal
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from scripts.experiments.experiment_matrix import Experiment
from scripts.experiments.run_state import (
    CampaignFileLock,
    RunnerIdentity,
    atomic_write_json,
    utc_now,
)
from src.config.data_root import resolve_data_root
from src.tuners.distributed.bootstrap import (
    install_deps_command,
    RemoteLayout,
    rsync_command,
    ssh_command,
    stop_agent_command,
)
from src.tuners.distributed.config import ExecutionMode
from src.tuners.distributed.inventory import DeviceSpec, load_inventory
from src.utils.hardware_info import detect_worker_resources, _resolve_block_device_node
from src.tuners.pbt.config import (
    RAPID_CONFIG,
    STANDARD_CONFIG,
    THOROUGH_CONFIG,
    RESEARCH_CONFIG,
)

# Per-experiment parallel-worker resolution must mirror what the PBT CLI
# (``src.tuners.pbt``) will actually use as ``num_parallel_workers`` -- that is
# the denominator ``detect_worker_resources`` divides host capacity by. The CLI
# uses ``--parallel-workers`` when supplied, else the selected config profile's
# default. We reproduce that mapping here from the canonical profile configs so
# resource budgets cannot drift from the real run (the previous code hardcoded
# 8, which silently mis-sized any experiment whose effective width was not 8).
_PROFILE_PARALLEL_WORKERS = {
    "rapid": RAPID_CONFIG.num_parallel_workers,
    "standard": STANDARD_CONFIG.num_parallel_workers,
    "thorough": THOROUGH_CONFIG.num_parallel_workers,
    "research": RESEARCH_CONFIG.num_parallel_workers,
}
# Fallback when a profile name is unknown (defensive; the matrix only uses
# "thorough" and "rapid" today).
_DEFAULT_PARALLEL_WORKERS = THOROUGH_CONFIG.num_parallel_workers

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS_DIR = PROJECT_ROOT / "results"
DEFAULT_MANIFEST_DIR = RESULTS_DIR / "manifests"
LEGACY_MANIFEST_PATH = RESULTS_DIR / "experiment_manifest.json"
# Back-compat alias: legacy callers (e.g. __main__'s --status) import
# MANIFEST_PATH directly. The runner no longer writes here by default.
MANIFEST_PATH = LEGACY_MANIFEST_PATH

# Results repo (a separate git repo at RESULTS_DIR) version-control settings.
RESULTS_BRANCH = "main"
# Remote to push results to when the branch has no configured upstream. The
# actual remote is resolved per-run from the branch's upstream (``@{u}``), so
# machines whose results remote is named something other than "origin" (e.g.
# "main") still push correctly; this is only the last-resort fallback.
DEFAULT_RESULTS_REMOTE = "origin"
# Bounded retry for the multi-VM push race: when a peer commit lands between
# our pull and our push, the push is rejected (non-fast-forward); we re-pull
# with rebase and try again up to this many times.
PUSH_RETRIES = 3
STASH_MSG = "pbtune-autostash"
GCP_STOP_RETRIES = 3
GCP_STOP_VERIFY_POLLS = 12
GCP_STOP_VERIFY_DELAY_S = 5.0

LOGGER = logging.getLogger("ExperimentRunner")


def _empty_manifest() -> dict:
    return {
        "started_at": utc_now(),
        "runs": {},
    }


@dataclass(frozen=True)
class RemoteArtifactSpec:
    """Expected remote result produced by one BO or EVAL attempt."""

    phase: str
    output_dir: str
    pattern: str
    marker_path: str
    receipt_path: str

    def to_dict(self) -> dict[str, str]:
        """Serialize the artifact contract into the phase manifest."""
        return {
            "phase": self.phase,
            "output_dir": self.output_dir,
            "pattern": self.pattern,
            "marker_path": self.marker_path,
            "receipt_path": self.receipt_path,
        }


class ExperimentRunner:
    def __init__(
        self,
        dry_run: bool = False,
        no_push: bool = False,
        manifest_dir: Path | None = None,
        manifest_path: Path | None = None,
        execution_mode: ExecutionMode | str = ExecutionMode.LOCAL,
        inventory: Path | None = None,
        bootstrap: bool = True,
        remote_install_deps: bool = True,
        eval_timeout: float = 1800.0,
        agent_timeout: float = 60.0,
        comparison_worker_id: int = 0,
        stop_gcp_after_campaign: bool = False,
    ):
        """Run experiments and persist progress to per-experiment manifests.

        Parameters
        ----------
        manifest_dir
            Directory holding one ``<experiment_id>.json`` per experiment.
            Defaults to ``results/manifests/``.
        manifest_path
            Explicit single-file override. When set, every experiment
            shares this file (legacy single-manifest behavior). Takes
            precedence over ``manifest_dir``.
        """
        self.dry_run = dry_run
        self.no_push = no_push
        self.manifest_dir = manifest_dir or DEFAULT_MANIFEST_DIR
        self.manifest_path_override = manifest_path
        self.execution_mode = ExecutionMode(execution_mode)
        self.inventory = inventory.expanduser() if inventory is not None else None
        self.bootstrap = bootstrap
        self.remote_install_deps = remote_install_deps
        self.eval_timeout = eval_timeout
        self.agent_timeout = agent_timeout
        self.comparison_worker_id = comparison_worker_id
        self.stop_gcp_after_campaign = stop_gcp_after_campaign
        self._fleet_inventory = None
        self._comparison_code_synced = False
        self._runner_identity = RunnerIdentity.create()
        self._campaign_lock = CampaignFileLock(
            PROJECT_ROOT / ".agent_work" / "experiment-runner.lock",
            self._runner_identity,
        )
        self._campaign_lock_depth = 0
        self._active_phase_key: str | None = None
        self._gcp_stop_completed = False

        if self.execution_mode is ExecutionMode.DISTRIBUTED and self.inventory is None:
            raise ValueError("Distributed execution requires a fleet inventory path")
        if self.eval_timeout <= 0 or self.agent_timeout <= 0:
            raise ValueError("Distributed RPC timeouts must be positive")
        if self.comparison_worker_id < 0:
            raise ValueError("Comparison worker ID must be non-negative")
        if self.stop_gcp_after_campaign and not self.distributed:
            raise ValueError(
                "GCP fleet shutdown is only available in distributed mode"
            )

        # Active experiment's manifest, populated by run_experiment().
        self._active_manifest_path: Path | None = None
        self._active_manifest: dict = _empty_manifest()

        # Read-only cross-manifest index for warm-start lookups (a source
        # experiment may live in a different manifest file, possibly
        # written by a peer machine and pulled via git).
        self._cross_manifest_index = self._build_cross_manifest_index()
        
        # Per-experiment worker-resource flags are computed lazily in
        # ``_worker_resource_flags(exp)`` (the parallel-worker denominator
        # depends on the experiment), and memoised here keyed by that count.
        self._worker_flag_cache: dict[int, tuple[str, int]] = {}
        
        # Set up logging
        if not LOGGER.handlers:
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s [%(levelname)s] %(message)s"
            )

    @contextmanager
    def _exclusive_runner_session(self) -> Iterator[None]:
        """Prevent concurrent campaign processes from sharing the fleet."""
        if self.dry_run:
            yield
            return

        if self._campaign_lock_depth:
            self._campaign_lock_depth += 1
            try:
                yield
            finally:
                self._campaign_lock_depth -= 1
            return

        with self._campaign_lock.held():
            self._campaign_lock_depth = 1
            try:
                yield
            finally:
                self._campaign_lock_depth = 0

    def _effective_parallel_workers(self, exp: Experiment) -> int:
        """Resolve the parallel-worker count the PBT run will actually use.

        Mirrors ``src.tuners.pbt``: an explicit ``parallel_workers`` on the
        experiment wins; otherwise the config profile's default applies. This
        is the denominator host capacity is divided by, so it must match the
        real run or per-worker budgets are wrong.
        """
        if exp.parallel_workers is not None:
            return max(1, int(exp.parallel_workers))
        return _PROFILE_PARALLEL_WORKERS.get(
            exp.config_profile, _DEFAULT_PARALLEL_WORKERS
        )

    def _worker_resource_flags(self, exp: Experiment) -> tuple[str, int]:
        """Return ``(worker_ram, worker_cpus)`` CLI flag values for ``exp``.

        Per-worker budgets are host capacity (at 95%) divided by the
        experiment's effective parallel-worker count. Memoised by that count so
        repeated experiments of the same width don't re-probe the host.
        """
        n = self._effective_parallel_workers(exp)
        cached = self._worker_flag_cache.get(n)
        if cached is not None:
            return cached
        resources = detect_worker_resources(max_parallel_workers=n, threshold=0.95)
        worker_ram_mb = resources.ram_bytes // (1024 * 1024)
        flags = (f"{worker_ram_mb}M", max(1, resources.cpu_cores))
        self._worker_flag_cache[n] = flags
        LOGGER.info(
            "Experiment %s: per-worker resources for %d parallel workers -> "
            "%s RAM, %d CPUs",
            exp.id,
            n,
            flags[0],
            flags[1],
        )
        return flags

    @property
    def distributed(self) -> bool:
        """Whether PBT workers execute on the remote device fleet."""
        return self.execution_mode is ExecutionMode.DISTRIBUTED

    def _distributed_pbt_flags(self) -> list[str]:
        """Build remote-agent CLI flags for a distributed PBT command."""
        if not self.distributed:
            return []
        assert self.inventory is not None
        flags = [
            "--distributed",
            "--inventory",
            str(self.inventory),
            "--eval-timeout",
            str(self.eval_timeout),
            "--agent-timeout",
            str(self.agent_timeout),
        ]
        if not self.bootstrap:
            flags.append("--no-bootstrap")
        if not self.remote_install_deps:
            flags.append("--no-remote-deps")
        return flags

    def _load_fleet_inventory(self):
        """Load the configured fleet once for comparison-phase SSH operations."""
        if self._fleet_inventory is None:
            assert self.inventory is not None
            self._fleet_inventory = load_inventory(self.inventory)
        return self._fleet_inventory

    def _comparison_device(self) -> DeviceSpec:
        """Return the sole fleet device used for BO and post-hoc evaluation."""
        return self._load_fleet_inventory().device_for_worker(
            self.comparison_worker_id
        )

    @staticmethod
    def _ssh_transport_options(device: DeviceSpec) -> list[str]:
        """Build shared non-interactive SSH options for rsync transfers."""
        options = [
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ServerAliveCountMax=6",
        ]
        if device.ssh_key:
            options.extend(["-i", device.ssh_key])
        return options

    @staticmethod
    def _ssh_target(device: DeviceSpec) -> str:
        """Return an SSH target using the inventory's optional user."""
        return (
            f"{device.ssh_user}@{device.host}"
            if device.ssh_user
            else device.host
        )

    def _run_remote_command(
        self,
        device: DeviceSpec,
        cmd: list[str],
        artifact: RemoteArtifactSpec | None = None,
    ) -> bool:
        """Execute a remote phase, receipt its artifact, and clean PostgreSQL."""
        if not cmd or cmd[0] != "python":
            raise ValueError("Remote experiment commands must begin with 'python'")
        layout = RemoteLayout.for_device(device)
        remote_argv = [device.python, *cmd[1:]]
        cleanup_argv = [
            device.python,
            "-m",
            "src.scripts.cleanup_instances",
            "--data-dir",
            layout.instances_dir,
            "--force",
            "--docker-only",
        ]
        cleanup_cmd = shlex.join(cleanup_argv)
        artifact_setup = ""
        artifact_receipt = ""
        if artifact is not None:
            attempt_dir = str(PurePosixPath(artifact.marker_path).parent)
            artifact_setup = (
                f"mkdir -p {shlex.quote(attempt_dir)}; "
                f"rm -f {shlex.quote(artifact.receipt_path)}; "
                f"touch {shlex.quote(artifact.marker_path)}; "
            )
            # GNU find is available on the Linux fleet. The marker makes the
            # receipt specific to this attempt even when older traces exist.
            artifact_receipt = (
                "if [ \"$phase_exit_code\" -eq 0 ]; then "
                "artifact_path=\"$(find "
                f"{shlex.quote(artifact.output_dir)} -type f "
                f"-name {shlex.quote(artifact.pattern)} "
                f"-newer {shlex.quote(artifact.marker_path)} "
                "-printf '%T@ %p\\n' 2>/dev/null | sort -nr | "
                "head -n 1 | cut -d' ' -f2-)\"; "
                "if [ -z \"$artifact_path\" ]; then "
                "echo 'Successful phase produced no expected artifact' >&2; "
                "phase_exit_code=74; "
                "else "
                f"printf '%s\\n' \"$artifact_path\" > "
                f"{shlex.quote(artifact.receipt_path)}.tmp; "
                f"mv {shlex.quote(artifact.receipt_path)}.tmp "
                f"{shlex.quote(artifact.receipt_path)}; "
                "fi; fi; "
            )
        remote_cmd = (
            f"cd {shlex.quote(layout.code_dir)} || exit $?; "
            f"{artifact_setup}"
            f"cleanup_comparison_instance() {{ {cleanup_cmd}; }}; "
            "trap cleanup_comparison_instance EXIT; "
            f"{shlex.join(remote_argv)}; phase_exit_code=$?; "
            f"{artifact_receipt}"
            "trap - EXIT; cleanup_comparison_instance; cleanup_exit_code=$?; "
            "if [ \"$phase_exit_code\" -ne 0 ]; then exit \"$phase_exit_code\"; fi; "
            "exit \"$cleanup_exit_code\""
        )
        return self._run_command(ssh_command(device, remote_cmd))

    def _stage_file_on_comparison_device(self, local_path: Path) -> str:
        """Upload one coordinator artifact while preserving its project path."""
        device = self._comparison_device()
        layout = RemoteLayout.for_device(device)
        try:
            relative = local_path.resolve().relative_to(PROJECT_ROOT.resolve())
        except ValueError as exc:
            raise ValueError(
                f"Comparison artifact must live under {PROJECT_ROOT}: {local_path}"
            ) from exc

        remote_path = str(PurePosixPath(layout.code_dir) / relative.as_posix())
        parent = str(PurePosixPath(remote_path).parent)
        if not self._run_command(
            ssh_command(device, f"mkdir -p {shlex.quote(parent)}")
        ):
            raise RuntimeError(
                f"Could not create comparison artifact directory on {device.display_name}"
            )

        ssh_shell = shlex.join(
            ["ssh", *self._ssh_transport_options(device)]
        )
        upload = [
            "rsync",
            "-az",
            "-e",
            ssh_shell,
            str(local_path),
            f"{self._ssh_target(device)}:{remote_path}",
        ]
        if not self._run_command(upload):
            raise RuntimeError(
                f"Could not upload {local_path} to {device.display_name}"
            )
        return remote_path

    def _sync_comparison_results(self) -> None:
        """Download BO/evaluation artifacts from the selected device."""
        device = self._comparison_device()
        layout = RemoteLayout.for_device(device)
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        ssh_shell = shlex.join(
            ["ssh", *self._ssh_transport_options(device)]
        )
        download = [
            "rsync",
            "-az",
            "-e",
            ssh_shell,
            f"{self._ssh_target(device)}:{layout.code_dir}/results/",
            f"{RESULTS_DIR}/",
        ]
        if not self._run_command(download):
            raise RuntimeError(
                f"Could not download comparison results from {device.display_name}"
            )

    def _sync_comparison_output(self, spec: RemoteArtifactSpec) -> None:
        """Download only one attempt's canonical phase output subtree."""
        device = self._comparison_device()
        layout = RemoteLayout.for_device(device)
        remote_dir = PurePosixPath(spec.output_dir)
        try:
            relative = remote_dir.relative_to(PurePosixPath(layout.code_dir))
        except ValueError as exc:
            raise RuntimeError(
                f"Remote output directory escaped code root: {spec.output_dir}"
            ) from exc
        if not relative.parts or relative.parts[0] != "results":
            raise RuntimeError(
                f"Remote output directory is outside results/: {spec.output_dir}"
            )

        local_dir = PROJECT_ROOT / Path(*relative.parts)
        local_dir.mkdir(parents=True, exist_ok=True)
        ssh_shell = shlex.join(["ssh", *self._ssh_transport_options(device)])
        download = [
            "rsync",
            "-az",
            "-e",
            ssh_shell,
            f"{self._ssh_target(device)}:{spec.output_dir}/",
            f"{local_dir}/",
        ]
        if not self._run_command(download):
            raise RuntimeError(
                f"Could not download {spec.phase} output from {device.display_name}"
            )

    def _remote_artifact_spec(
        self,
        exp: Experiment,
        phase: str,
        attempt_id: str,
    ) -> RemoteArtifactSpec:
        """Build the remote artifact contract for one comparison attempt."""
        if phase not in {"bo", "eval"}:
            raise ValueError(f"Remote reconciliation is unsupported for {phase!r}")

        layout = RemoteLayout.for_device(self._comparison_device())
        relative_output = self._paths_to_stage(exp, phase)[-1]
        output_dir = str(
            PurePosixPath(layout.code_dir) / "results" / relative_output
        )
        pattern = "trace_*.json" if phase == "bo" else "*comparison_*.json"
        attempt_dir = PurePosixPath(layout.root) / ".pbtune-attempts"
        return RemoteArtifactSpec(
            phase=phase,
            output_dir=output_dir,
            pattern=pattern,
            marker_path=str(attempt_dir / f"{attempt_id}.started"),
            receipt_path=str(attempt_dir / f"{attempt_id}.artifact"),
        )

    def _remote_artifact_from_receipt(
        self,
        spec: RemoteArtifactSpec,
        started_at: str | None,
    ) -> str | None:
        """Resolve a receipted artifact, with a legacy timestamp fallback."""
        device = self._comparison_device()
        receipt = shlex.quote(spec.receipt_path)
        output_dir = shlex.quote(spec.output_dir)
        pattern = shlex.quote(spec.pattern)
        fallback = ""
        if started_at:
            fallback = (
                "else find "
                f"{output_dir} -type f -name {pattern} "
                f"-newermt {shlex.quote(started_at)} "
                "-printf '%T@ %p\\n' 2>/dev/null | sort -nr | "
                "head -n 1 | cut -d' ' -f2-; "
            )
        remote_cmd = (
            f"if [ -s {receipt} ]; then cat {receipt}; "
            f"{fallback}"
            "fi"
        )
        result = self._capture_command(ssh_command(device, remote_cmd))
        if result.returncode != 0:
            LOGGER.warning(
                "Could not query remote %s artifact: %s",
                spec.phase,
                (result.stderr or result.stdout).strip(),
            )
            return None
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return lines[-1] if lines else None

    def _validate_remote_artifact_path(
        self,
        remote_path: str,
        spec: RemoteArtifactSpec,
    ) -> Path:
        """Map a trusted remote result path onto its coordinator location."""
        device = self._comparison_device()
        layout = RemoteLayout.for_device(device)
        candidate = PurePosixPath(remote_path)
        expected_dir = PurePosixPath(spec.output_dir)
        try:
            candidate.relative_to(expected_dir)
            relative = candidate.relative_to(PurePosixPath(layout.code_dir))
        except ValueError as exc:
            raise RuntimeError(
                f"Remote artifact escaped its expected directory: {remote_path}"
            ) from exc
        if not fnmatch.fnmatch(candidate.name, spec.pattern):
            raise RuntimeError(
                f"Remote artifact {candidate.name!r} does not match {spec.pattern!r}"
            )
        if not relative.parts or relative.parts[0] != "results":
            raise RuntimeError(f"Remote artifact is outside results/: {remote_path}")
        return PROJECT_ROOT / Path(*relative.parts)

    @staticmethod
    def _validate_reconciled_json(path: Path, phase: str, seed: int) -> None:
        """Reject malformed or clearly mismatched comparison artifacts."""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid reconciled artifact {path}: {exc}") from exc

        recorded_seed = None
        if phase == "bo":
            recorded_seed = payload.get("tuning_session", {}).get("seed")
        elif phase == "eval":
            recorded_seed = payload.get("comparison_metadata", {}).get(
                "pair_seed_base"
            )
        if recorded_seed is not None and int(recorded_seed) != seed:
            raise RuntimeError(
                f"Reconciled {phase} artifact seed {recorded_seed} != {seed}"
            )

    def _reconcile_remote_artifact(
        self,
        exp: Experiment,
        seed: int,
        phase: str,
        run_data: dict,
    ) -> Path | None:
        """Recover a completed remote BO/EVAL result after coordinator loss."""
        if self.dry_run:
            return None
        artifact_data = run_data.get("remote_artifact") or {}
        attempt_id = str(run_data.get("attempt_id") or "legacy")
        spec = RemoteArtifactSpec(
            phase=phase,
            output_dir=str(
                artifact_data.get("output_dir")
                or self._remote_artifact_spec(exp, phase, attempt_id).output_dir
            ),
            pattern=str(
                artifact_data.get("pattern")
                or self._remote_artifact_spec(exp, phase, attempt_id).pattern
            ),
            marker_path=str(
                artifact_data.get("marker_path")
                or self._remote_artifact_spec(exp, phase, attempt_id).marker_path
            ),
            receipt_path=str(
                artifact_data.get("receipt_path")
                or self._remote_artifact_spec(exp, phase, attempt_id).receipt_path
            ),
        )
        remote_path = self._remote_artifact_from_receipt(
            spec, run_data.get("started_at")
        )
        if remote_path is None:
            return None

        local_path = self._validate_remote_artifact_path(remote_path, spec)
        self._sync_comparison_output(spec)
        if not local_path.is_file():
            raise RuntimeError(
                f"Remote artifact receipt resolved to {remote_path}, but rsync did "
                f"not create {local_path}"
            )
        self._validate_reconciled_json(local_path, phase, seed)
        return local_path

    def _sync_comparison_device_code(self) -> None:
        """Ensure BO/EVAL use current code even when manifest skips PBT.

        Ordinarily distributed PBT bootstrap performs the repository sync. A
        resumed campaign can skip PBT, however, so comparison setup must not
        assume that bootstrap ran in the current invocation.
        """
        if not self.bootstrap or self._comparison_code_synced:
            return

        device = self._comparison_device()
        layout = RemoteLayout.for_device(device)
        steps = [
            ssh_command(device, f"mkdir -p {shlex.quote(layout.code_dir)}"),
            rsync_command(device, str(PROJECT_ROOT), layout.code_dir),
        ]
        if self.remote_install_deps:
            steps.append(ssh_command(device, install_deps_command(layout, device)))

        for command in steps:
            if not self._run_command(command):
                raise RuntimeError(
                    f"Could not synchronize comparison code to {device.display_name}"
                )
        self._comparison_code_synced = True

    def _prepare_comparison_device(self) -> None:
        """Stop every PBT agent and instance before starting solo comparison."""
        inventory = self._load_fleet_inventory()
        selected = self._comparison_device()
        LOGGER.info(
            "Using worker %d (%s) exclusively for BO and EVAL; stopping %d "
            "other fleet device(s).",
            selected.worker_id,
            selected.display_name,
            max(0, len(inventory.devices) - 1),
        )
        failures: list[str] = []
        for device in inventory.devices:
            layout = RemoteLayout.for_device(device)
            # Use Docker directly here instead of the synced Python cleanup
            # module. A resumed manifest can skip PBT bootstrap, leaving an old
            # module on the worker that does not understand new cleanup flags.
            cleanup = (
                f"{stop_agent_command(layout)}; "
                "if ! container_ids=\"$(docker ps -aq "
                "--filter 'name=^/pbt-worker-' "
                "--filter 'name=^/eval-worker-')\"; then exit 1; fi; "
                "if [ -n \"$container_ids\" ]; then "
                "docker rm -f $container_ids; "
                "fi"
            )
            if not self._run_command(ssh_command(device, cleanup)):
                failures.append(device.display_name)
        if failures:
            raise RuntimeError(
                "Could not stop unused fleet device(s): " + ", ".join(failures)
            )
        self._sync_comparison_device_code()

    def _stop_gcp_fleet(self) -> None:
        """Stop and verify every worker VM after the experiment campaign."""
        if self._gcp_stop_completed:
            return
        inventory = self._load_fleet_inventory()
        missing = [
            device.display_name
            for device in inventory.devices
            if not (
                device.gcp_project
                and device.gcp_zone
                and device.gcp_instance
            )
        ]
        if missing:
            raise ValueError(
                "GCP shutdown requires gcp_project, gcp_zone, and gcp_instance "
                "for every fleet device; missing: " + ", ".join(missing)
            )

        LOGGER.info(
            "Campaign complete: stopping %d GCP worker VM(s).",
            len(inventory.devices),
        )
        shutdown = self._active_manifest.setdefault("fleet_shutdown", {})
        shutdown.update(
            requested=True,
            started_at=utc_now(),
            status="stopping",
            devices=shutdown.get("devices", {}),
        )
        self._save_manifest()
        failures: list[str] = []
        for device in inventory.devices:
            command = [
                "gcloud",
                "compute",
                "instances",
                "stop",
                str(device.gcp_instance),
                "--project",
                str(device.gcp_project),
                "--zone",
                str(device.gcp_zone),
                "--quiet",
            ]
            device_state = shutdown["devices"].setdefault(
                str(device.gcp_instance), {}
            )
            stopped = False
            for attempt in range(1, GCP_STOP_RETRIES + 1):
                current_status = self._gcp_instance_status(device)
                if current_status == "TERMINATED":
                    stopped = True
                    break
                device_state.update(
                    status="stopping",
                    attempt=attempt,
                    updated_at=utc_now(),
                )
                self._save_manifest()
                if not self._run_command(command):
                    continue
                if self.dry_run:
                    stopped = True
                    break
                for verification_poll in range(1, GCP_STOP_VERIFY_POLLS + 1):
                    current_status = self._gcp_instance_status(device)
                    device_state.update(
                        observed_status=current_status,
                        verification_poll=verification_poll,
                        updated_at=utc_now(),
                    )
                    self._save_manifest()
                    if current_status == "TERMINATED":
                        stopped = True
                        break
                    if verification_poll < GCP_STOP_VERIFY_POLLS:
                        time.sleep(GCP_STOP_VERIFY_DELAY_S)
                if stopped:
                    break

            if stopped:
                device_state.update(status="terminated", stopped_at=utc_now())
                self._save_manifest()
            else:
                device_state.update(
                    status="failed",
                    updated_at=utc_now(),
                    error="GCP did not report TERMINATED",
                )
                self._save_manifest()
                failures.append(device.display_name)
        if failures:
            shutdown.update(status="failed", finished_at=utc_now())
            self._save_manifest()
            raise RuntimeError(
                "Could not stop GCP worker VM(s): " + ", ".join(failures)
            )
        shutdown.update(status="terminated", finished_at=utc_now())
        self._gcp_stop_completed = True
        self._save_manifest()

    def _gcp_instance_status(self, device: DeviceSpec) -> str | None:
        """Return the authoritative Compute Engine status for one VM."""
        command = [
            "gcloud",
            "compute",
            "instances",
            "describe",
            str(device.gcp_instance),
            "--project",
            str(device.gcp_project),
            "--zone",
            str(device.gcp_zone),
            "--format=value(status)",
        ]
        result = self._capture_command(command)
        if result.returncode != 0:
            LOGGER.warning(
                "Could not verify GCP state for %s: %s",
                device.display_name,
                (result.stderr or result.stdout).strip(),
            )
            return None
        return result.stdout.strip().upper() or None

    @contextmanager
    def gcp_campaign_session(self):
        """Own the fleet exclusively and stop GCP VMs on every cleanable exit."""
        previous_handlers: dict[int, object] = {}

        def _interrupt(signum, _frame) -> None:
            signal_name = signal.Signals(signum).name
            raise KeyboardInterrupt(f"Received {signal_name}")

        def _stop_at_exit() -> None:
            if self.stop_gcp_after_campaign and not self._gcp_stop_completed:
                try:
                    self._stop_gcp_fleet()
                except Exception:
                    LOGGER.exception("Final GCP fleet shutdown attempt failed")

        with self._exclusive_runner_session():
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                try:
                    previous_handlers[sig] = signal.getsignal(sig)
                    signal.signal(sig, _interrupt)
                except (AttributeError, OSError, ValueError):
                    pass
            if self.stop_gcp_after_campaign:
                atexit.register(_stop_at_exit)
            active_error: BaseException | None = None
            try:
                yield
            except BaseException as exc:
                active_error = exc
                raise
            finally:
                try:
                    if self.stop_gcp_after_campaign:
                        self._stop_gcp_fleet()
                except Exception:
                    if active_error is None:
                        raise
                    LOGGER.exception(
                        "GCP fleet shutdown also failed while handling %s",
                        type(active_error).__name__,
                    )
                finally:
                    if self.stop_gcp_after_campaign:
                        atexit.unregister(_stop_at_exit)
                    for sig, previous in previous_handlers.items():
                        try:
                            signal.signal(sig, previous)  # type: ignore[arg-type]
                        except (OSError, TypeError, ValueError):
                            pass

    def _resolve_manifest_path(self, exp_id: str) -> Path:
        """Resolve the manifest path for ``exp_id``.

        Precedence: ``--manifest`` override > per-experiment derived
        path under ``manifest_dir``.
        """
        if self.manifest_path_override is not None:
            return self.manifest_path_override
        return self.manifest_dir / f"{exp_id}.json"

    def _workload_key(self, exp: Experiment) -> str:
        """Return the workload segment used by current result writers."""
        if exp.benchmark == "tpch":
            return "olap"
        if exp.benchmark == "sysbench":
            return exp.sysbench_workload or "oltp_read_write"
        raise ValueError(
            f"Cannot derive workload key for experiment {exp.id!r}: "
            f"unknown benchmark {exp.benchmark!r}"
        )

    @staticmethod
    def _tier_slug(exp: Experiment) -> str:
        """Return the tier segment used by current result writers."""
        if exp.knob_source == "data_driven":
            return f"{exp.knob_tier}@scalpel-v1"
        return exp.knob_tier

    def _paths_to_stage(self, exp: Experiment, phase: str) -> list[str]:
        """Compute phase-specific git pathspecs for one result commit.

        Returns paths relative to ``RESULTS_DIR``. Restricting to these
        avoids ``git add -A`` picking up an in-flight peer-machine write
        — the original source of merge conflicts on the results repo.

        Tuner outputs use ``sessions/<workload>/<strategy>/<tier>`` while
        post-hoc evaluations use ``comparisons/<workload>/<tier>``. Older
        layouts placed these artifacts directly under ``olap`` or ``oltp``;
        staging those obsolete roots made successful current runs report
        ``No changes to commit``.
        """
        paths: list[str] = []
        if self._active_manifest_path is not None:
            try:
                paths.append(
                    str(self._active_manifest_path.relative_to(RESULTS_DIR))
                )
            except ValueError:
                # Manifest path lives outside RESULTS_DIR (legacy override
                # pointing elsewhere). Skip — caller will still commit
                # the phase output below.
                pass

        workload = self._workload_key(exp)
        tier = self._tier_slug(exp)
        if phase in {"pbt", "bo", "lhs"}:
            path = Path("sessions") / workload / phase / tier
            if exp.ablation_variable and exp.ablation_value is not None:
                path /= Path(
                    "ablations", exp.ablation_variable, str(exp.ablation_value)
                )
        elif phase == "eval":
            path = Path("comparisons") / workload / tier
        else:
            raise ValueError(f"Unknown experiment phase: {phase!r}")
        paths.append(path.as_posix())
        return paths

    def _load_manifest(self, path: Path) -> dict:
        if path.exists():
            manifest = json.loads(path.read_text())
            if not isinstance(manifest, dict) or not isinstance(
                manifest.get("runs"), dict
            ):
                raise ValueError(f"Invalid experiment manifest structure: {path}")
            return manifest
        return _empty_manifest()

    def _save_manifest(self) -> None:
        if self.dry_run or self._active_manifest_path is None:
            return
        atomic_write_json(self._active_manifest_path, self._active_manifest)

    def _build_cross_manifest_index(self) -> dict:
        """Aggregate every manifest's ``runs`` for read-only lookups.

        Used by warm-start resolution. We never write through this
        index — the active experiment's manifest is the only file the
        runner mutates, so peer machines never collide on writes.
        """
        merged: dict = {}
        if LEGACY_MANIFEST_PATH.exists():
            try:
                legacy = json.loads(LEGACY_MANIFEST_PATH.read_text())
                merged.update(legacy.get("runs", {}))
            except (json.JSONDecodeError, OSError) as exc:
                LOGGER.warning("Could not read legacy manifest: %s", exc)
        if self.manifest_dir.exists():
            for path in sorted(self.manifest_dir.glob("*.json")):
                try:
                    data = json.loads(path.read_text())
                    merged.update(data.get("runs", {}))
                except (json.JSONDecodeError, OSError) as exc:
                    LOGGER.warning("Could not read manifest %s: %s", path, exc)
        return merged

    def _run_command(self, cmd: list[str], cwd: Path = PROJECT_ROOT) -> bool:
        if self.dry_run:
            LOGGER.info(f"DRY RUN: {' '.join(cmd)}")
            return True
        
        try:
            LOGGER.info(f"Executing: {' '.join(cmd)}")
            subprocess.run(cmd, cwd=cwd, check=True)
            return True
        except subprocess.CalledProcessError as e:
            LOGGER.error(f"Command failed with exit code {e.returncode}: {' '.join(cmd)}")
            return False

    def _capture_command(
        self, cmd: list[str], cwd: Path = PROJECT_ROOT
    ) -> subprocess.CompletedProcess[str]:
        """Run a command and retain output without raising on non-zero exit."""
        if self.dry_run:
            LOGGER.info("DRY RUN: %s", " ".join(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        try:
            return subprocess.run(
                cmd,
                cwd=cwd,
                check=False,
                text=True,
                capture_output=True,
            )
        except OSError as exc:
            return subprocess.CompletedProcess(cmd, 127, "", str(exc))

    def _git(
        self, *args: str, check: bool = True, capture: bool = False
    ) -> subprocess.CompletedProcess:
        """Run a git command in the results repo (``RESULTS_DIR``)."""
        return subprocess.run(
            ["git", *args],
            cwd=RESULTS_DIR,
            check=check,
            text=True,
            capture_output=capture,
        )

    def _resolve_remote(self) -> str:
        """Resolve the results repo's push remote.

        Prefers the remote configured as ``RESULTS_BRANCH``'s upstream
        (``git rev-parse --abbrev-ref <branch>@{u}`` → ``<remote>/<branch>``),
        so machines whose results remote is named something other than
        ``origin`` (a real case: this repo's clone names it ``main``) still
        push correctly. Falls back to :data:`DEFAULT_RESULTS_REMOTE` when the
        branch has no upstream. Cached after the first resolution.
        """
        if getattr(self, "_results_remote", None) is not None:
            return self._results_remote

        remote = DEFAULT_RESULTS_REMOTE
        upstream = self._git(
            "rev-parse", "--abbrev-ref", f"{RESULTS_BRANCH}@{{u}}",
            check=False, capture=True,
        )
        if upstream.returncode == 0 and "/" in (upstream.stdout or ""):
            # "<remote>/<branch>" → take the remote half.
            remote = upstream.stdout.strip().rsplit("/", 1)[0]
        self._results_remote: str = remote
        return remote

    def _sync_results_repo(self) -> None:
        """Integrate peer commits before staging ours: stash → pull → stash pop.

        Several VMs push to the shared results repo concurrently. Pulling on a
        dirty tree would abort the rebase, so we stash first (including
        untracked files — new result JSONs are untracked and a plain stash
        would miss them), rebase onto the remote, then restore our artifacts.
        Failures here are logged, never fatal: a phase's progress is already
        persisted in the manifest before this runs.
        """
        stash = self._git(
            "stash", "push", "--include-untracked", "-m", STASH_MSG,
            check=False, capture=True,
        )
        did_stash = (
            stash.returncode == 0
            and "No local changes to save" not in (stash.stdout or "")
        )
        try:
            pull = self._git(
                "pull", "--rebase", self._resolve_remote(), RESULTS_BRANCH,
                check=False, capture=True,
            )
            if pull.returncode != 0:
                LOGGER.warning(
                    "git pull --rebase failed: %s. Aborting any partial rebase.",
                    (pull.stderr or pull.stdout or "").strip(),
                )
                # Never leave the repo mid-rebase for the next phase.
                self._git("rebase", "--abort", check=False, capture=True)
        finally:
            if did_stash:
                pop = self._git("stash", "pop", check=False, capture=True)
                if pop.returncode != 0:
                    LOGGER.error(
                        "git stash pop hit a conflict integrating peer changes: "
                        "%s\nLocal artifacts are preserved in the stash "
                        "(`git stash list`); resolve manually.",
                        (pop.stderr or pop.stdout or "").strip(),
                    )

    def _commit_and_push(
        self, exp: Experiment, seed: int, phase: str
    ) -> tuple[str, str | None]:
        """Publish phase artifacts and return status without changing execution."""
        if self.dry_run or self.no_push:
            return ("dry_run" if self.dry_run else "disabled", None)

        try:
            # 1. Integrate peer commits first (stash → pull --rebase → pop) so
            #    our push is a fast-forward in the common case.
            self._sync_results_repo()

            # 2. Stage only this experiment's artifacts (path scoping avoids
            #    picking up an in-flight peer write).
            self._git("add", "--", *self._paths_to_stage(exp, phase))
            if self._git("diff", "--cached", "--quiet", check=False).returncode == 0:
                LOGGER.info("No changes to commit in results repo.")
                return ("no_changes", None)

            # 3. Commit locally.
            msg = f"results({exp.id}): {phase} seed={seed}"
            self._git("commit", "-m", msg)

            # 4. Push with bounded retry: a peer may land a commit between our
            #    pull and push, rejecting it. Re-integrate and retry.
            remote = self._resolve_remote()
            for attempt in range(1, PUSH_RETRIES + 1):
                push = self._git(
                    "push", remote, RESULTS_BRANCH, check=False, capture=True
                )
                if push.returncode == 0:
                    LOGGER.info("Successfully pushed %s", msg)
                    return ("published", None)
                LOGGER.warning(
                    "Push rejected (attempt %d/%d): %s. Re-pulling with rebase.",
                    attempt, PUSH_RETRIES, (push.stderr or "").strip(),
                )
                self._git(
                    "pull", "--rebase", remote, RESULTS_BRANCH,
                    check=False, capture=True,
                )
            LOGGER.error(
                "Push still failing after %d attempts; commit %r is preserved "
                "locally and will be reconciled on the next phase's sync.",
                PUSH_RETRIES, msg,
            )
            return ("failed", "Push retries exhausted")
        except (OSError, subprocess.CalledProcessError) as e:
            LOGGER.error(f"Failed to commit/push results: {e}")
            return ("failed", str(e))

    def _flush_publication_record(
        self,
        exp: Experiment,
        seed: int,
        phase: str,
    ) -> None:
        """Publish the manifest's final publication status as metadata."""
        if self.dry_run or self.no_push or self._active_manifest_path is None:
            return
        try:
            manifest_path = str(self._active_manifest_path.relative_to(RESULTS_DIR))
        except ValueError:
            return

        try:
            self._git("add", "--", manifest_path)
            if self._git("diff", "--cached", "--quiet", check=False).returncode == 0:
                return
            message = f"results({exp.id}): record {phase} publication seed={seed}"
            self._git("commit", "-m", message)
            remote = self._resolve_remote()
            for attempt in range(1, PUSH_RETRIES + 1):
                push = self._git(
                    "push", remote, RESULTS_BRANCH, check=False, capture=True
                )
                if push.returncode == 0:
                    return
                LOGGER.warning(
                    "Publication metadata push rejected (attempt %d/%d): %s",
                    attempt,
                    PUSH_RETRIES,
                    (push.stderr or "").strip(),
                )
                self._git(
                    "pull",
                    "--rebase",
                    remote,
                    RESULTS_BRANCH,
                    check=False,
                    capture=True,
                )
        except (OSError, subprocess.CalledProcessError) as exc:
            LOGGER.error("Could not publish manifest metadata: %s", exc)

    def _publish_phase(
        self,
        exp: Experiment,
        seed: int,
        phase: str,
        key: str,
    ) -> None:
        """Publish results while leaving successful execution independently done."""
        self._set_publication(key, "pending")
        status, detail = self._commit_and_push(exp, seed, phase)
        if status == "failed":
            self._set_publication(key, status, error=detail)
        else:
            self._set_publication(key, status)
        if status == "published":
            self._flush_publication_record(exp, seed, phase)

    def _resume_publication_if_needed(
        self,
        exp: Experiment,
        seed: int,
        phase: str,
        key: str,
    ) -> None:
        """Retry publication independently for an already-completed phase."""
        entry = self._active_manifest["runs"].get(key, {})
        if entry.get("status") != "done":
            return
        publication_status = entry.get("publication", {}).get("status")
        if publication_status in {"published", "disabled", "dry_run"}:
            return
        LOGGER.info(
            "Execution is complete but publication is %s; retrying publish.",
            publication_status or "unrecorded",
        )
        self._publish_phase(exp, seed, phase, key)

    def _find_latest_session_json(self, output_dir: Path, strategy: str) -> Path | None:
        """Find the most-recently-written session trace for a ``strategy``.

        Every strategy now writes a strategy-agnostic ``trace_*.json`` (the
        strategy is encoded in the ``sessions/<workload>/<strategy>/<tier>/
        traces/`` path, not the filename), so the ``/<strategy>/`` path segment
        disambiguates PBT / BO / LHS. The legacy per-strategy stem
        (``{strategy}_results_*.json``, written under the old ``<strategy>_runs/``
        layout) is still matched so pre-rename runs resolve.
        """
        if not output_dir.exists():
            return None
        candidates: list[Path] = [
            p
            for p in output_dir.rglob("trace_*.json")
            if f"/{strategy}/" in p.as_posix()
        ]
        candidates.extend(output_dir.rglob(f"{strategy}_results_*.json"))
        candidates = sorted(set(candidates), key=lambda p: p.stat().st_mtime)
        return candidates[-1] if candidates else None

    def _get_run_key(self, exp_id: str, seed: int, phase: str) -> str:
        return f"{exp_id}/seed_{seed}/{phase}"

    def _is_done(self, key: str, retry_failed: bool = False) -> bool:
        run_data = self._active_manifest["runs"].get(key, {})
        status = run_data.get("status")
        if status == "done":
            return True
        if status == "failed" and not retry_failed:
            return True
        return False

    def _start_phase(
        self,
        key: str,
        *,
        remote_artifact: RemoteArtifactSpec | None = None,
    ) -> str:
        """Create a uniquely owned attempt and mark its phase running."""
        entry = self._active_manifest["runs"].setdefault(key, {})
        previous_status = entry.get("status")
        attempts = entry.setdefault("attempts", [])
        if previous_status in {"running", "syncing"}:
            if attempts:
                attempts[-1].update(
                    status="stale",
                    finished_at=utc_now(),
                    error="Superseded after an incomplete runner invocation",
                )
            entry["status"] = "stale"

        attempt_id = f"{self._runner_identity.runner_id}-{len(attempts) + 1}"
        started_at = utc_now()
        attempt: dict = {
            "attempt_id": attempt_id,
            "number": len(attempts) + 1,
            "status": "running",
            "started_at": started_at,
            "owner": self._runner_identity.to_dict(),
        }
        if remote_artifact is not None:
            attempt["remote_artifact"] = remote_artifact.to_dict()
        attempts.append(attempt)
        for stale_field in ("finished_at", "duration_s", "error"):
            entry.pop(stale_field, None)
        entry.update(
            status="running",
            started_at=started_at,
            attempt_id=attempt_id,
            attempt_number=attempt["number"],
            owner=self._runner_identity.to_dict(),
        )
        if remote_artifact is not None:
            entry["remote_artifact"] = remote_artifact.to_dict()
        self._active_phase_key = key
        self._save_manifest()
        return attempt_id

    def _mark_status(self, key: str, status: str, **kwargs) -> None:
        if key not in self._active_manifest["runs"]:
            self._active_manifest["runs"][key] = {}
        entry = self._active_manifest["runs"][key]
        entry["status"] = status
        entry.update(kwargs)
        attempts = entry.get("attempts", [])
        if attempts:
            attempts[-1]["status"] = status
            attempts[-1].update(kwargs)
            if status in {"done", "failed", "interrupted", "stale"}:
                finished_at = kwargs.get("finished_at", utc_now())
                entry["finished_at"] = finished_at
                attempts[-1].setdefault("finished_at", finished_at)
        if status in {"done", "failed", "interrupted", "stale"}:
            self._active_phase_key = None
        self._save_manifest()

    def _mark_interrupted(self, key: str, exc: BaseException) -> None:
        """Persist interruption before propagating it to campaign cleanup."""
        self._mark_status(
            key,
            "interrupted",
            finished_at=utc_now(),
            error=str(exc) or type(exc).__name__,
        )

    def _set_publication(
        self,
        key: str,
        status: str,
        *,
        error: str | None = None,
    ) -> None:
        """Track result publication independently from phase execution."""
        entry = self._active_manifest["runs"].setdefault(key, {})
        publication = entry.setdefault("publication", {})
        publication["status"] = status
        publication["updated_at"] = utc_now()
        if error:
            publication["error"] = error
        else:
            publication.pop("error", None)
        self._save_manifest()

    def _set_remote_artifact(
        self, key: str, spec: RemoteArtifactSpec
    ) -> None:
        """Persist the artifact contract before launching a remote process."""
        entry = self._active_manifest["runs"][key]
        artifact = spec.to_dict()
        entry["remote_artifact"] = artifact
        attempts = entry.get("attempts", [])
        if attempts:
            attempts[-1]["remote_artifact"] = artifact
        self._save_manifest()

    def _try_reconcile_remote_phase(
        self,
        exp: Experiment,
        seed: int,
        phase: str,
        key: str,
        *,
        retry_failed: bool,
    ) -> Path | None:
        """Finalize abandoned remote work when its result already exists."""
        if not self.distributed:
            return None
        run_data = self._active_manifest["runs"].get(key, {})
        status = run_data.get("status")
        recoverable = status in {"running", "syncing", "interrupted"} or (
            status == "failed" and retry_failed
        )
        if not recoverable:
            return None

        artifact = self._reconcile_remote_artifact(
            exp, seed, phase, run_data
        )
        if artifact is None:
            if status in {"running", "syncing"}:
                self._mark_status(
                    key,
                    "stale",
                    finished_at=utc_now(),
                    error="No completed remote artifact found during resume",
                )
            return None

        relative = str(artifact.relative_to(PROJECT_ROOT))
        fields = {
            "finished_at": utc_now(),
            "reconciled": True,
            "artifact_json": relative,
        }
        if phase == "bo":
            fields["session_json"] = relative
        self._mark_status(key, "done", **fields)
        self._publish_phase(exp, seed, phase, key)
        LOGGER.info("Recovered completed remote %s artifact: %s", phase, artifact)
        return artifact

    @contextmanager
    def _phase_attempt(
        self,
        key: str,
        *,
        remote_artifact: RemoteArtifactSpec | None = None,
    ) -> Iterator[str]:
        """Record ownership and convert process interruption into durable state."""
        attempt_id = self._start_phase(key, remote_artifact=remote_artifact)
        try:
            yield attempt_id
        except KeyboardInterrupt as exc:
            self._mark_interrupted(key, exc)
            raise
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            if self._active_manifest["runs"].get(key, {}).get("status") == "syncing":
                self._mark_status(
                    key,
                    "syncing",
                    sync_failed_at=utc_now(),
                    error=error,
                )
                self._active_phase_key = None
            else:
                self._mark_status(
                    key,
                    "failed",
                    finished_at=utc_now(),
                    error=error,
                )
            raise

    def _preflight_disk_isolation(self) -> None:
        """Fail fast if per-worker Disk-IO limits cannot be enforced.

        PBT, BO, and EVAL must run every worker inside the *same*
        hardware envelope for the comparison to be fair. Disk-IO parity
        is enforced via Docker cgroup ``io.max``/blkio, which requires
        resolving the host block device backing the workers' data root.
        When that device can't be resolved (non-Linux host, tmpfs/overlay
        data dir, bind-mounted path), ``EnvironmentFactory`` silently
        drops the disk limits and only logs a warning — a multi-hour run
        can then finish with broken Disk-IO parity and no hard failure.

        This converts that buried warning into a hard stop at the
        orchestration layer, where a whole experiment (and hours of
        compute) is at stake. Skipped under ``dry_run`` (no real runs).
        """
        if self.dry_run:
            return
        if self.distributed:
            LOGGER.info(
                "Distributed mode: skipping coordinator disk-isolation preflight; "
                "each PBT worker owns a dedicated fleet device."
            )
            return
        data_root = resolve_data_root()
        device = _resolve_block_device_node(data_root)
        if device is None:
            raise RuntimeError(
                "Disk-IO isolation preflight FAILED: could not resolve a host "
                f"block device for the workers' data root ({data_root}). "
                "Per-worker disk limits would NOT be enforced, so PBT, BO, and "
                "EVAL would not share the same hardware envelope — invalidating "
                "the comparison. Run on a Linux host whose data root lives on a "
                "real block device (not tmpfs/overlay/bind-mount), or set the "
                "data root via PBT_DATA_ROOT. Aborting before launching the run."
            )
        LOGGER.info(
            "Disk-IO isolation preflight OK: workers' data root %s → block "
            "device %s (per-worker cgroup limits will be enforced).",
            data_root,
            device,
        )

    @contextmanager
    def cpu_performance_session(self):
        """Pin CPU governor=performance + disable turbo for the batch, then revert.

        Removes per-core throughput variance with active-core count (frequency
        scaling + turbo) so PBT (N parallel workers) and the co-tenant-loaded BO
        baseline see identical per-core clocks. The host's original governor/turbo
        is snapshotted on entry and **always restored on exit** — normal return,
        exception, or SIGINT/SIGTERM — so the machine is never left mutated.

        Skipped under ``dry_run``. Best-effort: if the host lacks the sysfs
        interface or we lack root, it logs a warning and proceeds unpinned rather
        than aborting (the disk-isolation preflight is the hard gate; clock
        pinning is a quality-of-measurement improvement, not a correctness
        invariant).
        """
        if self.dry_run or self.distributed:
            if self.distributed:
                LOGGER.info(
                    "Distributed mode: leaving coordinator CPU policy unchanged; "
                    "benchmark execution occurs on dedicated fleet devices."
                )
            yield
            return

        from src.utils.cpu_perf import (
            read_cpu_perf_state,
            set_performance_mode,
            restore_cpu_perf_state,
        )

        saved = read_cpu_perf_state()
        restored = {"done": False}

        def _restore_once(*_args) -> None:
            if restored["done"]:
                return
            restored["done"] = True
            restore_cpu_perf_state(saved)

        # Belt-and-suspenders: also restore on hard signals and at interpreter
        # exit, in case the surrounding loop is killed outside the finally.
        prev_handlers: dict[int, object] = {}
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                prev_handlers[sig] = signal.getsignal(sig)

                def _handler(signum, frame, _sig=sig):
                    _restore_once()
                    prev = prev_handlers.get(_sig)
                    if callable(prev):
                        prev(signum, frame)
                    else:
                        # Default behaviour: re-raise as the process terminating.
                        raise KeyboardInterrupt()

                signal.signal(sig, _handler)
            except (ValueError, OSError):
                # Not in main thread or unsupported — skip signal hook.
                pass
        atexit.register(_restore_once)

        if saved.supported:
            set_performance_mode(saved)
        try:
            yield
        finally:
            _restore_once()
            for sig, prev in prev_handlers.items():
                try:
                    signal.signal(sig, prev)  # type: ignore[arg-type]
                except (ValueError, OSError, TypeError):
                    pass

    def run_experiment(self, exp: Experiment, retry_failed: bool = False) -> None:
        """Run one experiment while holding the process-wide fleet lock."""
        with self._exclusive_runner_session():
            self._run_experiment_locked(exp, retry_failed=retry_failed)

    def _run_experiment_locked(
        self, exp: Experiment, retry_failed: bool = False
    ) -> None:
        """Execute one experiment after exclusive ownership is established."""
        LOGGER.info(f"Starting experiment {exp.id} (Tier {exp.tier})")

        if self.distributed and exp.strategy != "pbt":
            raise ValueError(
                "Distributed execution currently supports PBT experiments only"
            )

        # Guarantee Disk-IO parity is enforceable before burning compute.
        self._preflight_disk_isolation()

        # Activate this experiment's own manifest. All writes during
        # this call go to a single file owned by this experiment, so
        # peer machines running other experiments never compete on it.
        self._active_manifest_path = self._resolve_manifest_path(exp.id)
        self._active_manifest = self._load_manifest(self._active_manifest_path)
        # Refresh the cross-manifest index so the active experiment's
        # own writes don't shadow peer manifests pulled since startup.
        self._cross_manifest_index = self._build_cross_manifest_index()

        # LHS-design sweeps are a single-phase prep run (no BO/eval), but
        # reuse the same manifest/resource/commit machinery as every other
        # experiment.
        if exp.strategy == "lhs":
            self._run_lhs_experiment(exp, retry_failed)
            return

        for seed in exp.seeds:
            LOGGER.info(f"=== {exp.id} | Seed {seed} ===")
            pbt_session_path = None
            bo_session_path = None

            # 1. PBT Phase
            pbt_key = self._get_run_key(exp.id, seed, "pbt")
            if not self._is_done(pbt_key, retry_failed):
                LOGGER.info(f"Phase 1/3: Running PBT for {exp.id} (seed {seed})")
                cmd = self._build_pbt_cmd(exp, seed)
                with self._phase_attempt(pbt_key):
                    start_time = time.monotonic()
                    success = self._run_command(cmd)
                    duration = time.monotonic() - start_time

                    if success:
                        json_path = self._find_latest_session_json(
                            RESULTS_DIR, "pbt"
                        )
                        if json_path is None and not self.dry_run:
                            raise RuntimeError(
                                "PBT exited successfully without a session JSON"
                            )
                        json_str = (
                            str(json_path.relative_to(PROJECT_ROOT))
                            if json_path
                            else None
                        )
                        self._mark_status(
                            pbt_key,
                            "done",
                            duration_s=duration,
                            finished_at=utc_now(),
                            session_json=json_str,
                        )
                        pbt_session_path = json_path
                    else:
                        self._mark_status(
                            pbt_key,
                            "failed",
                            duration_s=duration,
                            finished_at=utc_now(),
                            error="PBT command failed",
                        )
                if success:
                    self._publish_phase(exp, seed, "pbt", pbt_key)
                else:
                    if self.distributed:
                        self._prepare_comparison_device()
                    LOGGER.error(
                        "PBT phase failed. Skipping BO and EVAL for this seed."
                    )
                    continue
            else:
                LOGGER.info("Skipping PBT (already done/failed)")
                self._resume_publication_if_needed(
                    exp, seed, "pbt", pbt_key
                )
                json_str = self._active_manifest["runs"][pbt_key].get("session_json")
                pbt_session_path = PROJECT_ROOT / json_str if json_str else None

            comparison_pbt_session = pbt_session_path
            if self.distributed:
                # PBT agent shutdown does not remove its PostgreSQL container.
                # Clean every fleet host before any comparison or early exit.
                self._prepare_comparison_device()
                if not pbt_session_path and not self.dry_run:
                    LOGGER.error(
                        "Cannot hand off distributed run: PBT session JSON not found."
                    )
                    continue
                comparison_pbt_session = Path(
                    self._stage_file_on_comparison_device(
                        pbt_session_path
                        if pbt_session_path is not None
                        else PROJECT_ROOT / "DRY_RUN_PBT_SESSION_PATH.json"
                    )
                )

            # 2. BO Phase (only if enabled)
            if exp.run_bo:
                bo_key = self._get_run_key(exp.id, seed, "bo")
                recovered_bo = self._try_reconcile_remote_phase(
                    exp,
                    seed,
                    "bo",
                    bo_key,
                    retry_failed=retry_failed,
                )
                if recovered_bo is not None:
                    bo_session_path = recovered_bo
                if not self._is_done(bo_key, retry_failed):
                    if not pbt_session_path and not self.dry_run:
                        LOGGER.error("Cannot run BO: PBT session JSON not found.")
                        self._mark_status(
                            bo_key,
                            "failed",
                            finished_at=utc_now(),
                            error="Missing PBT JSON",
                        )
                    else:
                        LOGGER.info(f"Phase 2/3: Running BO for {exp.id} (seed {seed})")
                        cmd = self._build_bo_cmd(exp, comparison_pbt_session, seed)
                        with self._phase_attempt(bo_key) as attempt_id:
                            artifact_spec = (
                                self._remote_artifact_spec(
                                    exp, "bo", attempt_id
                                )
                                if self.distributed
                                else None
                            )
                            if artifact_spec is not None:
                                self._set_remote_artifact(bo_key, artifact_spec)
                            start_time = time.monotonic()
                            success = (
                                self._run_remote_command(
                                    self._comparison_device(),
                                    cmd,
                                    artifact=artifact_spec,
                                )
                                if self.distributed
                                else self._run_command(cmd)
                            )
                            duration = time.monotonic() - start_time

                            if success:
                                if self.distributed:
                                    self._mark_status(
                                        bo_key,
                                        "syncing",
                                        duration_s=duration,
                                        remote_completed_at=utc_now(),
                                    )
                                    json_path = self._reconcile_remote_artifact(
                                        exp,
                                        seed,
                                        "bo",
                                        self._active_manifest["runs"][bo_key],
                                    )
                                else:
                                    json_path = self._find_latest_session_json(
                                        RESULTS_DIR, "bo"
                                    )
                                if json_path is None and not self.dry_run:
                                    raise RuntimeError(
                                        "BO exited successfully without a session JSON"
                                    )
                                json_str = (
                                    str(json_path.relative_to(PROJECT_ROOT))
                                    if json_path
                                    else None
                                )
                                self._mark_status(
                                    bo_key,
                                    "done",
                                    duration_s=duration,
                                    finished_at=utc_now(),
                                    session_json=json_str,
                                    artifact_json=json_str,
                                )
                                bo_session_path = json_path
                            else:
                                self._mark_status(
                                    bo_key,
                                    "failed",
                                    duration_s=duration,
                                    finished_at=utc_now(),
                                    error="BO command failed",
                                )
                        if success:
                            self._publish_phase(exp, seed, "bo", bo_key)
                        else:
                            LOGGER.error(
                                "BO phase failed. Skipping EVAL for this seed."
                            )
                            continue
                else:
                    LOGGER.info("Skipping BO (already done/failed)")
                    self._resume_publication_if_needed(
                        exp, seed, "bo", bo_key
                    )
                    if bo_session_path is None:
                        json_str = self._active_manifest["runs"].get(
                            bo_key, {}
                        ).get("session_json")
                        bo_session_path = (
                            PROJECT_ROOT / json_str if json_str else None
                        )

            comparison_bo_session = bo_session_path
            if self.distributed and bo_session_path is not None:
                comparison_bo_session = Path(
                    self._stage_file_on_comparison_device(bo_session_path)
                )

            # 3. EVAL Phase
            eval_key = self._get_run_key(exp.id, seed, "eval")
            self._try_reconcile_remote_phase(
                exp,
                seed,
                "eval",
                eval_key,
                retry_failed=retry_failed,
            )
            if not self._is_done(eval_key, retry_failed):
                if not pbt_session_path and not self.dry_run:
                    LOGGER.error("Cannot run EVAL: PBT session JSON not found.")
                    self._mark_status(
                        eval_key,
                        "failed",
                        finished_at=utc_now(),
                        error="Missing PBT JSON",
                    )
                else:
                    LOGGER.info(f"Phase 3/3: Running EVAL for {exp.id} (seed {seed})")
                    cmd = self._build_eval_cmd(
                        comparison_pbt_session,
                        comparison_bo_session,
                        exp.eval_repetitions,
                        seed,
                    )
                    with self._phase_attempt(eval_key) as attempt_id:
                        artifact_spec = (
                            self._remote_artifact_spec(exp, "eval", attempt_id)
                            if self.distributed
                            else None
                        )
                        if artifact_spec is not None:
                            self._set_remote_artifact(eval_key, artifact_spec)
                        start_time = time.monotonic()
                        success = (
                            self._run_remote_command(
                                self._comparison_device(),
                                cmd,
                                artifact=artifact_spec,
                            )
                            if self.distributed
                            else self._run_command(cmd)
                        )
                        duration = time.monotonic() - start_time

                        if success:
                            if self.distributed:
                                self._mark_status(
                                    eval_key,
                                    "syncing",
                                    duration_s=duration,
                                    remote_completed_at=utc_now(),
                                )
                                artifact_path = self._reconcile_remote_artifact(
                                    exp,
                                    seed,
                                    "eval",
                                    self._active_manifest["runs"][eval_key],
                                )
                            else:
                                candidates = sorted(
                                    RESULTS_DIR.rglob("*comparison_*.json"),
                                    key=lambda path: path.stat().st_mtime,
                                )
                                artifact_path = candidates[-1] if candidates else None
                            if artifact_path is None and not self.dry_run:
                                raise RuntimeError(
                                    "EVAL exited successfully without a result JSON"
                                )
                            artifact_json = (
                                str(artifact_path.relative_to(PROJECT_ROOT))
                                if artifact_path
                                else None
                            )
                            self._mark_status(
                                eval_key,
                                "done",
                                duration_s=duration,
                                finished_at=utc_now(),
                                artifact_json=artifact_json,
                            )
                        else:
                            self._mark_status(
                                eval_key,
                                "failed",
                                duration_s=duration,
                                finished_at=utc_now(),
                                error="EVAL command failed",
                            )
                    if success:
                        self._publish_phase(exp, seed, "eval", eval_key)
            else:
                LOGGER.info("Skipping EVAL (already done/failed)")
                self._resume_publication_if_needed(
                    exp, seed, "eval", eval_key
                )

    def _run_lhs_experiment(self, exp: Experiment, retry_failed: bool = False) -> None:
        """Run an LHS-design importance sweep: a single phase, no BO/eval.

        Mirrors the PBT phase's manifest tracking, resource handling, and
        commit/push so an LHS run is resumable and recorded exactly like every
        other experiment. The ``lhs_results_*.json`` it produces is the input
        to the SCALPEL knob-importance pipeline
        (``scripts/run_importance_fast.sh`` / ``run_importance_full.sh``).
        """
        for seed in exp.seeds:
            LOGGER.info(f"=== {exp.id} | Seed {seed} (LHS) ===")
            key = self._get_run_key(exp.id, seed, "lhs")
            if self._is_done(key, retry_failed):
                LOGGER.info("Skipping LHS (already done/failed)")
                self._resume_publication_if_needed(
                    exp, seed, "lhs", key
                )
                continue

            LOGGER.info(f"Running LHS-design sweep for {exp.id} (seed {seed})")
            cmd = self._build_lhs_cmd(exp, seed)
            with self._phase_attempt(key):
                start_time = time.monotonic()
                success = self._run_command(cmd)
                duration = time.monotonic() - start_time

                if success:
                    json_path = self._find_latest_session_json(
                        RESULTS_DIR, "lhs"
                    )
                    if json_path is None and not self.dry_run:
                        raise RuntimeError(
                            "LHS exited successfully without a session JSON"
                        )
                    json_str = (
                        str(json_path.relative_to(PROJECT_ROOT))
                        if json_path
                        else None
                    )
                    self._mark_status(
                        key,
                        "done",
                        duration_s=duration,
                        finished_at=utc_now(),
                        session_json=json_str,
                    )
                else:
                    self._mark_status(
                        key,
                        "failed",
                        duration_s=duration,
                        finished_at=utc_now(),
                        error="LHS command failed",
                    )
            if success:
                self._publish_phase(exp, seed, "lhs", key)

    def _resolve_warm_start_path(self, exp: Experiment) -> Path | None:
        """Resolve the best_config.json path for a warm-start experiment.

        The source experiment's PBT phase must have completed and recorded
        a session_json in the manifest. The best_config.json lives as a
        sibling of the session JSON: ``.../pbt/<tier>/best_configs/
        best_<timestamp>.json`` (vs ``.../pbt/<tier>/traces/
        trace_<timestamp>.json``).

        Returns None if the source isn't ready (caller should fail fast).
        """
        if exp.warm_start_source is None or exp.warm_start_source_seed is None:
            return None

        source_key = self._get_run_key(
            exp.warm_start_source, exp.warm_start_source_seed, "pbt"
        )
        # Cross-manifest read: source experiment may live in its own
        # per-experiment manifest (possibly pulled from a peer machine).
        source_run = self._cross_manifest_index.get(source_key, {})
        if source_run.get("status") != "done":
            LOGGER.error(
                "Warm-start source %s/seed_%d/pbt is not done (status=%s). "
                "Run that experiment first, or remove --tier filtering so the "
                "matrix runs in dependency order.",
                exp.warm_start_source,
                exp.warm_start_source_seed,
                source_run.get("status", "missing"),
            )
            return None

        session_json_str = source_run.get("session_json")
        if not session_json_str:
            LOGGER.error(
                "Warm-start source %s recorded no session_json in manifest.",
                source_key,
            )
            return None

        session_path = PROJECT_ROOT / session_json_str
        # Derive sibling best_config path. Filenames share the timestamp
        # suffix; only the directory differs (traces ↔ best_configs) and the
        # stem prefix (trace_ ↔ best_). Legacy runs used pbt_results_ ↔
        # best_config_, so fall back to that when the trace_ stem is absent.
        try:
            name = session_path.name
            if name.startswith("trace_"):
                best_name = name.replace("trace_", "best_", 1)
            else:
                best_name = name.replace("pbt_results_", "best_config_", 1)
            best_config_path = (
                session_path.parent.parent / "best_configs" / best_name
            )
        except Exception as e:
            LOGGER.error("Failed to derive best_config path from %s: %s", session_path, e)
            return None

        if not best_config_path.exists():
            LOGGER.error(
                "Warm-start best_config not found at %s (session was %s)",
                best_config_path,
                session_path,
            )
            return None

        return best_config_path

    def _build_lhs_cmd(self, exp: Experiment, seed: int) -> list[str]:
        """Build the LHS-design importance-sweep command.

        Threads the same resource flags (``--worker-ram``/``--worker-cpus``
        from ``detect_worker_resources``) and instance/snapshot handling as
        the PBT phase, so the sweep runs under identical resource limits.
        ``--config thorough`` supplies the 512-point design size unless the
        experiment pins ``design_size``.
        """
        worker_ram, worker_cpus = self._worker_resource_flags(exp)
        cmd = [
            "python", "-m", "src.tuners.lhs_design",
            "--config", exp.config_profile,
            "--tier", exp.knob_tier,
            "--knob-source", exp.knob_source,
            "--benchmark", exp.benchmark,
            "--random-seed", str(seed),
            "--tuning-mode", exp.tuning_mode,
            "--snapshot-restore-interval", "1",
            "--force-recreate-instances",
            "--worker-ram", worker_ram,
            "--worker-cpus", str(worker_cpus),
            "--verbose", "DEBUG",
        ]
        if exp.sysbench_workload:
            cmd.extend(["--sysbench-workload", exp.sysbench_workload])
        if exp.scale_factor is not None:
            cmd.extend(["--scale-factor", str(exp.scale_factor)])
        if exp.design_size is not None:
            cmd.extend(["--design-size", str(exp.design_size)])
        return cmd

    def _build_pbt_cmd(self, exp: Experiment, seed: int) -> list[str]:
        cmd = [
            "python", "-m", "src.tuners", "pbt",
            "--config", exp.config_profile,
            "--tier", exp.knob_tier,
            "--knob-source", exp.knob_source,
            "--benchmark", exp.benchmark,
            "--random-seed", str(seed),
            "--tuning-mode", exp.tuning_mode,
            # Pin restore interval explicitly so the experiment is
            # self-documenting and cannot drift if THOROUGH_CONFIG changes
            # upstream. THOROUGH currently has interval=1; the explicit
            # flag makes that contract part of the experiment record.
            "--snapshot-restore-interval", "1",
            "--force-recreate-instances",
            "--verbose", "DEBUG"
        ]

        if self.distributed:
            # Device agents detect and report their full dedicated-device
            # resources. Never derive worker limits from the coordinator.
            cmd.extend(self._distributed_pbt_flags())
        else:
            worker_ram, worker_cpus = self._worker_resource_flags(exp)
            cmd.extend(
                [
                    "--worker-ram",
                    worker_ram,
                    "--worker-cpus",
                    str(worker_cpus),
                ]
            )

        if exp.sysbench_workload:
            cmd.extend(["--sysbench-workload", exp.sysbench_workload])
        if exp.scale_factor is not None:
            cmd.extend(["--scale-factor", str(exp.scale_factor)])
        if exp.population is not None:
            cmd.extend(["--population", str(exp.population)])
        if exp.generations is not None:
            cmd.extend(["--generations", str(exp.generations)])
        if exp.parallel_workers is not None:
            cmd.extend(["--parallel-workers", str(exp.parallel_workers)])
        if exp.exploit_quantile is not None:
            cmd.extend(["--exploit-quantile", str(exp.exploit_quantile)])
        if exp.scoring_policy is not None:
            cmd.extend(["--scoring-policy", exp.scoring_policy])
        if exp.perturbation_factor is not None:
            cmd.extend(["--perturbation-factor", str(exp.perturbation_factor)])
        if exp.ablation_variable:
            cmd.extend([
                "--ablation-variable", exp.ablation_variable,
                "--ablation-value", str(exp.ablation_value)
            ])

        # Warm-start: resolve upstream best_config.json from the manifest.
        # If the source isn't ready, the resolver returns None and logs
        # an error; we fall through without a flag so PBT runs without
        # warm-start (LHS init only). Caller should check
        # _resolve_warm_start_path before invoking when correctness matters.
        if exp.warm_start_source is not None:
            warm_path = self._resolve_warm_start_path(exp)
            if warm_path is not None:
                cmd.extend(["--warm-start", str(warm_path)])
            elif self.dry_run:
                cmd.extend(["--warm-start", "DRY_RUN_WARM_START_PATH.json"])

        return cmd

    def _build_bo_cmd(self, exp: Experiment, pbt_session: Path | None, seed: int) -> list[str]:
        # BO inherits population/budget from the PBT session via
        # --pbt-session, but every experiment-defining flag is passed
        # explicitly so a stale or partial session JSON cannot silently
        # change the workload shape. Anything that would mismatch the
        # PBT run breaks the fair-comparison invariant the paper rests on.
        #
        # NOTE: when --pbt-session is present (the normal matrix path) the BO
        # runner inherits per-worker resources AND the co-tenancy degree from
        # the session JSON, so these --worker-ram/--worker-cpus flags are
        # intentionally redundant (logged as ignored by the BO runner). They
        # are kept for the standalone-BO path where no session is supplied.
        cmd = [
            "python", "-m", "src.tuners", "bo",
            "--config", exp.config_profile,
            "--tier", exp.knob_tier,
            "--knob-source", exp.knob_source,
            "--benchmark", exp.benchmark,
            "--seed", str(seed),
            "--tuning-mode", exp.tuning_mode,
            "--enable-snapshots",
            "--snapshot-restore-interval", "1",
            "--force-recreate-instances",
            "--verbose", "INFO"
        ]
        if self.distributed:
            # Distributed PBT has no single-host co-tenancy. Run BO alone on
            # one of the same fleet devices and inherit its full resource
            # envelope from the PBT session.
            cmd.append("--no-cotenant")
        else:
            worker_ram, worker_cpus = self._worker_resource_flags(exp)
            cmd.extend(
                [
                    "--worker-ram",
                    worker_ram,
                    "--worker-cpus",
                    str(worker_cpus),
                ]
            )
        if exp.sysbench_workload:
            cmd.extend(["--sysbench-workload", exp.sysbench_workload])
        if exp.scale_factor is not None:
            cmd.extend(["--scale-factor", str(exp.scale_factor)])

        if pbt_session:
            cmd.extend(["--pbt-session", str(pbt_session)])
        elif self.dry_run:
            cmd.extend(["--pbt-session", "DRY_RUN_PBT_SESSION_PATH.json"])
        return cmd

    def _build_eval_cmd(self, pbt_session: Path | None, bo_session: Path | None, repetitions: int, seed: int) -> list[str]:
        cmd = [
            "python", "-m", "src.evaluation",
            "--repetitions", str(repetitions),
            "--seed", str(seed),
            "--verbose", "INFO"
        ]
        if pbt_session:
            cmd.extend(["--session", str(pbt_session)])
        elif self.dry_run:
            cmd.extend(["--session", "DRY_RUN_PBT_SESSION_PATH.json"])
            
        if bo_session:
            cmd.extend(["--bo-session", str(bo_session)])
        elif self.dry_run and bo_session is not False:
            cmd.extend(["--bo-session", "DRY_RUN_BO_SESSION_PATH.json"])
            
        return cmd
