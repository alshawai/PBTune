import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

@dataclass(frozen=True)
class Experiment:
    id: str                             # e.g. "t1_sysbench_rw"
    tier: int                           # 1, 2, or 3
    description: str
    benchmark: str                      # "sysbench" | "tpch"
    sysbench_workload: Optional[str]    # "oltp_read_write" | ...
    scale_factor: Optional[float]       # TPC-H SF override
    config_profile: str                 # always "thorough"
    knob_tier: str                      # "extensive" | "minimal" | "core" | "standard"
    knob_source: str                    # "expert" | "data_driven"
    tuning_mode: str                    # "offline"
    seeds: List[int]
    eval_repetitions: int               # 10 or 5
    run_bo: bool                        # True for Tier 1/2, False for Tier 3
    population: Optional[int] = None
    generations: Optional[int] = None
    parallel_workers: Optional[int] = None
    exploit_quantile: Optional[float] = None
    scoring_policy: Optional[str] = None
    perturbation_factor: Optional[float] = None
    ablation_variable: Optional[str] = None
    ablation_value: Optional[str] = None


def get_data_driven_tier_experiments(workload_type: str = "oltp_read_write") -> List[Experiment]:
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
                seeds=[42],
                eval_repetitions=5,
                run_bo=False,
                ablation_variable="knob_source",
                ablation_value=f"data_driven_{tier_name}",
            )
        )
    return experiments


def build_all_experiments() -> List[Experiment]:
    seeds_k5 = [42, 123, 456, 789, 1024]
    seeds_k1 = [42]
    
    experiments = [
        # Tier 1
        Experiment(
            id="t1_sysbench_rw", tier=1, description="Primary: Sysbench OLTP RW",
            benchmark="sysbench", sysbench_workload="oltp_read_write", scale_factor=None,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=seeds_k5, eval_repetitions=10, run_bo=True
        ),
        Experiment(
            id="t1_tpch_sf1", tier=1, description="Primary: TPC-H SF1",
            benchmark="tpch", sysbench_workload=None, scale_factor=1.0,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=seeds_k5, eval_repetitions=10, run_bo=True
        ),
        
        # Tier 2
        Experiment(
            id="t2_sysbench_ro", tier=2, description="Generalizability: Sysbench OLTP RO",
            benchmark="sysbench", sysbench_workload="oltp_read_only", scale_factor=None,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=seeds_k1, eval_repetitions=10, run_bo=True
        ),
        Experiment(
            id="t2_sysbench_wo", tier=2, description="Generalizability: Sysbench OLTP WO",
            benchmark="sysbench", sysbench_workload="oltp_write_only", scale_factor=None,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=seeds_k1, eval_repetitions=10, run_bo=True
        ),
        Experiment(
            id="t2_tpch_sf10", tier=2, description="Generalizability: TPC-H SF10",
            benchmark="tpch", sysbench_workload=None, scale_factor=10.0,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=seeds_k1, eval_repetitions=10, run_bo=True
        ),
        
        # Tier 3 (Ablations)
        Experiment(
            id="t3_pop_4", tier=3, description="Ablation: Population size 4",
            benchmark="sysbench", sysbench_workload="oltp_read_write", scale_factor=None,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=seeds_k1, eval_repetitions=5, run_bo=False,
            population=4, ablation_variable="population_size", ablation_value="4"
        ),
        Experiment(
            id="t3_pop_12", tier=3, description="Ablation: Population size 12",
            benchmark="sysbench", sysbench_workload="oltp_read_write", scale_factor=None,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=seeds_k1, eval_repetitions=5, run_bo=False,
            population=12, parallel_workers=6, ablation_variable="population_size", ablation_value="12"
        ),
        Experiment(
            id="t3_pop_16", tier=3, description="Ablation: Population size 16",
            benchmark="sysbench", sysbench_workload="oltp_read_write", scale_factor=None,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=seeds_k1, eval_repetitions=5, run_bo=False,
            population=16, parallel_workers=8, ablation_variable="population_size", ablation_value="16"
        ),
        Experiment(
            id="t3_scoring_v1", tier=3, description="Ablation: Scoring fixed_v1",
            benchmark="sysbench", sysbench_workload="oltp_read_write", scale_factor=None,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=seeds_k1, eval_repetitions=5, run_bo=False,
            scoring_policy="fixed_v1", ablation_variable="scoring_pipeline", ablation_value="fixed_v1"
        ),
        Experiment(
            id="t3_exploit_020", tier=3, description="Ablation: Exploit 0.20 (Pop 12)",
            benchmark="sysbench", sysbench_workload="oltp_read_write", scale_factor=None,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=seeds_k1, eval_repetitions=5, run_bo=False,
            population=12, parallel_workers=6, exploit_quantile=0.20, ablation_variable="exploit_quantile", ablation_value="0.20"
        ),
        Experiment(
            id="t3_exploit_025", tier=3, description="Ablation: Exploit 0.25 (Pop 12)",
            benchmark="sysbench", sysbench_workload="oltp_read_write", scale_factor=None,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=seeds_k1, eval_repetitions=5, run_bo=False,
            population=12, parallel_workers=6, exploit_quantile=0.25, ablation_variable="exploit_quantile", ablation_value="0.25"
        ),
        Experiment(
            id="t3_exploit_030", tier=3, description="Ablation: Exploit 0.30 (Pop 12)",
            benchmark="sysbench", sysbench_workload="oltp_read_write", scale_factor=None,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=seeds_k1, eval_repetitions=5, run_bo=False,
            population=12, parallel_workers=6, exploit_quantile=0.30, ablation_variable="exploit_quantile", ablation_value="0.30"
        ),
        Experiment(
            id="t3_tier_minimal", tier=3, description="Ablation: Knob tier minimal",
            benchmark="sysbench", sysbench_workload="oltp_read_write", scale_factor=None,
            config_profile="thorough", knob_tier="minimal", knob_source="expert", tuning_mode="offline",
            seeds=seeds_k1, eval_repetitions=5, run_bo=False,
            ablation_variable="knob_tier", ablation_value="minimal"
        ),
        Experiment(
            id="t3_tier_core", tier=3, description="Ablation: Knob tier core",
            benchmark="sysbench", sysbench_workload="oltp_read_write", scale_factor=None,
            config_profile="thorough", knob_tier="core", knob_source="expert", tuning_mode="offline",
            seeds=seeds_k1, eval_repetitions=5, run_bo=False,
            ablation_variable="knob_tier", ablation_value="core"
        ),
        Experiment(
            id="t3_tier_standard", tier=3, description="Ablation: Knob tier standard",
            benchmark="sysbench", sysbench_workload="oltp_read_write", scale_factor=None,
            config_profile="thorough", knob_tier="standard", knob_source="expert", tuning_mode="offline",
            seeds=seeds_k1, eval_repetitions=5, run_bo=False,
            ablation_variable="knob_tier", ablation_value="standard"
        ),
        Experiment(
            id="t3_perturb_narrow", tier=3, description="Ablation: Perturbation narrow [0.9, 1.1]",
            benchmark="sysbench", sysbench_workload="oltp_read_write", scale_factor=None,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=seeds_k1, eval_repetitions=5, run_bo=False,
            perturbation_factor=0.1, ablation_variable="perturbation_factor", ablation_value="0.1"
        ),
        Experiment(
            id="t3_perturb_wide", tier=3, description="Ablation: Perturbation wide [0.6, 1.4]",
            benchmark="sysbench", sysbench_workload="oltp_read_write", scale_factor=None,
            config_profile="thorough", knob_tier="extensive", knob_source="expert", tuning_mode="offline",
            seeds=seeds_k1, eval_repetitions=5, run_bo=False,
            perturbation_factor=0.4, ablation_variable="perturbation_factor", ablation_value="0.4"
        ),
    ]
    
    # Add dynamic data-driven tier experiments
    experiments.extend(get_data_driven_tier_experiments())
    
    return experiments

def get_experiments_by_tier(tier: int) -> List[Experiment]:
    return [e for e in build_all_experiments() if e.tier == tier]

def get_experiment_by_id(exp_id: str) -> Optional[Experiment]:
    for e in build_all_experiments():
        if e.id == exp_id:
            return e
    return None
