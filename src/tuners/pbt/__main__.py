"""
Direct entry point for the PBT tuner subpackage.

Enables the standalone door::

    python -m src.tuners.pbt --tier core --config standard

The routed door (``python -m src.tuners pbt ...``) reaches the same
:func:`src.tuners.pbt.cli.main` via the top-level router.
"""

from src.tuners.pbt.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
