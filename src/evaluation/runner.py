# Copyright (C) 2026 Ibrahim Al-Shawa and PBTune contributors
# Licensed under the GNU General Public License v3.0
# See LICENSE file for details

"""
Comparison Runner — Core Orchestrator
======================================

Drives the end-to-end comparative evaluation pipeline:

1. Load the PBT tuning session → extract best knobs + resource constraints.
2. Create a DatabaseEnvironment (Docker or bare-metal) via EnvironmentFactory.
3. Run N repetitions with the **default** PostgreSQL configuration,
   each in a fresh container (Docker) or a re-initialized instance (bare-metal).
4. Run N repetitions with the **tuned** configuration the same way.
5. Compute non-parametric statistical comparison (Wilcoxon, bootstrap CI,
   primary endpoint at alpha, Holm-corrected secondary endpoints, Cohen's d).
6. Write the full result to `results/{workload}/comparisons/{tier}`.
7. Print a formatted summary table to stdout.

Fresh-per-run strategy
----------------------
Every repetition, regardless of configuration type, starts from a
clean-slate database with its own container/instance. This prevents
cache warming, index bloat, and other state-accumulation effects from
leaking between measurements — the most rigorous isolation achievable
without hardware-level separation.
"""

from __future__ import annotations

import dataclasses
import json
import platform
import re
import shutil
import time
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

from src.config.database import DatabaseConfig, get_db_config
from src.utils.environments import EnvironmentFactory, DatabaseEnvironment
from src.utils.hardware_info import WorkerResources as RuntimeWorkerResources
from src.utils.logger import add_html_file_logging, get_evaluation_banner, get_logger
from src.utils.metrics import PerformanceMetrics, create_metric_config
from src.utils.scoring import create_scoring_engine
from src.config.data_root import resolve_data_root
from src.utils.calibration import rescore_metrics_globally
from src.benchmarks.sysbench.executor import (
    SysbenchExecutor,
    DEFAULT_SYSBENCH_WORKLOAD,
    validate_sysbench_workload,
)
from src.benchmarks.tpch.executor import TPCHExecutor
from src.benchmarks.executor import BenchmarkExecutor, ExecutionContext
from src.knobs import get_knob_space
from src.utils.applicator import ApplicatorConfig, KnobApplicator
from src.evaluation.exceptions import DockerEnvironmentError
from src.evaluation.loader import load_tuning_session
from src.evaluation.statistics import (
    compute_comparison_statistics,
    compute_pairwise_statistics,
)
from src.evaluation.types import (
    ComparisonConfig,
    ComparisonResult,
    MultiArmComparisonResult,
    PairwiseResult,
    RunResult,
    TuningSessionData,
    WorkerResources,
)

LOGGER = get_logger("Runner")


class ComparisonRunner:
    """
    Orchestrates the full default-vs-tuned benchmark comparison.

    Args:
        config: ``ComparisonConfig`` specifying the tuning session path,
            benchmark parameters, repetition count, and output directory.

    Example::

        runner = ComparisonRunner(ComparisonConfig(
            tuning_session_path=Path(
                "results/sessions/olap/pbt/extensive/traces/"
                "pbt_results_20260326_2115.json"
            ),
            repetitions=5,
        ))
        result = runner.run()
        print(f"Overall improvement: {result.statistics.overall_improvement_pct:+.1f}%")
    """

    def __init__(self, config: ComparisonConfig) -> None:
        """Initialize ComparisonRunner with configuration."""
        self.config = config
        self.base_db_config: DatabaseConfig | None = None
        self.timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        self._session_log_path: Path | None = None

    def run(self) -> ComparisonResult:
        """
        Execute the full comparison pipeline and return the result.

        Returns:
            ComparisonResult containing all run data, statistics, and the
            path where the JSON result was saved.

        Raises:
            TuningSessionLoadError: If the session JSON is invalid.
            DockerEnvironmentError: If Docker is required but unavailable.
            EvaluationError: If a benchmark run fails fatally.
        """
        session = load_tuning_session(self.config.tuning_session_path)

        benchmark = self.config.benchmark or session.benchmark
        if benchmark not in {"sysbench", "tpch"}:
            raise ValueError(
                f"Unsupported benchmark '{benchmark}'. Expected 'sysbench' or 'tpch'."
            )

        effective_params = self._resolve_effective_benchmark_params(session, benchmark)
        self.config = dataclasses.replace(
            self.config,
            benchmark=benchmark,
            scale_factor=float(effective_params["scale_factor"]),
            sysbench_duration=int(effective_params["sysbench_duration"]),
            sysbench_tables=int(effective_params["sysbench_tables"]),
            sysbench_table_size=int(effective_params["sysbench_table_size"]),
            sysbench_workload=str(effective_params["sysbench_workload"]),
            sysbench_warmup_seconds=int(effective_params["sysbench_warmup_seconds"]),
            tpch_warmup_passes=int(effective_params["tpch_warmup_passes"]),
        )

        self._validate_docker_prerequisites()

        session_tuning_mode = session.tuning_config.get("tuning_mode", "").lower()
        if session_tuning_mode == "adaptive":
            LOGGER.warning(
                "Source tuning session used ADAPTIVE mode. "
                "Best-config knobs may include restart-required values that were "
                "never active during evaluation (phantom-config risk). "
                "Verify results carefully."
            )

        output_dir = self._resolve_output_dir_for(session)
        log_path = self._resolve_log_output_path(output_dir)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._session_log_path = add_html_file_logging(log_path)

        banner = get_evaluation_banner(
            session_name=self.config.tuning_session_path.name,
            benchmark=benchmark,
            repetitions=self.config.repetitions,
            env_type="Docker" if self.config.use_docker else "bare-metal",
        )
        LOGGER.info("\n%s", banner)
        LOGGER.info("  HTML Log: %s", self._session_log_path)
        if benchmark == "sysbench":
            LOGGER.info(
                "  Effective Sysbench params: workload=%s tables=%d table_size=%d"
                " duration=%ds warmup=%ds",
                self.config.sysbench_workload,
                self.config.sysbench_tables,
                self.config.sysbench_table_size,
                self.config.sysbench_duration,
                self.config.sysbench_warmup_seconds,
            )
        else:
            LOGGER.info(
                "  Effective TPC-H params: scale_factor=%.2f warmup_passes=%d",
                self.config.scale_factor,
                self.config.tpch_warmup_passes,
            )

        tuned_knobs = self._resolve_tuned_knobs(session)

        LOGGER.info("\n── Tuned Configuration Applied ──")
        for _k, _v in sorted(tuned_knobs.items()):
            LOGGER.info("  %-40s = %s", _k, _v)
        LOGGER.info("── (%d knobs total) ──", len(tuned_knobs))

        executor = self._create_executor()

        eval_policy = self.config.scoring_policy or session.scoring_policy
        eval_policy_version = (
            self.config.scoring_policy_version or session.scoring_policy_version
        )
        eval_ref_version = (
            self.config.metric_reference_version or session.metric_reference_version
        )

        LOGGER.info(
            "Running paired default/tuned comparisons for %d repetitions...",
            self.config.repetitions,
        )
        default_runs, tuned_runs = self._run_paired_comparisons(
            tuned_knobs=tuned_knobs,
            session=session,
            executor=executor,
            scoring_policy=eval_policy,
            scoring_policy_version=eval_policy_version,
            metric_reference_version=eval_ref_version,
        )

        all_runs = sorted(
            [*default_runs, *tuned_runs],
            key=lambda r: (r.run_number, r.order_in_pair, r.config_type),
        )

        if (
            eval_policy != session.scoring_policy
            or eval_policy_version != session.scoring_policy_version
            or eval_ref_version != session.metric_reference_version
        ):
            LOGGER.warning(
                "Mixed-version scoring detected! The tuning session was run with "
                "[%s v%s, ref %s], but evaluation is using "
                "[%s v%s, ref %s]. Results may not align with original tuning incentives.",
                session.scoring_policy,
                session.scoring_policy_version,
                session.metric_reference_version,
                eval_policy,
                eval_policy_version,
                eval_ref_version,
            )

        # Score both arms (default vs tuned) under the STATIC workload-feature
        # prior, not the PBT session's drifted vector. PBT refines features
        # via EMA every generation; using its drifted vector to grade the
        # head-to-head bakes whatever direction PBT happened to drift into
        # the rubric, which is asymmetric since the default arm never had a
        # chance to influence those features.
        _, rescored_scores, scoring_metadata = rescore_metrics_globally(
            [r.metrics for r in all_runs],
            benchmark=benchmark,
            padding_factor=0.0,
            scoring_policy=eval_policy,
            scoring_policy_version=eval_policy_version,
            metric_reference_version=eval_ref_version,
            workload_features=None,
        )
        for run, score in zip(all_runs, rescored_scores, strict=True):
            run.score = score

        LOGGER.info("\n── Statistical analysis ──")
        statistics = compute_comparison_statistics(
            default_runs,
            tuned_runs,
            benchmark=benchmark,
        )

        result = ComparisonResult(
            default_runs,
            tuned_runs,
            tuned_knobs,
            statistics,
            self.config,
            session,
            self.timestamp,
            log_path=self._session_log_path,
            scoring_metadata=scoring_metadata,
            session_scoring_metadata={
                "scoring_policy": session.scoring_policy,
                "scoring_policy_version": session.scoring_policy_version,
                "metric_reference_version": session.metric_reference_version,
                "workload_features": session.workload_features,
                "normalization_metadata": session.normalization_metadata,
                "score_breakdown": session.score_breakdown.to_dict(),
            },
        )

        output_path = self._save_result(result)
        result.output_path = output_path
        self._print_summary(result)

        return result

    def run_multi_arm(self) -> MultiArmComparisonResult:
        """
        Execute a multi-arm comparison (e.g. Default vs. PBT vs. BO).

        Loads the primary PBT session and the optional BO session, validates
        that both sessions are compatible, then runs all arms under identical
        conditions sharing deterministic seeds per repetition.

        Returns:
            MultiArmComparisonResult with per-arm runs and pairwise statistics.
        """
        pbt_session = load_tuning_session(self.config.tuning_session_path)

        bo_session = None
        if self.config.bo_session_path:
            bo_session = load_tuning_session(self.config.bo_session_path)
            self._validate_session_compatibility(pbt_session, bo_session)

        benchmark = self.config.benchmark or pbt_session.benchmark
        if benchmark not in {"sysbench", "tpch"}:
            raise ValueError(
                f"Unsupported benchmark '{benchmark}'. Expected 'sysbench' or 'tpch'."
            )

        effective_params = self._resolve_effective_benchmark_params(
            pbt_session, benchmark
        )
        self.config = dataclasses.replace(
            self.config,
            benchmark=benchmark,
            scale_factor=float(effective_params["scale_factor"]),
            sysbench_duration=int(effective_params["sysbench_duration"]),
            sysbench_tables=int(effective_params["sysbench_tables"]),
            sysbench_table_size=int(effective_params["sysbench_table_size"]),
            sysbench_workload=str(effective_params["sysbench_workload"]),
            sysbench_warmup_seconds=int(effective_params["sysbench_warmup_seconds"]),
            tpch_warmup_passes=int(effective_params["tpch_warmup_passes"]),
        )

        self._validate_docker_prerequisites()

        output_dir = self._resolve_output_dir_for(pbt_session)
        log_path = self._resolve_log_output_path(output_dir)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._session_log_path = add_html_file_logging(log_path)

        arm_count = 2 + (1 if bo_session else 0)
        banner = get_evaluation_banner(
            session_name=self.config.tuning_session_path.name,
            benchmark=benchmark,
            repetitions=self.config.repetitions,
            env_type="Docker" if self.config.use_docker else "bare-metal",
        )
        LOGGER.info("\n%s", banner)
        LOGGER.info("  Mode     : Multi-arm comparison (%d arms)", arm_count)
        LOGGER.info("  HTML Log : %s", self._session_log_path)

        pbt_knobs = self._resolve_tuned_knobs(pbt_session)
        arms: dict[str, dict[str, Any]] = {"default": {}, "pbt": pbt_knobs}
        knobs_by_arm: dict[str, dict[str, Any]] = {"default": {}, "pbt": pbt_knobs}

        LOGGER.info("\n── Tuned Configuration Applied (PBT) ──")
        for _k, _v in sorted(pbt_knobs.items()):
            LOGGER.info("  %-40s = %s", _k, _v)
        LOGGER.info("── (%d knobs total) ──", len(pbt_knobs))

        if bo_session:
            bo_knobs = self._resolve_tuned_knobs(bo_session)
            arms["bo"] = bo_knobs
            knobs_by_arm["bo"] = bo_knobs
            LOGGER.info("\n── Tuned Configuration Applied (BO) ──")
            for _k, _v in sorted(bo_knobs.items()):
                LOGGER.info("  %-40s = %s", _k, _v)
            LOGGER.info("── (%d knobs total) ──", len(bo_knobs))

        executor = self._create_executor()

        eval_policy = self.config.scoring_policy or pbt_session.scoring_policy
        eval_policy_version = (
            self.config.scoring_policy_version or pbt_session.scoring_policy_version
        )
        eval_ref_version = (
            self.config.metric_reference_version or pbt_session.metric_reference_version
        )

        LOGGER.info(
            "Running multi-arm comparisons (%d arms, %d repetitions)...",
            len(arms),
            self.config.repetitions,
        )
        runs_by_arm = self._run_multi_arm_repetitions(
            arms=arms,
            session=pbt_session,
            executor=executor,
            scoring_policy=eval_policy,
            scoring_policy_version=eval_policy_version,
            metric_reference_version=eval_ref_version,
        )

        all_runs = sorted(
            [r for arm_runs in runs_by_arm.values() for r in arm_runs],
            key=lambda r: (r.run_number, r.order_in_pair, r.config_type),
        )
        # Multi-arm head-to-head: score every arm under the SAME static
        # workload-feature prior. Using the PBT session's drifted vector
        # (refined via EMA every generation during PBT training) would
        # give PBT a co-adapted rubric while BO is graded on a vector it
        # never trained against. workload_features=None falls back to the
        # workload-type-conditioned base prior in create_metric_config,
        # which is symmetric across arms.
        _, rescored_scores, scoring_metadata = rescore_metrics_globally(
            [r.metrics for r in all_runs],
            benchmark=benchmark,
            padding_factor=0.0,
            scoring_policy=eval_policy,
            scoring_policy_version=eval_policy_version,
            metric_reference_version=eval_ref_version,
            workload_features=None,
        )
        for run, score in zip(all_runs, rescored_scores, strict=True):
            run.score = score

        LOGGER.info("\n── Pairwise statistical analysis ──")
        pairwise_results = compute_pairwise_statistics(runs_by_arm, benchmark=benchmark)

        result = MultiArmComparisonResult(
            runs_by_arm=runs_by_arm,
            knobs_by_arm=knobs_by_arm,
            pairwise_statistics=pairwise_results,
            config=self.config,
            session_data=pbt_session,
            bo_session_data=bo_session,
            timestamp=self.timestamp,
            log_path=self._session_log_path,
            scoring_metadata=scoring_metadata,
            session_scoring_metadata={
                "pbt": {
                    "scoring_policy": pbt_session.scoring_policy,
                    "scoring_policy_version": pbt_session.scoring_policy_version,
                    "metric_reference_version": pbt_session.metric_reference_version,
                },
                **(
                    {
                        "bo": {
                            "scoring_policy": bo_session.scoring_policy,
                            "scoring_policy_version": bo_session.scoring_policy_version,
                            "metric_reference_version": bo_session.metric_reference_version,
                        }
                    }
                    if bo_session
                    else {}
                ),
            },
        )

        output_path = self._save_multi_arm_result(result)
        result.output_path = output_path
        self._print_multi_arm_summary(result)

        return result

    @staticmethod
    def _validate_session_compatibility(
        primary: TuningSessionData, secondary: TuningSessionData
    ) -> None:
        """Verify that two tuning sessions can be evaluated together."""
        if primary.benchmark != secondary.benchmark:
            raise ValueError(
                f"Benchmark mismatch: primary={primary.benchmark}, "
                f"secondary={secondary.benchmark}. "
                "Multi-arm comparison requires identical benchmark types."
            )
        if primary.workload_type != secondary.workload_type:
            LOGGER.warning(
                "Workload type mismatch: primary=%s, secondary=%s. "
                "Proceeding, but results may not be directly comparable.",
                primary.workload_type,
                secondary.workload_type,
            )

    @staticmethod
    def _missing_docker_image_help(image_name: str) -> str:
        """Build a concise remediation message for missing evaluation images."""
        return (
            f"Docker image '{image_name}' is not available locally and pull failed. "
            "Build the evaluation image first with:\n"
            f"  docker build -f docker/eval.Dockerfile -t {image_name} docker/\n"
            "Or rerun with --no-docker (reduced isolation)."
        )

    def _validate_docker_prerequisites(self) -> None:
        """Fail fast when Docker evaluation prerequisites are unavailable."""
        if not self.config.use_docker:
            return

        image_name = self.config.docker_image

        try:
            import docker  # local import keeps non-Docker paths lightweight
            from docker import errors as docker_errors
        except ImportError as exc:
            raise DockerEnvironmentError(
                "Docker evaluation requested, but the Docker SDK is unavailable. "
                "Install dependencies with: pip install -r requirements.txt"
            ) from exc

        client = None
        try:
            client = docker.from_env(timeout=30)
            client.ping()

            try:
                client.images.get(image_name)
                return
            except docker_errors.ImageNotFound:
                LOGGER.info(
                    "Docker image '%s' not found locally; attempting to pull once...",
                    image_name,
                )
                try:
                    client.images.pull(image_name)
                    LOGGER.info("Pulled Docker image '%s'.", image_name)
                    return
                except (docker_errors.ImageNotFound, docker_errors.APIError) as exc:
                    raise DockerEnvironmentError(
                        self._missing_docker_image_help(image_name)
                    ) from exc

        except docker_errors.DockerException as exc:
            raise DockerEnvironmentError(
                "Docker evaluation requested, but Docker daemon is unavailable. "
                "Start Docker or rerun with --no-docker."
            ) from exc
        finally:
            if client is not None:
                client.close()

    def _create_executor(self) -> BenchmarkExecutor:
        """
        Create the appropriate BenchmarkExecutor for the configured benchmark.

        This executor serves dual purpose: it is passed to the EnvironmentFactory
        as the `schema_provider` (so environment setup can validate/prepare the
        schema), and it is used directly to execute benchmark measurements.
        """
        if self.config.benchmark == "tpch":
            return TPCHExecutor(scale_factor=float(self.config.scale_factor or 1.0))
        return SysbenchExecutor(
            tables=int(self.config.sysbench_tables or 10),
            table_size=int(self.config.sysbench_table_size or 100_000),
            script=str(self.config.sysbench_workload or DEFAULT_SYSBENCH_WORKLOAD),
        )

    def _resolve_effective_benchmark_params(
        self,
        session: TuningSessionData,
        benchmark: str,
    ) -> dict[str, float | int | str]:
        """
        Resolve effective benchmark runtime parameters using strict precedence.

        Precedence order:
            1. CLI overrides from ComparisonConfig
            2. Session metadata from tuning_session
            3. Benchmark defaults
        """
        defaults: dict[str, float | int] = {
            "scale_factor": 1.0,
            "sysbench_duration": 60,
            "sysbench_tables": 10,
            "sysbench_table_size": 100_000,
            "sysbench_warmup_seconds": 30,
            "tpch_warmup_passes": 1,
        }
        session_cfg = session.tuning_config

        def _pick_int(
            cli_value: Any, session_keys: list[str], default_value: int
        ) -> int:
            """Pick an int from CLI value, session config, or default."""
            if cli_value is not None:
                return int(cli_value)
            for key in session_keys:
                val = session_cfg.get(key)
                if val is not None:
                    try:
                        return int(val)
                    except (TypeError, ValueError):
                        LOGGER.warning(
                            "Ignoring invalid integer value for session key '%s': %r",
                            key,
                            val,
                        )
            return default_value

        def _pick_float(
            cli_value: Any, session_keys: list[str], default_value: float
        ) -> float:
            """Pick a float from CLI value, session config, or default."""
            if cli_value is not None:
                return float(cli_value)
            for key in session_keys:
                val = session_cfg.get(key)
                if val is not None:
                    try:
                        return float(val)
                    except (TypeError, ValueError):
                        LOGGER.warning(
                            "Ignoring invalid float value for session key '%s': %r",
                            key,
                            val,
                        )
            return default_value

        sysbench_workload = self._resolve_sysbench_workload(session, benchmark)

        resolved = {
            "scale_factor": _pick_float(
                self.config.scale_factor,
                ["scale_factor"],
                float(defaults["scale_factor"]),
            ),
            "sysbench_duration": _pick_int(
                self.config.sysbench_duration,
                [
                    "sysbench_duration_seconds",
                    "sysbench_duration",
                    "evaluation_duration",
                ],
                int(defaults["sysbench_duration"]),
            ),
            "sysbench_tables": _pick_int(
                self.config.sysbench_tables,
                ["sysbench_tables"],
                int(defaults["sysbench_tables"]),
            ),
            "sysbench_table_size": _pick_int(
                self.config.sysbench_table_size,
                ["sysbench_table_size"],
                int(defaults["sysbench_table_size"]),
            ),
            "sysbench_warmup_seconds": _pick_int(
                self.config.sysbench_warmup_seconds,
                ["sysbench_warmup_seconds", "warmup_duration"],
                int(defaults["sysbench_warmup_seconds"]),
            ),
            "tpch_warmup_passes": _pick_int(
                self.config.tpch_warmup_passes,
                ["tpch_warmup_passes", "warmup_passes"],
                int(defaults["tpch_warmup_passes"]),
            ),
            "sysbench_workload": sysbench_workload,
        }

        if benchmark == "tpch":
            # Keep sysbench fields resolved for metadata completeness.
            resolved["sysbench_duration"] = int(str(resolved["sysbench_duration"]))  # type: ignore
        return resolved  # type: ignore

    def _resolve_sysbench_workload(
        self,
        session: TuningSessionData,
        benchmark: str,
    ) -> str:
        """Resolve sysbench workload mode using CLI -> session -> default precedence."""
        if benchmark != "sysbench":
            return DEFAULT_SYSBENCH_WORKLOAD

        if self.config.sysbench_workload is not None:
            return validate_sysbench_workload(self.config.sysbench_workload)

        if session.sysbench_workload is not None:
            return validate_sysbench_workload(session.sysbench_workload)

        session_cfg_mode = session.tuning_config.get("sysbench_workload")
        if session_cfg_mode is not None:
            try:
                return validate_sysbench_workload(str(session_cfg_mode))
            except ValueError:
                LOGGER.warning(
                    "Ignoring invalid sysbench_workload value from tuning config: %r",
                    session_cfg_mode,
                )

        return DEFAULT_SYSBENCH_WORKLOAD

    def _build_environment(
        self,
        executor: BenchmarkExecutor,
        worker_resources: WorkerResources,
    ) -> DatabaseEnvironment:
        """
        Create an evaluation environment via EnvironmentFactory.

        Each call creates a fresh environment so every benchmark repetition
        starts from a clean-slate database.
        """
        if self.base_db_config is None:
            self.base_db_config = get_db_config()

        data_root = resolve_data_root(cli_override=self.config.data_dir)

        return EnvironmentFactory.create(
            schema_provider=executor,
            use_docker=self.config.use_docker,
            base_dir=data_root,
            db_config=self.base_db_config,
            worker_resources=worker_resources,
            run_id=f"eval_{self.timestamp}",
            container_prefix="eval-worker",
            image_name=self.config.docker_image,
            force_recreate_baseline=self.config.force_recreate_baseline,
        )

    def _resolve_tuned_knobs(self, session: TuningSessionData) -> dict[str, Any]:
        """
        Resolve tuned knob values from serialized fractions to absolute values.

        Tuning sessions persist hardware-relative knobs as fractions so they can
        transfer across machines. Evaluation must convert them back to absolute
        PostgreSQL values for the local worker resource constraints.
        """
        LOGGER.debug("Resolving tuned knobs for evaluation session...")
        tier = self._resolve_tier_slug_from_session(
            session, self.config.tuning_session_path
        )
        if tier == "unknown":
            LOGGER.warning(
                "➤ Could not infer knob tier for session %s; "
                "applying stored knob values as-is.",
                self.config.tuning_session_path.name,
            )
            return dict(session.best_knobs)

        if session.benchmark == "sysbench":
            resolved_workload_type = session.sysbench_workload or "oltp_read_write"
        elif session.benchmark == "tpch":
            resolved_workload_type = "olap"
        else:
            resolved_workload_type = session.workload_type

        try:
            knob_space = get_knob_space(
                tier,
                knob_source=session.knob_source,
                workload_type=resolved_workload_type,
            )
        except Exception as exc:
            LOGGER.warning(
                "Failed to load knob space for tier '%s': %s. Applying stored knob values as-is.",
                tier,
                exc,
            )
            return dict(session.best_knobs)

        runtime_resources = RuntimeWorkerResources(
            ram_bytes=session.worker_resources.ram_bytes,
            cpu_cores=session.worker_resources.cpu_cores,
            disk_type=session.worker_resources.disk_type,
            disk_read_bps=session.worker_resources.disk_read_bps,
            disk_write_bps=session.worker_resources.disk_write_bps,
            disk_read_iops=session.worker_resources.disk_read_iops,
            disk_write_iops=session.worker_resources.disk_write_iops,
            disk_class=session.worker_resources.disk_class,
        )
        knob_space.resolve_hardware_ranges(runtime_resources)
        resolved = knob_space.fractions_to_config(session.best_knobs)

        LOGGER.debug(
            "➤ Resolved tuned knobs from fractional representation using tier=%s.",
            tier,
        )
        return resolved

    def _run_paired_comparisons(
        self,
        tuned_knobs: dict[str, Any],
        session: TuningSessionData,
        executor: BenchmarkExecutor,
        scoring_policy: str,
        scoring_policy_version: str,
        metric_reference_version: str,
    ) -> tuple[list[RunResult], list[RunResult]]:
        """
        Execute strict paired runs where each pair shares one deterministic seed.

        For pair i, both default and tuned runs use seed = pair_seed_base + i - 1.
        """
        default_runs: list[RunResult] = []
        tuned_runs: list[RunResult] = []
        failed_pairs = 0

        for run_number in range(1, self.config.repetitions + 1):
            pair_seed = self.config.pair_seed + run_number - 1
            LOGGER.info(
                "[Pair %d/%d] seed=%d",
                run_number,
                self.config.repetitions,
                pair_seed,
            )

            try:
                default_run = self._run_single(
                    config_type="default",
                    knobs={},
                    run_number=run_number,
                    pair_seed=pair_seed,
                    order_in_pair=1,
                    session=session,
                    executor=executor,
                    scoring_policy=scoring_policy,
                    scoring_policy_version=scoring_policy_version,
                    metric_reference_version=metric_reference_version,
                )
                tuned_run = self._run_single(
                    config_type="tuned",
                    knobs=tuned_knobs,
                    run_number=run_number,
                    pair_seed=pair_seed,
                    order_in_pair=2,
                    session=session,
                    executor=executor,
                    scoring_policy=scoring_policy,
                    scoring_policy_version=scoring_policy_version,
                    metric_reference_version=metric_reference_version,
                )
            except Exception as exc:
                failed_pairs += 1
                LOGGER.warning("➤ Pair %d failed: %s", run_number, exc)
                if failed_pairs > self.config.repetitions // 2:
                    raise RuntimeError(
                        "More than half of run pairs failed "
                        f"({failed_pairs}/{self.config.repetitions})."
                    ) from exc
                continue

            default_runs.append(default_run)
            tuned_runs.append(tuned_run)

            _d, _t = default_run, tuned_run
            LOGGER.info(
                "➤ Pair %d complete  [%.1fs | %.1fs]:\n"
                "     default : score=%8.3f  p95=%9.2f ms  tps=%9.1f  mem=%6.1f%%\n"
                "     tuned   : score=%8.3f  p95=%9.2f ms  tps=%9.1f  mem=%6.1f%%",
                run_number,
                _d.duration_seconds,
                _t.duration_seconds,
                _d.score,
                _d.metrics.latency_p95,
                _d.metrics.throughput,
                _d.metrics.memory_utilization * 100.0,
                _t.score,
                _t.metrics.latency_p95,
                _t.metrics.throughput,
                _t.metrics.memory_utilization * 100.0,
            )

        if not default_runs or not tuned_runs:
            raise RuntimeError("All paired runs failed.")
        if len(default_runs) != len(tuned_runs):
            raise RuntimeError(
                "Paired comparison integrity violation: default/tuned lengths differ."
            )

        LOGGER.info(
            "➤ Paired execution complete: %d successful pairs / %d requested.",
            len(default_runs),
            self.config.repetitions,
        )
        return default_runs, tuned_runs

    def _fetch_boot_values(
        self, env: DatabaseEnvironment, param_names: list[str]
    ) -> dict[str, str]:
        """Fetch the PostgreSQL default boot values for the specified parameters."""
        if not param_names:
            return {}
        try:
            active_config = env.get_db_config(worker_id=0)
            from src.database.connection import get_connection

            conn = get_connection(config=active_config)
            cursor = conn.cursor()
            placeholders = ",".join(["%s"] * len(param_names))
            cursor.execute(
                f"SELECT name, boot_val FROM pg_settings WHERE name IN ({placeholders})",
                param_names,
            )
            results = {row[0]: row[1] for row in cursor.fetchall()}
            cursor.close()
            conn.close()
            return results
        except Exception as e:
            LOGGER.debug("Failed to fetch boot_values: %s", e)
            return {}

    def _run_multi_arm_repetitions(
        self,
        arms: dict[str, dict[str, Any]],
        session: TuningSessionData,
        executor: BenchmarkExecutor,
        scoring_policy: str,
        scoring_policy_version: str,
        metric_reference_version: str,
    ) -> dict[str, list[RunResult]]:
        """
        Execute N repetitions across all arms, sharing seeds per repetition.

        For each repetition, every arm is evaluated with the same pair_seed,
        preserving the paired experimental design for pairwise Wilcoxon tests.
        """
        runs_by_arm: dict[str, list[RunResult]] = {name: [] for name in arms}
        arm_order = sorted(arms.keys())
        failed_reps = 0

        for run_number in range(1, self.config.repetitions + 1):
            pair_seed = self.config.pair_seed + run_number - 1
            LOGGER.info(
                "[Rep %d/%d] seed=%d, arms=%s",
                run_number,
                self.config.repetitions,
                pair_seed,
                arm_order,
            )

            rep_runs: dict[str, RunResult] = {}
            try:
                for order, arm_name in enumerate(arm_order, start=1):
                    run = self._run_single(
                        config_type=arm_name,
                        knobs=arms[arm_name],
                        run_number=run_number,
                        pair_seed=pair_seed,
                        order_in_pair=order,
                        session=session,
                        executor=executor,
                        scoring_policy=scoring_policy,
                        scoring_policy_version=scoring_policy_version,
                        metric_reference_version=metric_reference_version,
                    )
                    rep_runs[arm_name] = run
            except Exception as exc:
                failed_reps += 1
                LOGGER.warning("➤ Repetition %d failed: %s", run_number, exc)
                if failed_reps > self.config.repetitions // 2:
                    raise RuntimeError(
                        "More than half of repetitions failed "
                        f"({failed_reps}/{self.config.repetitions})."
                    ) from exc
                continue

            for arm_name, run in rep_runs.items():
                runs_by_arm[arm_name].append(run)

            summary_parts = []
            for arm_name in arm_order:
                r = rep_runs[arm_name]
                summary_parts.append(
                    f"{arm_name}(score={r.score:8.3f}, "
                    f"p95={r.metrics.latency_p95:9.2f}ms, "
                    f"tps={r.metrics.throughput:9.1f}, "
                    f"mem={r.metrics.memory_utilization * 100.0:6.1f}%, "
                    f"dur={r.duration_seconds:6.1f}s)"
                )
            LOGGER.info(
                "➤ Rep %d complete:\n     %s", run_number, "\n     ".join(summary_parts)
            )

        total_successful = min(len(v) for v in runs_by_arm.values())
        if total_successful == 0:
            raise RuntimeError("All multi-arm repetitions failed.")

        lengths = {name: len(runs) for name, runs in runs_by_arm.items()}
        if len(set(lengths.values())) > 1:
            raise RuntimeError(
                f"Arm run-count mismatch: {lengths}. "
                "All arms must have equal repetition counts."
            )

        LOGGER.info(
            "➤ Multi-arm execution complete: %d successful reps / %d requested, "
            "%d arms.",
            total_successful,
            self.config.repetitions,
            len(arms),
        )
        return runs_by_arm

    def _run_single(
        self,
        config_type: str,
        knobs: dict[str, Any],
        run_number: int,
        pair_seed: int,
        order_in_pair: int,
        session: TuningSessionData,
        executor: BenchmarkExecutor,
        scoring_policy: str,
        scoring_policy_version: str,
        metric_reference_version: str,
    ) -> RunResult:
        """
        Execute one benchmark repetition in a fresh environment.

        Lifecycle per run:
        1. Create fresh environment → setup_instances(1, force_recreate=True) \\
           (this also initializes the schema via the executor/schema_provider)
        2. Apply tuned knobs if config_type == "tuned"
        3. Execute benchmark measurement
        4. Tear down environment (stop + cleanup)
        """
        benchmark_name = self.config.benchmark or session.benchmark
        run_started = time.monotonic()
        LOGGER.info(
            "  Starting single run: %s (Rep %d, order %d) in container %s",
            config_type,
            run_number,
            order_in_pair,
            f"eval_{self.timestamp}",
        )
        env = self._build_environment(executor, session.worker_resources)

        try:
            setup_started = time.monotonic()
            env.setup_instances(
                num_workers=1, force_recreate=True, num_parallel_workers=1
            )
            active_config = env.get_db_config(worker_id=0)

            if knobs:
                applicator_config = ApplicatorConfig(rollback_on_error=False)
                knob_applicator = KnobApplicator(
                    db_config=active_config,
                    config=applicator_config,
                    worker_id=0,
                )

                boot_values = self._fetch_boot_values(env, list(knobs.keys()))

                apply_result = knob_applicator.apply(knobs)

                for k, v in knobs.items():
                    boot_val = boot_values.get(k)
                    if boot_val is not None and str(v) != boot_val:
                        LOGGER.debug(
                            "  Knob '%s': boot_val=%s, applied_val=%s", k, boot_val, v
                        )

                if apply_result.restart_required:
                    restart_started = time.monotonic()
                    LOGGER.info(
                        "  Restart-required knobs applied (%s); restarting instance...",
                        list(apply_result.restart_required),
                    )
                    if not env.restart_instance(worker_id=0):
                        raise RuntimeError(
                            "Failed to restart instance after applying "
                            f"restart-required knobs: {list(apply_result.restart_required)}"
                        )
                    LOGGER.info(
                        "  Instance restarted in %.1fs",
                        time.monotonic() - restart_started,
                    )

                    # Refresh config after restart and rebind applicator.
                    active_config = env.get_db_config(worker_id=0)
                    knob_applicator = KnobApplicator(
                        db_config=active_config,
                        config=applicator_config,
                        worker_id=0,
                    )

                verification = knob_applicator.verify(knobs)
                if verification.failed_params:
                    LOGGER.warning(
                        "Configuration verification failed for %d parameters: %s",
                        len(verification.failed_params),
                        verification.failed_params,
                    )

            setup_elapsed = time.monotonic() - setup_started
            LOGGER.debug("  Environment setup completed in %.1fs", setup_elapsed)

            bench_started = time.monotonic()
            if benchmark_name == "tpch":
                ctx = ExecutionContext(
                    db_config=active_config,
                    duration=0.0,
                    warmup_passes=int(self.config.tpch_warmup_passes or 1),
                )
            else:
                ctx = ExecutionContext(
                    db_config=active_config,
                    duration=int(self.config.sysbench_duration or 60),
                    warmup=int(self.config.sysbench_warmup_seconds or 30),
                    random_seed=pair_seed,
                )
            metrics = executor.execute(ctx)
            bench_elapsed = time.monotonic() - bench_started
            LOGGER.debug(
                "  Benchmark execution completed in %.1fs (setup=%.1fs)",
                bench_elapsed,
                setup_elapsed,
            )

            metrics.memory_utilization = env.collect_memory_utilization(worker_id=0)

            score = _metrics_to_score(
                metrics,
                benchmark_name,
                scoring_policy=scoring_policy,
                scoring_policy_version=scoring_policy_version,
                metric_reference_version=metric_reference_version,
                workload_features=session.workload_features,
            )

            return RunResult(
                config_type=config_type,
                run_number=run_number,
                pair_seed=pair_seed,
                order_in_pair=order_in_pair,
                metrics=metrics,
                score=score,
                duration_seconds=time.monotonic() - run_started,
                container_id=env.run_id,
            )
        finally:
            # Always tear down, even on failure
            try:
                env.stop_all()
            except Exception as stop_exc:
                LOGGER.debug("Error stopping environment: %s", stop_exc)
            try:
                env.cleanup(remove_data=True)
            except Exception as cleanup_exc:
                LOGGER.debug("Error cleaning up environment: %s", cleanup_exc)

    def _save_result(self, result: ComparisonResult) -> Path:
        """
        Serialize the ComparisonResult to JSON and write to disk.

        Output path: ``results/{workload}/comparisons/{tier}/comparison_{timestamp}.json``

        Args:
            result: The fully populated ComparisonResult.

        Returns:
            Path to the written file.
        """
        output_dir = self._resolve_output_dir(result)
        output_dir.mkdir(parents=True, exist_ok=True)

        output_path = output_dir / f"comparison_{result.timestamp}.json"
        payload = _serialize_result(result)

        with output_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)

        LOGGER.info("Results saved to: %s", output_path)
        return output_path

    def _resolve_output_dir(self, result: ComparisonResult) -> Path:
        """Determine the output directory, creating it if necessary."""
        return self._resolve_output_dir_for(result.session_data)

    def _resolve_output_dir_for(
        self,
        session_data: TuningSessionData,
    ) -> Path:
        """Determine the output directory for this evaluation session."""
        if self.config.output_dir:
            return self.config.output_dir

        tier = self._resolve_tier_slug_from_session(
            session_data, self.config.tuning_session_path
        )

        data_root = resolve_data_root(cli_override=self.config.data_dir)
        base_dir = (
            data_root / "results" if self.config.colocate_output else Path("results")
        )

        # Derive granular workload key (same convention as tuner CLI)
        benchmark = self.config.benchmark or session_data.benchmark
        if benchmark == "sysbench":
            workload = str(
                self.config.sysbench_workload
                or session_data.sysbench_workload
                or DEFAULT_SYSBENCH_WORKLOAD
            )
        elif benchmark == "tpch":
            workload = "olap"
        else:
            workload = session_data.workload_type.lower()
            if workload not in ("oltp", "olap", "mixed"):
                workload = "mixed"

        return base_dir / "comparisons" / workload / tier

    def _resolve_log_output_path(
        self, output_dir: Path, timestamp: str | None = None
    ) -> Path:
        """Return the HTML log artifact path for this evaluation invocation."""
        effective_ts = timestamp or self.timestamp
        return output_dir / "logs" / f"evaluation_{effective_ts}.html"

    def _resolve_tier_slug(self, result: ComparisonResult) -> str:
        """Infer knob tier slug from a computed result object."""
        return self._resolve_tier_slug_from_session(
            result.session_data,
            result.config.tuning_session_path,
        )

    def _resolve_tier_slug_from_session(
        self,
        session_data: TuningSessionData,
        session_path: Path,
    ) -> str:
        """Infer knob tier slug from session metadata, then from session path."""
        metadata_tier = _sanitize_tier_name(
            session_data.tuning_config.get("knob_tier")
            or session_data.tuning_config.get("tier")
        )
        if metadata_tier:
            return metadata_tier

        parts = session_path.parts
        for anchor in ("pbt", "bo", "lhs", "pbt_runs", "bo_runs", "lhs_runs"):
            try:
                idx = parts.index(anchor)
                if idx + 1 < len(parts):
                    path_tier = _sanitize_tier_name(parts[idx + 1])
                    if path_tier:
                        return path_tier
            except ValueError:
                continue
        return "unknown"

    def _print_summary(self, result: ComparisonResult) -> None:
        """Print a formatted comparison summary table to stdout."""
        stats = result.statistics

        # Header
        print("\n" + "═" * 68)
        print("  EVALUATION SUMMARY")
        print(f"  Session : {result.config.tuning_session_path.name}")
        benchmark_name = result.config.benchmark or result.session_data.benchmark
        print(f"  Benchmark: {benchmark_name.upper()}")
        print(f"  Reps    : {result.config.repetitions}")
        print(f"  Env     : {'Docker' if result.config.use_docker else 'bare-metal'}")
        if benchmark_name == "sysbench":
            print(
                "  Params  : "
                f"tables={result.config.sysbench_tables}, "
                f"table_size={result.config.sysbench_table_size}, "
                f"duration={result.config.sysbench_duration}s, "
                f"warmup={result.config.sysbench_warmup_seconds}s"
            )
        else:
            print(
                "  Params  : "
                f"scale_factor={result.config.scale_factor}, "
                f"warmup_passes={result.config.tpch_warmup_passes}"
            )
        print("═" * 68)

        # Per-metric table  (Median ± SD columns)
        header = (
            f"  {'Metric':<18} {'Default Med ± SD':>18} {'Tuned Med ± SD':>18} "
            f"{'Δ%':>9} {'p (adj)':>9} {'Cohen d':>8} {'Sig':>4}"
        )
        print(header)
        print("  " + "─" * 90)

        for mc in stats.metrics:
            d_str = f"{mc.default.median:.3f} ± {mc.default.std:.3f}"
            t_str = f"{mc.tuned.median:.3f} ± {mc.tuned.std:.3f}"
            imp = mc.improvement_pct
            star = "✓" if mc.significant else " "
            print(
                f"  {mc.metric_name:<18} "
                f"{d_str:>18} "
                f"{t_str:>18} "
                f"{imp:>+9.1f}% "
                f"{mc.p_value_corrected:>9.4f} "
                f"{mc.cohens_d:>8.2f} "
                f"{star:>4}"
            )

        print("  " + "─" * 90)
        print(f"\n  Overall improvement : {stats.overall_improvement_pct:+.1f}%")
        ci_lo, ci_hi = stats.overall_improvement_ci
        print(f"  Bootstrap 95% CI   : [{ci_lo:+.1f}%, {ci_hi:+.1f}%]")
        print(f"  Alpha              : {stats.alpha:.4f}")
        print(f"  Primary endpoint   : {stats.primary_endpoint}")
        print(f"  Primary significant: {'yes' if stats.primary_significant else 'no'}")
        print("  Statistical test   : Wilcoxon signed-rank (paired, two-sided)")
        print(
            "  Secondary endpoints: "
            f"{', '.join(stats.secondary_endpoints) or 'none'} "
            f"({stats.secondary_correction_method} corrected)"
        )
        n_pairs = stats.n_pairs
        print(f"  Paired sample size : N={n_pairs}")
        if stats.power_warning:
            print(f"  Power note         : {stats.power_warning}")
        if result.scoring_metadata:
            print(
                "  Rescoring mode     : "
                f"{result.scoring_metadata.get('mode')} "
                f"(latency={result.scoring_metadata.get('latency_metric')})"
            )
        print(
            f"  Significant metrics: {', '.join(stats.significant_metrics) or 'none'}"
        )
        if result.output_path:
            print(f"\n  Results written to : {result.output_path}")
        if result.log_path:
            print(f"  Session log written: {result.log_path}")
        print("═" * 68 + "\n")

    def _save_multi_arm_result(self, result: MultiArmComparisonResult) -> Path:
        """Serialize a MultiArmComparisonResult to JSON and write to disk."""
        output_dir = self._resolve_output_dir_for(result.session_data)
        output_dir.mkdir(parents=True, exist_ok=True)

        output_path = output_dir / f"multi_arm_comparison_{result.timestamp}.json"
        payload = _serialize_multi_arm_result(result)

        with output_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)

        LOGGER.info("Multi-arm results saved to: %s", output_path)
        return output_path

    def _print_multi_arm_summary(self, result: MultiArmComparisonResult) -> None:
        """Print a formatted multi-arm comparison summary to stdout."""
        benchmark_name = result.config.benchmark or result.session_data.benchmark
        arm_names = sorted(result.runs_by_arm.keys())
        n_reps = len(next(iter(result.runs_by_arm.values())))

        print("\n" + "═" * 90)
        print("  MULTI-ARM EVALUATION SUMMARY")
        print(f"  Arms      : {', '.join(arm_names)}")
        print(f"  Benchmark : {benchmark_name.upper()}")
        print(f"  Reps      : {n_reps}")
        print(f"  Env       : {'Docker' if result.config.use_docker else 'bare-metal'}")
        print("═" * 90)

        import numpy as _np

        header = (
            f"  {'Arm':<12} {'Score Med ± SD':>18} "
            f"{'Throughput Med ± SD':>22} {'Latency p95 Med ± SD':>24}"
        )
        print(header)
        print("  " + "─" * 78)
        for arm_name in arm_names:
            runs = result.runs_by_arm[arm_name]
            scores = [r.score for r in runs]
            tps = [r.metrics.throughput for r in runs]
            lat = [r.metrics.latency_p95 for r in runs]
            sc_str = f"{float(_np.median(scores)):.2f} ± {float(_np.std(scores, ddof=1) if len(scores) > 1 else 0.0):.2f}"
            tp_str = f"{float(_np.median(tps)):.1f} ± {float(_np.std(tps, ddof=1) if len(tps) > 1 else 0.0):.1f}"
            lt_str = f"{float(_np.median(lat)):.2f} ± {float(_np.std(lat, ddof=1) if len(lat) > 1 else 0.0):.2f}"
            print(f"  {arm_name:<12} {sc_str:>18} {tp_str:>22} {lt_str:>24}")
        print("  " + "─" * 78)

        print("\n  PAIRWISE COMPARISONS (score)")
        print("  " + "─" * 74)
        header = (
            f"  {'Pair':<22} {'Δ%':>9} {'95% CI':>18} "
            f"{'p (adj)':>9} {'Cohen d':>8} {'Sig':>4}"
        )
        print(header)
        print("  " + "─" * 74)

        for pw in result.pairwise_statistics:
            score_mc = next(
                mc for mc in pw.statistics.metrics if mc.metric_name == "score"
            )
            label = f"{pw.arm_a} vs {pw.arm_b}"
            ci_lo, ci_hi = score_mc.improvement_ci
            star = "✓" if score_mc.significant else " "
            print(
                f"  {label:<22} "
                f"{score_mc.improvement_pct:>+9.1f}% "
                f"[{ci_lo:>+7.1f}%, {ci_hi:>+7.1f}%] "
                f"{score_mc.p_value_corrected:>9.4f} "
                f"{score_mc.cohens_d:>8.2f} "
                f"{star:>4}"
            )

        print("  " + "─" * 74)
        print("  Statistical test: Wilcoxon signed-rank (paired, two-sided)")
        print(f"  Paired sample size: N={n_reps}")

        if result.output_path:
            print(f"\n  Results written to : {result.output_path}")
        if result.log_path:
            print(f"  Session log written: {result.log_path}")
        print("═" * 90 + "\n")


def _metrics_to_score(
    metrics: PerformanceMetrics,
    benchmark: str,
    scoring_policy: str = "fixed_v1",
    scoring_policy_version: str = "1.0",
    metric_reference_version: str = "1.0",
    workload_features: dict[str, float] | None = None,
) -> float:
    """
    Compute a composite score using the same workload-specific metric model
    used by the tuning loop.

    This ensures that intermediate logging uses the correct scoring policy,
    even though global rescoring is applied at the end.
    """
    if metrics.throughput <= 0.0 or metrics.error_rate >= 1.0:
        return 0.0

    if benchmark == "tpch":
        workload = "olap"
    elif benchmark == "sysbench":
        workload = "oltp"
    else:
        workload = "mixed"

    metric_config = create_metric_config(
        workload,
        scoring_policy=scoring_policy,
        scoring_policy_version=scoring_policy_version,
        metric_reference_version=metric_reference_version,
        workload_features=workload_features,
    )

    return create_scoring_engine(metric_config).compute_breakdown(metrics).final_score


def _extract_pg_major(pg_version_str: str) -> str:
    """
    Extract the major PostgreSQL version number from version strings.

    Examples:
        "PostgreSQL 16.2" → "16"
        "PostgreSQL 18.3" → "18"
        "unknown"         → "16"
    """
    m = re.search(r"(\d+)\.\d+", pg_version_str)
    return m.group(1) if m else "16"


def _sanitize_tier_name(raw_tier: Any) -> str | None:
    """Normalize tier names into stable path-safe slugs."""
    if raw_tier is None:
        return None

    tier = str(raw_tier).strip().lower()
    if not tier:
        return None

    slug = re.sub(r"[^a-z0-9_-]+", "_", tier).strip("_")
    return slug or None


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _serialize_result(result: ComparisonResult) -> dict[str, Any]:
    """Convert ComparisonResult to a plain JSON-serialisable dict."""

    def _pkg_version(package_name: str) -> str:
        """Get the version of an installed package, or 'not-installed' if not found."""
        try:
            return importlib_metadata.version(package_name)
        except importlib_metadata.PackageNotFoundError:
            return "not-installed"

    def _snap(s: PerformanceMetrics) -> dict[str, Any]:
        """Convert PerformanceMetrics to a dict for JSON serialization."""
        return {
            "latency_p50": s.latency_p50,
            "latency_p95": s.latency_p95,
            "latency_p99": s.latency_p99,
            "throughput": s.throughput,
            "error_rate": s.error_rate,
            "memory_utilization": s.memory_utilization,
            "total_queries": s.total_queries,
            "total_time": s.total_time,
        }

    def _run(r: RunResult) -> dict[str, Any]:
        """Convert RunResult to a dict for JSON serialization."""
        return {
            "config_type": r.config_type,
            "run_number": r.run_number,
            "pair_seed": r.pair_seed,
            "order_in_pair": r.order_in_pair,
            "score": r.score,
            "duration_seconds": r.duration_seconds,
            "container_id": r.container_id,
            "metrics": _snap(r.metrics),
        }

    def _stat_sum(s) -> dict[str, Any]:
        """Convert StatSummary to a dict for JSON serialization."""
        return {
            "mean": s.mean,
            "std": s.std,
            "median": s.median,
            "iqr_lower": s.iqr_lower,
            "iqr_upper": s.iqr_upper,
            "values": s.values,
        }

    def _metric_cmp(mc) -> dict[str, Any]:
        """Convert MetricComparison to a dict for JSON serialization."""
        return {
            "metric_name": mc.metric_name,
            "default": _stat_sum(mc.default),
            "tuned": _stat_sum(mc.tuned),
            "improvement_pct": mc.improvement_pct,
            "improvement_ci": list(mc.improvement_ci),
            "p_value": mc.p_value,
            "p_value_corrected": mc.p_value_corrected,
            "cohens_d": mc.cohens_d,
            "significant": mc.significant,
            "higher_is_better": mc.higher_is_better,
            "endpoint_role": mc.endpoint_role,
            "correction_method": mc.correction_method,
        }

    wr = result.session_data.worker_resources
    cfg = result.config
    benchmark_name = cfg.benchmark or result.session_data.benchmark

    return {
        "comparison_metadata": {
            "timestamp": result.timestamp,
            "tuning_session_path": str(result.config.tuning_session_path),
            "evaluation_log_path": str(result.log_path) if result.log_path else None,
            "benchmark": benchmark_name,
            "repetitions": cfg.repetitions,
            "pair_seed_base": cfg.pair_seed,
            "benchmark_parameters": {
                "scale_factor": cfg.scale_factor,
                "sysbench_tables": cfg.sysbench_tables,
                "sysbench_table_size": cfg.sysbench_table_size,
                "sysbench_workload": cfg.sysbench_workload,
                "sysbench_duration": cfg.sysbench_duration,
                "sysbench_warmup_seconds": cfg.sysbench_warmup_seconds,
                "tpch_warmup_passes": cfg.tpch_warmup_passes,
            },
            "evaluation_environment": "docker" if cfg.use_docker else "bare-metal",
            "resource_constraints": {
                "ram_bytes": wr.ram_bytes,
                "cpu_cores": wr.cpu_cores,
                "disk_type": wr.disk_type,
            },
            "reproducibility": {
                "python_version": platform.python_version(),
                "postgres_version": str(
                    result.session_data.system_info.get("pg_version", "unknown")
                ),
                "docker_image": cfg.docker_image if cfg.use_docker else None,
                "benchmark_binary_paths": {
                    "sysbench": shutil.which("sysbench") or "not-found",
                    "psql": shutil.which("psql") or "not-found",
                },
                "python_package_versions": {
                    "docker": _pkg_version("docker"),
                    "psycopg2-binary": _pkg_version("psycopg2-binary"),
                    "numpy": _pkg_version("numpy"),
                    "scipy": _pkg_version("scipy"),
                },
            },
        },
        "tuned_knobs": result.tuned_knobs,
        "default_runs": [_run(r) for r in result.default_runs],
        "tuned_runs": [_run(r) for r in result.tuned_runs],
        "statistics": {
            "metrics": [_metric_cmp(mc) for mc in result.statistics.metrics],
            "alpha": result.statistics.alpha,
            "primary_endpoint": result.statistics.primary_endpoint,
            "primary_significant": result.statistics.primary_significant,
            "secondary_endpoints": result.statistics.secondary_endpoints,
            "secondary_correction_method": result.statistics.secondary_correction_method,
            "correction_method": result.statistics.correction_method,
            "n_pairs": result.statistics.n_pairs,
            "power_warning": result.statistics.power_warning,
            "significant_metrics": result.statistics.significant_metrics,
            "overall_improvement_pct": result.statistics.overall_improvement_pct,
            "overall_improvement_ci": list(result.statistics.overall_improvement_ci),
        },
        "scoring_metadata": result.scoring_metadata,
        "session_info": {
            "session_id": result.session_data.session_id,
            "workload_type": result.session_data.workload_type,
            "best_score_during_tuning": result.session_data.best_score,
            "scoring_policy": result.session_data.scoring_policy,
            "scoring_policy_version": result.session_data.scoring_policy_version,
            "metric_reference_version": result.session_data.metric_reference_version,
        },
        "session_scoring_metadata": result.session_scoring_metadata,
        "system_info": result.session_data.system_info,
    }


def _serialize_multi_arm_result(result: MultiArmComparisonResult) -> dict[str, Any]:
    """Convert MultiArmComparisonResult to a plain JSON-serialisable dict."""

    def _pkg_version(package_name: str) -> str:
        try:
            return importlib_metadata.version(package_name)
        except importlib_metadata.PackageNotFoundError:
            return "not-installed"

    def _snap(s: PerformanceMetrics) -> dict[str, Any]:
        return {
            "latency_p50": s.latency_p50,
            "latency_p95": s.latency_p95,
            "latency_p99": s.latency_p99,
            "throughput": s.throughput,
            "error_rate": s.error_rate,
            "memory_utilization": s.memory_utilization,
            "total_queries": s.total_queries,
            "total_time": s.total_time,
        }

    def _run(r: RunResult) -> dict[str, Any]:
        return {
            "config_type": r.config_type,
            "run_number": r.run_number,
            "pair_seed": r.pair_seed,
            "order_in_pair": r.order_in_pair,
            "score": r.score,
            "duration_seconds": r.duration_seconds,
            "container_id": r.container_id,
            "metrics": _snap(r.metrics),
        }

    def _stat_sum(s) -> dict[str, Any]:
        return {
            "mean": s.mean,
            "std": s.std,
            "median": s.median,
            "iqr_lower": s.iqr_lower,
            "iqr_upper": s.iqr_upper,
            "values": s.values,
        }

    def _metric_cmp(mc) -> dict[str, Any]:
        return {
            "metric_name": mc.metric_name,
            "default": _stat_sum(mc.default),
            "tuned": _stat_sum(mc.tuned),
            "improvement_pct": mc.improvement_pct,
            "improvement_ci": list(mc.improvement_ci),
            "p_value": mc.p_value,
            "p_value_corrected": mc.p_value_corrected,
            "cohens_d": mc.cohens_d,
            "significant": mc.significant,
            "higher_is_better": mc.higher_is_better,
            "endpoint_role": mc.endpoint_role,
            "correction_method": mc.correction_method,
        }

    def _pairwise(pw: PairwiseResult) -> dict[str, Any]:
        s = pw.statistics
        return {
            "arm_a": pw.arm_a,
            "arm_b": pw.arm_b,
            "statistics": {
                "metrics": [_metric_cmp(mc) for mc in s.metrics],
                "alpha": s.alpha,
                "primary_endpoint": s.primary_endpoint,
                "primary_significant": s.primary_significant,
                "secondary_endpoints": s.secondary_endpoints,
                "secondary_correction_method": s.secondary_correction_method,
                "correction_method": s.correction_method,
                "n_pairs": s.n_pairs,
                "power_warning": s.power_warning,
                "significant_metrics": s.significant_metrics,
                "overall_improvement_pct": s.overall_improvement_pct,
                "overall_improvement_ci": list(s.overall_improvement_ci),
            },
        }

    wr = result.session_data.worker_resources
    cfg = result.config
    benchmark_name = cfg.benchmark or result.session_data.benchmark
    arm_names = sorted(result.runs_by_arm.keys())

    pairwise_dict: dict[str, dict[str, Any]] = {}
    for pw in result.pairwise_statistics:
        key = f"{pw.arm_a}_vs_{pw.arm_b}"
        pairwise_dict[key] = _pairwise(pw)

    return {
        "comparison_metadata": {
            "timestamp": result.timestamp,
            "mode": "multi_arm",
            "arms": arm_names,
            "tuning_session_path": str(cfg.tuning_session_path),
            "bo_session_path": str(cfg.bo_session_path)
            if cfg.bo_session_path
            else None,
            "evaluation_log_path": str(result.log_path) if result.log_path else None,
            "benchmark": benchmark_name,
            "repetitions": cfg.repetitions,
            "pair_seed_base": cfg.pair_seed,
            "benchmark_parameters": {
                "scale_factor": cfg.scale_factor,
                "sysbench_tables": cfg.sysbench_tables,
                "sysbench_table_size": cfg.sysbench_table_size,
                "sysbench_workload": cfg.sysbench_workload,
                "sysbench_duration": cfg.sysbench_duration,
                "sysbench_warmup_seconds": cfg.sysbench_warmup_seconds,
                "tpch_warmup_passes": cfg.tpch_warmup_passes,
            },
            "evaluation_environment": "docker" if cfg.use_docker else "bare-metal",
            "resource_constraints": {
                "ram_bytes": wr.ram_bytes,
                "cpu_cores": wr.cpu_cores,
                "disk_type": wr.disk_type,
            },
            "reproducibility": {
                "python_version": platform.python_version(),
                "postgres_version": str(
                    result.session_data.system_info.get("pg_version", "unknown")
                ),
                "docker_image": cfg.docker_image if cfg.use_docker else None,
                "python_package_versions": {
                    "docker": _pkg_version("docker"),
                    "psycopg2-binary": _pkg_version("psycopg2-binary"),
                    "numpy": _pkg_version("numpy"),
                    "scipy": _pkg_version("scipy"),
                },
            },
        },
        "knobs_by_arm": result.knobs_by_arm,
        "runs_by_arm": {
            arm: [_run(r) for r in runs] for arm, runs in result.runs_by_arm.items()
        },
        "pairwise_statistics": pairwise_dict,
        "scoring_metadata": result.scoring_metadata,
        "session_info": {
            "pbt": {
                "session_id": result.session_data.session_id,
                "workload_type": result.session_data.workload_type,
                "best_score_during_tuning": result.session_data.best_score,
            },
            **(
                {
                    "bo": {
                        "session_id": result.bo_session_data.session_id,
                        "workload_type": result.bo_session_data.workload_type,
                        "best_score_during_tuning": result.bo_session_data.best_score,
                    }
                }
                if result.bo_session_data
                else {}
            ),
        },
        "session_scoring_metadata": result.session_scoring_metadata,
        "system_info": result.session_data.system_info,
    }
