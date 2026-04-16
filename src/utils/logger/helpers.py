"""
Logging Helper Functions
=========================

Reusable formatting helpers for structured log output: section headers,
generation summaries, and other recurring log patterns.
"""

import logging
from typing import Optional


def log_section_header(
        logger: logging.Logger,
        title: str,
        width: Optional[int] = None
    ) -> None:
    """
    Log a formatted section header.

    Parameters
    ----------
    logger : logging.Logger
        Logger instance
    title : str
        Section title
    width : int
        Width of header line

    Example
    -------
    >>> log_section_header(logger, "GENERATION 5")
    # Output:
    # ============
    # GENERATION 5
    # ============
    """
    width = len(title) if width is None else width
    logger.info("=" * width)
    logger.info(title)
    logger.info("=" * width)


def log_generation_summary(
    logger: logging.Logger,
    generation: int,
    best_score: float,
    mean_score: float,
    std_score: float,
    exploited: int,
    restarts: int,
    elapsed: float,
    converged: bool
) -> None:
    """
    Log a formatted generation summary.

    Parameters
    ----------
    logger : logging.Logger
        Logger instance
    generation : int
        Generation number
    best_score : float
        Best score in generation
    mean_score : float
        Mean score across workers
    std_score : float
        Standard deviation of scores
    exploited : int
        Number of workers exploited
    restarts : int
        Total restart count
    elapsed : float
        Elapsed time in seconds
    converged : bool
        Convergence status
    """
    logger.info("")
    logger.info(f"Generation {generation} Summary:")
    logger.info(f"  Best Score:  {best_score:.4f}")
    logger.info(f"  Mean Score:  {mean_score:.4f}")
    logger.info(f"  Std Dev:     {std_score:.4f}")
    logger.info(f"  Exploited:   {exploited} workers")
    logger.info(f"  Restarts:    {restarts} total")
    logger.info(f"  Elapsed:     {elapsed:.1f}s")
    logger.info(f"  Converged:   {'YES' if converged else 'NO'}")
    logger.info("")
