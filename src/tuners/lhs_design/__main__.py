"""
Direct entry point for the LHS-design tuner subpackage.

Enables the standalone door::

    python -m src.tuners.lhs_design --tier core --design-size 64

The routed door (``python -m src.tuners lhs ...``) reaches the same
:func:`src.tuners.lhs_design.cli.main` via the top-level router.
"""

from src.tuners.lhs_design.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
