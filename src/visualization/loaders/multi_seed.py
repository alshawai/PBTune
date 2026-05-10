"""
Aggregator for multi-seed tuning campaigns.
"""

from dataclasses import dataclass

import numpy as np

from src.visualization.loaders.session import SessionTrace
from src.utils.logger import get_logger

LOGGER = get_logger("MultiSeedLoader")


@dataclass
class MultiSeedAggregate:
    """Aggregated statistics across multiple identical sessions (seeds)."""

    generations: np.ndarray
    mean_best: np.ndarray  # Mean of best_scores across seeds
    std_best: np.ndarray  # Std of best_scores across seeds
    mean_population_mean: np.ndarray
    n_seeds: int
    seed_values: list[int]


def aggregate_seeds(sessions: list[SessionTrace]) -> MultiSeedAggregate:
    """
    Compute mean and standard deviation across multiple independent PBT sessions.
    Assumes all sessions ran for the same number of generations.
    """
    if not sessions:
        raise ValueError("Cannot aggregate empty session list")

    n_seeds = len(sessions)
    if n_seeds == 1:
        LOGGER.warning("Aggregating a single session. Std dev will be zero.")
        s = sessions[0]
        return MultiSeedAggregate(
            generations=s.generations,
            mean_best=s.best_scores,
            std_best=np.zeros_like(s.best_scores),
            mean_population_mean=s.mean_scores,
            n_seeds=1,
            seed_values=[],
        )

    # Find minimum generation count to align arrays
    min_gens = min(len(s.generations) for s in sessions)

    # Check if we need to truncate (should usually be uniform)
    for s in sessions:
        if len(s.generations) > min_gens:
            LOGGER.info(
                "Truncating session %s from %s to %s generations for alignment.",
                s.metadata.get("file_name", "unknown"),
                len(s.generations),
                min_gens,
            )

    generations = np.arange(1, min_gens + 1)

    # Stack arrays [n_seeds, min_gens]
    all_best = np.vstack([s.best_scores[:min_gens] for s in sessions])
    all_mean = np.vstack([s.mean_scores[:min_gens] for s in sessions])

    # Compute statistics
    mean_best = np.mean(all_best, axis=0)
    std_best = np.std(all_best, axis=0, ddof=1) if n_seeds > 1 else np.zeros(min_gens)
    mean_population_mean = np.mean(all_mean, axis=0)

    # Try to extract seed values from metadata
    seed_values = []
    for s in sessions:
        ts = s.metadata.get("tuning_session", {})
        if "seed" in ts:
            seed_values.append(ts["seed"])

    return MultiSeedAggregate(
        generations=generations,
        mean_best=mean_best,
        std_best=std_best,
        mean_population_mean=mean_population_mean,
        n_seeds=n_seeds,
        seed_values=seed_values,
    )
