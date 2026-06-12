import json
import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from src.utils.hardware_info import detect_worker_resources
from scripts.experiments.experiment_matrix import Experiment

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

    def _load_manifest(self) -> Dict:
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

    def _run_command(self, cmd: List[str], cwd: Path = PROJECT_ROOT) -> bool:
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
            subprocess.run(["git", "push", "main", "main"], cwd=RESULTS_DIR, check=True)
            LOGGER.info(f"Successfully pushed {msg} to PBTune-experiments")
        except subprocess.CalledProcessError as e:
            LOGGER.error(f"Failed to commit/push results: {e}")

    def _find_latest_session_json(self, output_dir: Path, prefix: str) -> Optional[Path]:
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
                        cmd = self._build_bo_cmd(pbt_session_path, seed)
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

    def _build_pbt_cmd(self, exp: Experiment, seed: int) -> List[str]:
        cmd = [
            "python", "-m", "src.tuner.main",
            "--config", exp.config_profile,
            "--tier", exp.knob_tier,
            "--knob-source", exp.knob_source,
            "--benchmark", exp.benchmark,
            "--random-seed", str(seed),
            "--tuning-mode", exp.tuning_mode,
            "--force-recreate-instances",
            "--cleanup-instances",
            "--worker-ram", self.worker_ram,
            "--worker-cpus", str(self.worker_cpus),
            "--verbose", "INFO"
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
            
        return cmd

    def _build_bo_cmd(self, pbt_session: Optional[Path], seed: int) -> List[str]:
        cmd = [
            "python", "-m", "src.scripts.bo_baseline",
            "--seed", str(seed),
            "--force-recreate-instances",
            "--cleanup-instances",
            "--worker-ram", self.worker_ram,
            "--worker-cpus", str(self.worker_cpus),
            "--verbose", "INFO"
        ]
        if pbt_session:
            cmd.extend(["--pbt-session", str(pbt_session)])
        elif self.dry_run:
            cmd.extend(["--pbt-session", "DRY_RUN_PBT_SESSION_PATH.json"])
        return cmd

    def _build_eval_cmd(self, pbt_session: Optional[Path], bo_session: Optional[Path], repetitions: int, seed: int) -> List[str]:
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
