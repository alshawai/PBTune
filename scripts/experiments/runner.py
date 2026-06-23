import json
import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path

from scripts.experiments.experiment_matrix import Experiment
from src.utils.hardware_info import detect_worker_resources

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS_DIR = PROJECT_ROOT / "results"
DEFAULT_MANIFEST_DIR = RESULTS_DIR / "manifests"
LEGACY_MANIFEST_PATH = RESULTS_DIR / "experiment_manifest.json"
# Back-compat alias: legacy callers (e.g. __main__'s --status) import
# MANIFEST_PATH directly. The runner no longer writes here by default.
MANIFEST_PATH = LEGACY_MANIFEST_PATH

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
        
        # Calculate resources once
        resources = detect_worker_resources(max_parallel_workers=8, threshold=0.95)
        worker_ram_mb = resources.ram_bytes // (1024 * 1024)
        self.worker_ram = f"{worker_ram_mb}M"
        self.worker_cpus = max(1, resources.cpu_cores)
        
        # Set up logging
        if not LOGGER.handlers:
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s [%(levelname)s] %(message)s"
            )

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

    def _commit_and_push(self, exp: Experiment, seed: int, phase: str) -> None:
        if self.dry_run or self.no_push:
            return

        try:
            subprocess.run(["git", "add", "--", *self._paths_to_stage(exp)], cwd=RESULTS_DIR, check=True)
            status = subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=RESULTS_DIR
            )
            if status.returncode == 0:
                LOGGER.info("No changes to commit in results repo.")
                return

            msg = f"results({exp.id}): {phase} seed={seed}"
            subprocess.run(["git", "commit", "-m", msg], cwd=RESULTS_DIR, check=True)
            subprocess.run(["git", "push", "origin", "main"], cwd=RESULTS_DIR, check=True)
            LOGGER.info(f"Successfully pushed {msg} to PBTune-experiments")
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

    def run_experiment(self, exp: Experiment, retry_failed: bool = False) -> None:
        LOGGER.info(f"Starting experiment {exp.id} (Tier {exp.tier})")

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
            "--worker-ram", self.worker_ram,
            "--worker-cpus", str(self.worker_cpus),
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
            "--worker-ram", self.worker_ram,
            "--worker-cpus", str(self.worker_cpus),
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
            "--worker-ram", self.worker_ram,
            "--worker-cpus", str(self.worker_cpus),
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
