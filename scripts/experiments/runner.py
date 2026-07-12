import atexit
import json
import logging
import signal
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from scripts.experiments.experiment_matrix import Experiment
from src.config.data_root import resolve_data_root
from src.utils.hardware_info import detect_worker_resources, _resolve_block_device_node
from src.tuners.pbt.config import (
    RAPID_CONFIG,
    STANDARD_CONFIG,
    THOROUGH_CONFIG,
    RESEARCH_CONFIG,
)

# Per-experiment parallel-worker resolution must mirror what the PBT CLI
# (``src.tuner.main``) will actually use as ``num_parallel_workers`` -- that is
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

LOGGER = logging.getLogger("ExperimentRunner")


def _empty_manifest() -> dict:
    return {
        "started_at": datetime.utcnow().isoformat() + "Z",
        "runs": {},
    }


class ExperimentRunner:
    def __init__(
        self,
        dry_run: bool = False,
        no_push: bool = False,
        manifest_dir: Path | None = None,
        manifest_path: Path | None = None,
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

    def _effective_parallel_workers(self, exp: Experiment) -> int:
        """Resolve the parallel-worker count the PBT run will actually use.

        Mirrors ``src.tuner.main``: an explicit ``parallel_workers`` on the
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

    def _resolve_manifest_path(self, exp_id: str) -> Path:
        """Resolve the manifest path for ``exp_id``.

        Precedence: ``--manifest`` override > per-experiment derived
        path under ``manifest_dir``.
        """
        if self.manifest_path_override is not None:
            return self.manifest_path_override
        return self.manifest_dir / f"{exp_id}.json"

    def _workload_subtree(self, exp: Experiment) -> str:
        """Return the workload-keyed result subdirectory (relative to
        ``RESULTS_DIR``) where this experiment's PBT, BO, and eval
        outputs land.

        Matches the on-disk convention used by ``src.tuner.main``,
        ``src.scripts.bo_baseline``, and ``src.evaluation``:

        - ``benchmark="sysbench"`` → ``oltp/<sysbench_workload>``
        - ``benchmark="tpch"``     → ``olap``

        The previous implementation staged ``exp.id`` directly, which
        ``git add`` rejected because the experiment id is a label
        (e.g. ``t2_sysbench_ro``) — never a directory on disk.
        """
        if exp.benchmark == "tpch":
            return "olap"
        if exp.benchmark == "sysbench":
            return f"oltp/{exp.sysbench_workload or 'oltp_read_write'}"
        raise ValueError(
            f"Cannot derive workload subtree for experiment {exp.id!r}: "
            f"unknown benchmark {exp.benchmark!r}"
        )

    def _paths_to_stage(self, exp: Experiment) -> list[str]:
        """Compute the git pathspecs to stage for ``exp``'s commit.

        Returns paths relative to ``RESULTS_DIR``. Restricting to these
        avoids ``git add -A`` picking up an in-flight peer-machine write
        — the original source of merge conflicts on the results repo.
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
                # the workload subtree below.
                pass
        # Workload subtree is where every phase's output file lands.
        paths.append(self._workload_subtree(exp))
        return paths

    def _load_manifest(self, path: Path) -> dict:
        if path.exists():
            return json.loads(path.read_text())
        return _empty_manifest()

    def _save_manifest(self) -> None:
        if self.dry_run or self._active_manifest_path is None:
            return
        self._active_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self._active_manifest_path.write_text(
            json.dumps(self._active_manifest, indent=2)
        )

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

    def _commit_and_push(self, exp: Experiment, seed: int, phase: str) -> None:
        if self.dry_run or self.no_push:
            return

        try:
            # 1. Integrate peer commits first (stash → pull --rebase → pop) so
            #    our push is a fast-forward in the common case.
            self._sync_results_repo()

            # 2. Stage only this experiment's artifacts (path scoping avoids
            #    picking up an in-flight peer write).
            self._git("add", "--", *self._paths_to_stage(exp))
            if self._git("diff", "--cached", "--quiet", check=False).returncode == 0:
                LOGGER.info("No changes to commit in results repo.")
                return

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
                    return
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
        except subprocess.CalledProcessError as e:
            LOGGER.error(f"Failed to commit/push results: {e}")

    def _find_latest_session_json(self, output_dir: Path, prefix: str) -> Path | None:
        if not output_dir.exists():
            return None
        candidates = sorted(output_dir.rglob(f"{prefix}*.json"), key=lambda p: p.stat().st_mtime)
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

    def _mark_status(self, key: str, status: str, **kwargs) -> None:
        if key not in self._active_manifest["runs"]:
            self._active_manifest["runs"][key] = {}
        self._active_manifest["runs"][key]["status"] = status
        self._active_manifest["runs"][key].update(kwargs)
        self._save_manifest()

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
        if self.dry_run:
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
        LOGGER.info(f"Starting experiment {exp.id} (Tier {exp.tier})")

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
                self._mark_status(pbt_key, "running", started_at=datetime.utcnow().isoformat() + "Z")
                
                start_time = time.time()
                success = self._run_command(cmd)
                duration = time.time() - start_time
                
                if success:
                    # Find session JSON
                    json_path = self._find_latest_session_json(RESULTS_DIR, "pbt_results_")
                    json_str = str(json_path.relative_to(PROJECT_ROOT)) if json_path else None
                    self._mark_status(pbt_key, "done", duration_s=duration, session_json=json_str)
                    self._commit_and_push(exp, seed, "pbt")
                    pbt_session_path = json_path
                else:
                    self._mark_status(pbt_key, "failed", duration_s=duration)
                    LOGGER.error("PBT phase failed. Skipping BO and EVAL for this seed.")
                    continue
            else:
                LOGGER.info(f"Skipping PBT (already done/failed)")
                json_str = self._active_manifest["runs"][pbt_key].get("session_json")
                pbt_session_path = PROJECT_ROOT / json_str if json_str else None

            # 2. BO Phase (only if enabled)
            if exp.run_bo:
                bo_key = self._get_run_key(exp.id, seed, "bo")
                if not self._is_done(bo_key, retry_failed):
                    if not pbt_session_path and not self.dry_run:
                        LOGGER.error("Cannot run BO: PBT session JSON not found.")
                        self._mark_status(bo_key, "failed", error="Missing PBT JSON")
                    else:
                        LOGGER.info(f"Phase 2/3: Running BO for {exp.id} (seed {seed})")
                        cmd = self._build_bo_cmd(exp, pbt_session_path, seed)
                        self._mark_status(bo_key, "running", started_at=datetime.utcnow().isoformat() + "Z")
                        
                        start_time = time.time()
                        success = self._run_command(cmd)
                        duration = time.time() - start_time
                        
                        if success:
                            json_path = self._find_latest_session_json(RESULTS_DIR, "bo_results_")
                            json_str = str(json_path.relative_to(PROJECT_ROOT)) if json_path else None
                            self._mark_status(bo_key, "done", duration_s=duration, session_json=json_str)
                            self._commit_and_push(exp, seed, "bo")
                            bo_session_path = json_path
                        else:
                            self._mark_status(bo_key, "failed", duration_s=duration)
                            LOGGER.error("BO phase failed. Skipping EVAL for this seed.")
                            continue
                else:
                    LOGGER.info(f"Skipping BO (already done/failed)")
                    json_str = self._active_manifest["runs"].get(bo_key, {}).get("session_json")
                    bo_session_path = PROJECT_ROOT / json_str if json_str else None
            
            # 3. EVAL Phase
            eval_key = self._get_run_key(exp.id, seed, "eval")
            if not self._is_done(eval_key, retry_failed):
                if not pbt_session_path and not self.dry_run:
                    LOGGER.error("Cannot run EVAL: PBT session JSON not found.")
                    self._mark_status(eval_key, "failed", error="Missing PBT JSON")
                else:
                    LOGGER.info(f"Phase 3/3: Running EVAL for {exp.id} (seed {seed})")
                    cmd = self._build_eval_cmd(pbt_session_path, bo_session_path, exp.eval_repetitions, seed)
                    self._mark_status(eval_key, "running", started_at=datetime.utcnow().isoformat() + "Z")
                    
                    start_time = time.time()
                    success = self._run_command(cmd)
                    duration = time.time() - start_time
                    
                    if success:
                        self._mark_status(eval_key, "done", duration_s=duration)
                        self._commit_and_push(exp, seed, "eval")
                    else:
                        self._mark_status(eval_key, "failed", duration_s=duration)
            else:
                LOGGER.info(f"Skipping EVAL (already done/failed)")

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
                continue

            LOGGER.info(f"Running LHS-design sweep for {exp.id} (seed {seed})")
            cmd = self._build_lhs_cmd(exp, seed)
            self._mark_status(key, "running", started_at=datetime.utcnow().isoformat() + "Z")

            start_time = time.time()
            success = self._run_command(cmd)
            duration = time.time() - start_time

            if success:
                json_path = self._find_latest_session_json(RESULTS_DIR, "lhs_results_")
                json_str = str(json_path.relative_to(PROJECT_ROOT)) if json_path else None
                self._mark_status(key, "done", duration_s=duration, session_json=json_str)
                self._commit_and_push(exp, seed, "lhs")
            else:
                self._mark_status(key, "failed", duration_s=duration)

    def _resolve_warm_start_path(self, exp: Experiment) -> Path | None:
        """Resolve the best_config.json path for a warm-start experiment.

        The source experiment's PBT phase must have completed and recorded
        a session_json in the manifest. The best_config.json lives as a
        sibling of the session JSON: ``.../pbt_runs/<tier>/best_configs/
        best_config_<timestamp>.json`` (vs ``.../pbt_runs/<tier>/
        tuning_sessions/pbt_results_<timestamp>.json``).

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
        # suffix; only the directory differs (tuning_sessions ↔ best_configs)
        # and the prefix (pbt_results_ ↔ best_config_).
        try:
            best_config_path = (
                session_path.parent.parent
                / "best_configs"
                / session_path.name.replace("pbt_results_", "best_config_", 1)
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
        worker_ram, worker_cpus = self._worker_resource_flags(exp)
        cmd = [
            "python", "-m", "src.tuner.main",
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
            "--worker-ram", worker_ram,
            "--worker-cpus", str(worker_cpus),
            "--verbose", "DEBUG"
        ]

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
        worker_ram, worker_cpus = self._worker_resource_flags(exp)
        cmd = [
            "python", "-m", "src.scripts.bo_baseline",
            "--config", exp.config_profile,
            "--tier", exp.knob_tier,
            "--knob-source", exp.knob_source,
            "--benchmark", exp.benchmark,
            "--seed", str(seed),
            "--tuning-mode", exp.tuning_mode,
            "--enable-snapshots",
            "--snapshot-restore-interval", "1",
            "--force-recreate-instances",
            "--worker-ram", worker_ram,
            "--worker-cpus", str(worker_cpus),
            "--verbose", "INFO"
        ]
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
