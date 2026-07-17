"""
RestartCostModel — archived from src/utils/restart_manager.py

This class modelled the amortised cost of PostgreSQL restarts
during tuning. It has been superseded by the explicit RestartPolicy
module (src/tuners/engine/restart_policy.py) which uses pure
TuningMode-based decisions instead of penalty-factor scoring.
"""

import logging
from typing import Optional

class RestartCostModel:
    """
    Models restart cost based on database tuning research
    
    Cost model from literature:
    - Base restart time: ~7 seconds (PostgreSQL startup median)
    - Cache warmup: ~10% of measurement duration
    - Batching: Amortize cost over N generations
    
    References:
    - CDBTune: Batch restarts every 10 iterations (3% amortized cost)
    - OtterTune: Accept ~25% cost per restart for 100x+ gains
    - QTune: Learned restart penalty converges to 5-15% of throughput
    """

    def __init__(
        self,
        base_restart_time: float = 7.0,
        cache_warmup_ratio: float = 0.1,
        restart_interval: int = 10
    ):
        """
        Initialize restart cost model
        
        Args:
            base_restart_time: Base PostgreSQL restart time (seconds)
                Literature value: 7s median (5-10s range)
            cache_warmup_ratio: Cache warmup as fraction of measurement time
                Literature value: 0.1 (10% of measurement duration)
            restart_interval: Batch restarts every N generations
                Literature value: 10 generations (CDBTune)
        """
        self.base_restart_time = base_restart_time
        self.cache_warmup_ratio = cache_warmup_ratio
        self.restart_interval = restart_interval

        base_logger.debug(
            "✓ Initialized RestartCostModel: base=%.1fs, warmup_ratio=%.1f, interval=%d",
            base_restart_time, cache_warmup_ratio, restart_interval
        )

    def calculate_raw_cost(self, measurement_duration: float) -> float:
        """
        Calculate raw restart cost (seconds)
        
        Args:
            measurement_duration: Duration of performance measurement (seconds)
        
        Returns:
            Total restart cost in seconds
        """
        cache_warmup = measurement_duration * self.cache_warmup_ratio
        return self.base_restart_time + cache_warmup

    def calculate_amortized_cost(
        self,
        measurement_duration: float,
        generation: int
    ) -> float:
        """
        Calculate amortized restart cost per generation
        
        With batching, cost is distributed across multiple generations
        
        Args:
            measurement_duration: Duration of measurement (seconds)
            generation: Current generation number
        
        Returns:
            Amortized cost per generation (seconds)
        """
        raw_cost = self.calculate_raw_cost(measurement_duration)

        if generation % self.restart_interval == 0:
            return raw_cost / self.restart_interval

        return 0.0

    def calculate_penalty_factor(
        self,
        measurement_duration: float,
        restart_occurred: bool,
        generation: Optional[int] = None
    ) -> float:
        """
        Calculate score penalty factor
        
        Args:
            measurement_duration: Duration of measurement (seconds)
            restart_occurred: Whether restart happened this generation
            generation: Generation number (for amortization calculation)
        
        Returns:
            Penalty factor to multiply score by (0.0-1.0)
            - 1.0 = no penalty
            - 0.75 = 25% penalty
            - 0.97 = 3% penalty (typical with batching)
        """
        if not restart_occurred:
            return 1.0

        if generation is not None:
            effective_cost = self.calculate_amortized_cost(measurement_duration, generation)
        else:
            effective_cost = self.calculate_raw_cost(measurement_duration)

        total_time = measurement_duration + effective_cost
        penalty_factor = measurement_duration / total_time

        return penalty_factor

    def apply_penalty(
        self,
        score: float,
        measurement_duration: float,
        restart_occurred: bool,
        generation: Optional[int] = None,
        logger: Optional[logging.Logger] = None
    ) -> float:
        """
        Apply restart penalty to score
        
        Args:
            score: Raw performance score
            measurement_duration: Duration of measurement (seconds)
            restart_occurred: Whether restart happened
            generation: Generation number (for batching)
            logger: Optional logger for worker-contextualized logging
        
        Returns:
            Adjusted score with restart penalty applied
        """
        penalty_factor = self.calculate_penalty_factor(
            measurement_duration, restart_occurred, generation
        )

        adjusted_score = score * penalty_factor

        if restart_occurred:
            penalty_pct = (1 - penalty_factor) * 100
            # Use provided logger (with worker context) or fallback to base_logger
            log = logger or base_logger
            log.debug(
                "Applied restart penalty: %.1f%% (score: %.4f -> %.4f)",
                penalty_pct, score, adjusted_score
            )

        return adjusted_score

