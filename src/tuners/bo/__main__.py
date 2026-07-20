"""Direct-door entry point for the BO tuner: ``python -m src.tuners.bo``.

Reaches the same :func:`~src.tuners.bo.cli.main` as the routed door
``python -m src.tuners bo`` (via :mod:`src.tuners.__main__`).
"""

from src.tuners.bo.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
