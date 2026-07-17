"""
LHS-design strategy package
===========================

Latin Hypercube Sampling *importance-design* sweep on top of the shared
``src.tuners`` framework. Unlike PBT/BO there is no persistent optimizer to
carry across rounds — a fixed space-filling design is drawn once and evaluated
in parallel barrier-synchronized batches — so this package holds just the
:class:`~src.tuners.lhs_design.tuner.LHSDesignTuner` and its CLI.

Two doors reach the same CLI ``main``:

* ``python -m src.tuners.lhs_design`` (direct) — via :mod:`src.tuners.lhs_design.__main__`.
* ``python -m src.tuners lhs`` (routed) — via the top-level router in
  :mod:`src.tuners.__main__`.
"""

from src.tuners.lhs_design.tuner import LHSDesignTuner
from src.tuners.lhs_design.cli import LHS_DESIGN_SIZE_BY_PROFILE

__all__ = [
    "LHSDesignTuner",
    "LHS_DESIGN_SIZE_BY_PROFILE",
]
