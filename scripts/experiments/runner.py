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
MANIFEST_PATH = RESULTS_DIR / "experiment_manifest.json"

LOGGER = logging.getLogger("ExperimentRunner")

class ExperimentRunner:
    def __init__(self, dry_run: bool = False, no_push: bool = False):
        self.dry_run = dry_run
        self.no_push = no_push
        self.manifest = self._load_manifest()
        
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

    def _load_manifest(self) -> dict:
        if MANIFEST_PATH.exists():
            return json.loads(MANIFEST_PATH.read_text())
        return {
            "started_at": datetime.utcnow().isoformat() + "Z",
            "runs": {}
        }

    def _save_manifest(self) -> None:
        if self.dry_run:
            return
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(json.dumps(self.manifest, indent=2))

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

    def _commit_and_push(self, exp_id: str, seed: int, phase: str) -> None:
        if self.dry_run or self.no_push:
            return
            
        try:
            subprocess.run(["git", "add", "-A"], cwd=RESULTS_DIR, check=True)
            status = subprocess.run(
                ["git", "diff", "--cached", "--quiet"], 
                cwd=RESULTS_DIR
            )
            if status.returncode == 0:
                LOGGER.info("No changes to commit in results repo.")
                return
                
            msg = f"results({exp_id}): {phase} seed={seed}"
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
        run_data = self.manifest["runs"].get(key, {})
        status = run_data.get("status")
        if status == "done":
            return True
        if status == "failed" and not retry_failed:
            return True
        return False

    def _mark_status(self, key: str, status: str, **kwargs) -> None:
        if key not in self.manifest["runs"]:
            self.manifest["runs"][key] = {}
        self.manifest["runs"][key]["status"] = status
        self.manifest["runs"][key].update(kwargs)
        self._save_manifest()

    def run_experiment(self, exp: Experiment, retry_failed: bool = False) -> None:
        LOGGER.info(f"Starting experiment {exp.id} (Tier {exp.tier})")
        
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
                    self._commit_and_push(exp.id, seed, "pbt")
                    pbt_session_path = json_path
                else:
                    self._mark_status(pbt_key, "failed", duration_s=duration)
                    LOGGER.error("PBT phase failed. Skipping BO and EVAL for this seed.")
                    continue
            else:
                LOGGER.info(f"Skipping PBT (already done/failed)")
                json_str = self.manifest["runs"][pbt_key].get("session_json")
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
                            self._commit_and_push(exp.id, seed, "bo")
                            bo_session_path = json_path
                        else:
                            self._mark_status(bo_key, "failed", duration_s=duration)
                            LOGGER.error("BO phase failed. Skipping EVAL for this seed.")
                            continue
                else:
                    LOGGER.info(f"Skipping BO (already done/failed)")
                    json_str = self.manifest["runs"].get(bo_key, {}).get("session_json")
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
                        self._commit_and_push(exp.id, seed, "eval")
                    else:
                        self._mark_status(eval_key, "failed", duration_s=duration)
            else:
                LOGGER.info(f"Skipping EVAL (already done/failed)")

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
        source_run = self.manifest["runs"].get(source_key, {})
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
