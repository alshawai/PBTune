import json
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Pinned seed lists. Frozen here as a single source of truth so a future
# patch to one experiment cannot silently violate the cross-experiment
# seed invariant the paper rests on. Tuples (not lists) so they remain
# immutable and hashable.
SEEDS_K5: tuple[int, ...] = (42, 123, 456, 789, 1024)
SEEDS_K1: tuple[int, ...] = (42,)


@dataclass(frozen=True)
class Experiment:
    id: str                             # e.g. "t1_sysbench_rw"
    tier: int                           # 1, 2, or 3
    description: str
    benchmark: str                      # "sysbench" | "tpch"
    sysbench_workload: str | None    # "oltp_read_write" | ...
    scale_factor: float | None       # TPC-H SF override
    config_profile: str                 # always "thorough"
    knob_tier: str                      # "extensive" | "minimal" | "core" | "standard"
    knob_source: str                    # "expert" | "data_driven"
    tuning_mode: str                    # "offline" | "online"
    seeds: tuple[int, ...]
    eval_repetitions: int               # 10 or 5
    run_bo: bool                        # True for Tier 1/2, False for Tier 3
    population: int | None = None
    generations: int | None = None
    parallel_workers: int | None = None
    exploit_quantile: float | None = None
    scoring_policy: str | None = None
    perturbation_factor: float | None = None
    ablation_variable: str | None = None
    ablation_value: str | None = None
    # Warm-start: when set, the runner resolves the best_config.json from
    # the manifest entry for ``(warm_start_source, warm_start_source_seed,
    # pbt)`` and passes it as ``--warm-start <path>``. The source
    # experiment must have completed its PBT phase before this experiment
    # runs.
    warm_start_source: str | None = None
    warm_start_source_seed: int | None = None
    # Strategy selector. "pbt" (default) runs the three-phase PBT→BO→EVAL
    # pipeline; "lhs" runs a single LHS-design importance sweep (no BO/eval),
    # used to generate the session JSON the SCALPEL pipeline consumes. Kept a
    # plain str so this lightweight matrix module need not import the tuners
    # enum.
    strategy: str = "pbt"
    # LHS-only: design-size override. None → profile-derived (thorough=512).
    design_size: int | None = None


def get_data_driven_tier_experiments(workload_type: str = "oltp_read_write") -> list[Experiment]:
    """Read data/data_driven_knobs/{workload_type}/data_driven_tiers.json
    and generate one ablation Experiment per non-empty tier.
    """
    path = PROJECT_ROOT / "data" / "data_driven_knobs" / workload_type / "data_driven_tiers.json"
    if not path.exists():
        return []
        
    tiers_data = json.loads(path.read_text())["tiers"]
    experiments = []
    for tier_name, knobs in tiers_data.items():
        if isinstance(knobs, list) and len(knobs) == 0:
            continue  # Skip empty tiers
        experiments.append(
            Experiment(
                id=f"t3_source_dd_{tier_name}",
                tier=3,
                description=f"Ablation: Data-driven knobs ({tier_name})",
                benchmark="sysbench",
                sysbench_workload="oltp_read_write",
                scale_factor=None,
                config_profile="thorough",
                knob_tier="extensive" if knobs is None else tier_name,
                knob_source="data_driven",
                tuning_mode="offline",
                seeds=SEEDS_K1,
                eval_repetitions=5,
                run_bo=False,
                ablation_variable="knob_source",
                ablation_value=f"data_driven_{tier_name}",
            )
        )
    return experiments


def build_all_experiments() -> list[Experiment]:
    experiments = [
        # Tier 1 — primary, K=5 for Wilcoxon-grade significance
        Experiment(
            id="t1_sysbench_rw", tier=1, description="Primary: Sysbench OLTP RW",
            benchmark="sysbench", sysbench_workload="oltp_read_write", scale_factor=None,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=SEEDS_K5, eval_repetitions=10, run_bo=True
        ),
        Experiment(
            id="t1_tpch_sf1", tier=1, description="Primary: TPC-H SF1",
            benchmark="tpch", sysbench_workload=None, scale_factor=1.0,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=SEEDS_K5, eval_repetitions=10, run_bo=True
        ),

        # Tier 2 — generalizability. OFFLINE rows extend Tier 1 across the
        # workload mix; ONLINE rows validate the no-restart claim — only
        # valid on read-only workloads, where snapshots are auto-disabled
        # (see docs/research/timing-instrumentation-plan.md §A and Phase
        # 4.3b/4.3d calibration).
        Experiment(
            id="t2_sysbench_ro", tier=2, description="Generalizability: Sysbench OLTP RO (OFFLINE)",
            benchmark="sysbench", sysbench_workload="oltp_read_only", scale_factor=None,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=SEEDS_K1, eval_repetitions=10, run_bo=True
        ),
        Experiment(
            id="t2_sysbench_wo", tier=2, description="Generalizability: Sysbench OLTP WO (OFFLINE)",
            benchmark="sysbench", sysbench_workload="oltp_write_only", scale_factor=None,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=SEEDS_K1, eval_repetitions=10, run_bo=True
        ),
        Experiment(
            id="t2_tpch_sf10", tier=2, description="Generalizability: TPC-H SF10 (OFFLINE)",
            benchmark="tpch", sysbench_workload=None, scale_factor=10.0,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=SEEDS_K1, eval_repetitions=10, run_bo=True
        ),
        Experiment(
            id="t2_online_sysbench_ro", tier=2, description="Generalizability: Sysbench OLTP RO (ONLINE no-restart)",
            benchmark="sysbench", sysbench_workload="oltp_read_only", scale_factor=None,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="online",
            seeds=SEEDS_K1, eval_repetitions=10, run_bo=True
        ),
        Experiment(
            id="t2_online_tpch_sf1", tier=2, description="Generalizability: TPC-H SF1 (ONLINE no-restart)",
            benchmark="tpch", sysbench_workload=None, scale_factor=1.0,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="online",
            seeds=SEEDS_K1, eval_repetitions=10, run_bo=True
        ),

        # Tier 3 (Ablations)
        Experiment(
            id="t3_pop_4", tier=3, description="Ablation: Population size 4",
            benchmark="sysbench", sysbench_workload="oltp_read_write", scale_factor=None,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=SEEDS_K1, eval_repetitions=5, run_bo=False,
            population=4, ablation_variable="population_size", ablation_value="4"
        ),
        Experiment(
            id="t3_pop_12", tier=3, description="Ablation: Population size 12",
            benchmark="sysbench", sysbench_workload="oltp_read_write", scale_factor=None,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=SEEDS_K1, eval_repetitions=5, run_bo=False,
            population=12, parallel_workers=6, ablation_variable="population_size", ablation_value="12"
        ),
        Experiment(
            id="t3_pop_16", tier=3, description="Ablation: Population size 16",
            benchmark="sysbench", sysbench_workload="oltp_read_write", scale_factor=None,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=SEEDS_K1, eval_repetitions=5, run_bo=False,
            population=16, parallel_workers=8, ablation_variable="population_size", ablation_value="16"
        ),
        Experiment(
            id="t3_scoring_v1", tier=3, description="Ablation: Scoring fixed_v1",
            benchmark="sysbench", sysbench_workload="oltp_read_write", scale_factor=None,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=SEEDS_K1, eval_repetitions=5, run_bo=False,
            scoring_policy="fixed_v1", ablation_variable="scoring_pipeline", ablation_value="fixed_v1"
        ),
        Experiment(
            id="t3_exploit_020", tier=3, description="Ablation: Exploit 0.20 (Pop 12, cohort=2)",
            benchmark="sysbench", sysbench_workload="oltp_read_write", scale_factor=None,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=SEEDS_K1, eval_repetitions=5, run_bo=False,
            population=12, parallel_workers=6, exploit_quantile=0.20, ablation_variable="exploit_quantile", ablation_value="0.20"
        ),
        Experiment(
            id="t3_exploit_025", tier=3, description="Ablation: Exploit 0.25 (Pop 12, cohort=3)",
            benchmark="sysbench", sysbench_workload="oltp_read_write", scale_factor=None,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=SEEDS_K1, eval_repetitions=5, run_bo=False,
            population=12, parallel_workers=6, exploit_quantile=0.25, ablation_variable="exploit_quantile", ablation_value="0.25"
        ),
        Experiment(
            id="t3_exploit_040", tier=3, description="Ablation: Exploit 0.40 (Pop 12, cohort=4)",
            benchmark="sysbench", sysbench_workload="oltp_read_write", scale_factor=None,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=SEEDS_K1, eval_repetitions=5, run_bo=False,
            population=12, parallel_workers=6, exploit_quantile=0.40, ablation_variable="exploit_quantile", ablation_value="0.40"
        ),
        Experiment(
            id="t3_tier_minimal", tier=3, description="Ablation: Knob tier minimal",
            benchmark="sysbench", sysbench_workload="oltp_read_write", scale_factor=None,
            config_profile="thorough", knob_tier="minimal", knob_source="expert", tuning_mode="offline",
            seeds=SEEDS_K1, eval_repetitions=5, run_bo=False,
            ablation_variable="knob_tier", ablation_value="minimal"
        ),
        Experiment(
            id="t3_tier_core", tier=3, description="Ablation: Knob tier core",
            benchmark="sysbench", sysbench_workload="oltp_read_write", scale_factor=None,
            config_profile="thorough", knob_tier="core", knob_source="expert", tuning_mode="offline",
            seeds=SEEDS_K1, eval_repetitions=5, run_bo=False,
            ablation_variable="knob_tier", ablation_value="core"
        ),
        Experiment(
            id="t3_tier_standard", tier=3, description="Ablation: Knob tier standard",
            benchmark="sysbench", sysbench_workload="oltp_read_write", scale_factor=None,
            config_profile="thorough", knob_tier="standard", knob_source="expert", tuning_mode="offline",
            seeds=SEEDS_K1, eval_repetitions=5, run_bo=False,
            ablation_variable="knob_tier", ablation_value="standard"
        ),
        Experiment(
            id="t3_perturb_narrow", tier=3, description="Ablation: Perturbation narrow [0.9, 1.1]",
            benchmark="sysbench", sysbench_workload="oltp_read_write", scale_factor=None,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=SEEDS_K1, eval_repetitions=5, run_bo=False,
            perturbation_factor=0.1, ablation_variable="perturbation_factor", ablation_value="0.1"
        ),
        Experiment(
            id="t3_perturb_wide", tier=3, description="Ablation: Perturbation wide [0.6, 1.4]",
            benchmark="sysbench", sysbench_workload="oltp_read_write", scale_factor=None,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=SEEDS_K1, eval_repetitions=5, run_bo=False,
            perturbation_factor=0.4, ablation_variable="perturbation_factor", ablation_value="0.4"
        ),
        # Warm-start ablation: bootstrap an ONLINE run on Sysbench RO from
        # the best config produced by the OFFLINE t1_sysbench_rw run.
        # Validates the OFFLINE-then-ONLINE deployment path (plan §A,
        # Tier 3 row). The runner resolves the upstream best_config.json
        # via the manifest entry for (warm_start_source,
        # warm_start_source_seed, pbt).
        Experiment(
            id="t3_warm_start_offline_to_online", tier=3,
            description="Ablation: OFFLINE→ONLINE warm-start (Sysbench RW source, RO target)",
            benchmark="sysbench", sysbench_workload="oltp_read_only", scale_factor=None,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="online",
            seeds=SEEDS_K1, eval_repetitions=5, run_bo=False,
            ablation_variable="warm_start", ablation_value="offline_to_online",
            warm_start_source="t1_sysbench_rw", warm_start_source_seed=42,
        ),
    ]

    # Add dynamic data-driven tier experiments
    # experiments.extend(get_data_driven_tier_experiments())

    return experiments

def get_experiments_by_tier(tier: int) -> list[Experiment]:
    return [e for e in build_all_experiments() if e.tier == tier]

def get_experiment_by_id(exp_id: str) -> Experiment | None:
    for e in build_all_experiments() + build_lhs_experiments():
        if e.id == exp_id:
            return e
    return None


def build_lhs_experiments() -> list[Experiment]:
    """Importance-design LHS sweeps — the SCALPEL inputs.

    These are *preparation* runs, not part of the Tier 1/2/3 comparison
    matrix, so they live outside :func:`build_all_experiments` and never
    appear in ``--tier`` or the default "run everything" path. They are
    reachable only by explicit id::

        python -m scripts.experiments --experiment lhs_design

    ``tier=0`` marks them as prep/analysis. The session JSON each produces
    (``lhs_results_*.json``) feeds ``scripts/run_importance_fast.sh`` /
    ``scripts/run_importance_full.sh``. ``knob_tier="extensive"`` samples the
    broadest space so SCALPEL prunes from the full knob set; ``thorough``
    supplies the 512-point design size (override via ``design_size``).
    """
    return [
        Experiment(
            id="lhs_design", tier=0,
            description="Importance-design LHS sweep (SCALPEL input)",
            benchmark="sysbench", sysbench_workload="oltp_read_write", scale_factor=None,
            config_profile="thorough", knob_tier="extensive", knob_source="expert",
            tuning_mode="offline", seeds=SEEDS_K1, eval_repetitions=0, run_bo=False,
            strategy="lhs",
        ),
    ]
