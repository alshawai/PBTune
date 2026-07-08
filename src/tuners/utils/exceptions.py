"""
Exceptions for the unified tuners package.
=========================================

Domain-specific exception hierarchy for the strategy-agnostic
:class:`~src.tuners.base.BaseTuner` lifecycle and its concrete subclasses.
All exceptions derive from :class:`TunerError` so callers can catch the full
domain with a single clause while still distinguishing specific failure modes.

This mirrors the shape of ``src/evaluation/exceptions.py``. The hierarchy is
deliberately self-contained: there is no cross-package ``src/utils`` base yet.
Once PBT/BO are refactored into ``src/tuners`` (ADR-006 exit criterion), a
shared root may be lifted into ``src/utils/exceptions.py``; until then this
package owns its taxonomy.
"""


class TunerError(Exception):
    """Base exception for all tuner failures."""


class TunerConfigError(TunerError):
    """
    Raised when a tuner is constructed with an invalid configuration.

    Covers out-of-range lifecycle settings (e.g. ``num_parallel_workers < 1``),
    an unknown :class:`~src.tuners.utils.types.TuningStrategy`, or an invalid
    strategy-specific hyperparameter such as ``design_size < 1``.
    """


class TunerSetupError(TunerError):
    """
    Raised when the shared setup phase cannot ready the tuning environment.

    Covers instance bring-up/verification failures and any other error that
    leaves the generation loop without a runnable environment.
    """


class KnobSpaceEmptyError(TunerSetupError):
    """
    Raised when no tunable knobs remain after runtime-compatibility pruning.

    The configured knob tier contained no knobs supported by the running
    PostgreSQL build, so there is nothing to tune.
    """


class GenerationEvaluationError(TunerError):
    """
    Raised when a generation cannot be evaluated at all.

    Per-configuration failures are tolerated and recorded inline; this is for
    a hard failure that invalidates the whole generation.
    """
