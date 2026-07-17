"""
Top-level router for the unified tuners package.

Selects a strategy by its leading positional token and forwards the remaining
arguments to that strategy's standalone CLI (``main(argv)``)::

    python -m src.tuners pbt --tier core --config standard
    python -m src.tuners lhs --tier core --design-size 64

Each strategy also keeps its own direct door, which reaches the *same*
``main`` without going through this router::

    python -m src.tuners.pbt --tier core --config standard
    python -m src.tuners.lhs_design --tier core --design-size 64

The router deliberately does NOT use argparse subparsers: subparsers would
require every strategy to register its arguments onto this shared parser, which
would defeat each strategy owning a standalone parser callable on its own. So
we peel the first token by hand and delegate the untouched remainder — the same
``main(argv)`` serves both doors, with no shared-parser coupling. New strategies
register by adding one entry to ``STRATEGY_MAINS`` (BO lands here later).
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable, Dict, List, Optional

from src.tuners.lhs_design.cli import main as lhs_main
from src.tuners.pbt.cli import main as pbt_main

# Strategy token → standalone CLI entry point. Aliases map to the same callable
# so both the short token and the module-ish name work.
STRATEGY_MAINS: Dict[str, Callable[[Optional[List[str]]], int]] = {
    "pbt": pbt_main,
    "lhs": lhs_main,
    "lhs_design": lhs_main,
}


def _build_router() -> argparse.ArgumentParser:
    """Router parser, used only for --help / bad-strategy diagnostics."""
    router = argparse.ArgumentParser(
        prog="python -m src.tuners",
        description=(
            "Unified PostgreSQL tuner entry point. Usage: "
            "`python -m src.tuners <strategy> [strategy args...]`. Each strategy "
            "also has a direct door, e.g. `python -m src.tuners.pbt ...`."
        ),
        add_help=True,
    )
    router.add_argument(
        "strategy",
        choices=sorted(STRATEGY_MAINS),
        help="Which tuning strategy to run (remaining args go to its CLI)",
    )
    return router


def main(argv: Optional[List[str]] = None) -> int:
    """Route ``<strategy> [args...]`` to the strategy's standalone CLI.

    The leading token is peeled by hand rather than with ``parse_known_args`` so
    a strategy-level ``--help`` (e.g. ``python -m src.tuners pbt --help``)
    reaches the strategy's own parser instead of being swallowed by the router.
    The router parser is consulted only when no valid strategy token leads —
    i.e. for top-level ``--help`` or an unknown/blank strategy.
    """
    args = list(sys.argv[1:] if argv is None else argv)

    if args and args[0] in STRATEGY_MAINS:
        return STRATEGY_MAINS[args[0]](args[1:])

    # No valid leading strategy: let the router parser emit help (on -h) or a
    # choices error (on a bad/blank token), matching argparse conventions.
    _build_router().parse_args(args)
    return 2  # parse_args exits on error/help; reached only defensively.


if __name__ == "__main__":
    raise SystemExit(main())
