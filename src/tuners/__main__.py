"""
Package entry point for the unified tuners package.

Currently dispatches to the LHS-design tuner CLI, so both of these work::

    python -m src.tuners
    python -m src.tuners.lhs_design

PBT and BO retain their own entry points (``python -m src.tuner.main`` and
``python -m src.scripts.bo_baseline``); see ADR-006.
"""

from src.tuners.lhs_design_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
