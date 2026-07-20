"""
Startup and Status Banners
===========================

Provides ASCII-art banners and formatted status messages for the tuning
pipelines (PBT, LHS-design, BO) and the evaluation comparison pipeline.

Functions:

    print_startup_banner(strategy=TuningStrategy.PBT)
        Per-strategy ASCII art banner for a tuner entry point. The zero-arg
        call preserves the original PBT banner verbatim.

    get_evaluation_banner(session_name, benchmark, reps, env_type)
        Professional box-drawing banner for evaluation runs.

    get_isolation_warning_banner()
        Colorized warning about bare-metal execution risks.
"""

import shutil
from typing import TYPE_CHECKING, Dict, Tuple, Union

from src.utils.logger.context import get_color_context

if TYPE_CHECKING:  # avoid a runtime import cycle (types -> logger -> types)
    from src.tuners.utils.types import TuningStrategy

COLORS = get_color_context()

# Per-strategy startup art + subtitle. Keyed by the TuningStrategy *value*
# ("pbt"/"lhs"/"bo") so the banner module never imports the tuners package at
# runtime. Each art spells the strategy's own product name (PBT "Database
# Tuner", BO "Baseline Tuner").
_PBT_ART = r"""
    ____  ____ ______   ____        __        __                       ______
   / __ \/ __ )_  __/  / __ \____ _/ /_____ _/ /_  ____ _________     /_  __/_  ______  ___  _____
  / /_/ / __  |/ /    / / / / __ `/ __/ __ `/ __ \/ __ `/ ___/ _ \     / / / / / / __ \/ _ \/ ___/
 / ____/ /_/ // /    / /_/ / /_/ / /_/ /_/ / /_/ / /_/ (__  )  __/    / / / /_/ / / / /  __/ /
/_/   /_____//_/    /_____/\__,_/\__/\__,_/_.___/\__,_/____/\___/    /_/  \__,_/_/ /_/\___/_/
"""

_LHS_ART = r"""
    __    __  _____     ____            _                ______
   / /   / / / / __/   / __ \___  _____(_)___ _____     /_  __/_ ______  ___  _____
  / /   / /_/ /\ \    / / / / _ \/ ___/ / __ `/ __ \     / / / / / / __ \/ _ \/ ___/
 / /___/ __  /___/ / / /_/ /  __(__  ) / /_/ / / / /    / / / /_/ / / / /  __/ /
/_____/_/ /_//____/ /_____/\___/____/_/\__, /_/ /_/    /_/  \__,_/_/ /_/\___/_/
                                      /____/
"""

_BO_ART = r"""
    ____  ____     ____                  ___               ______
   / __ )/ __ \   / __ )____ _________  / (_)___  ___     /_  __/_  ______  ___  _____
  / __  / / / /  / __  / __ `/ ___/ _ \/ / / __ \/ _ \     / / / / / / __ \/ _ \/ ___/
 / /_/ / /_/ /  / /_/ / /_/ (__  )  __/ / / / / /  __/    / / / /_/ / / / /  __/ /
/_____/\____/  /_____/\__,_/____/\___/_/_/_/ /_/\___/    /_/  \__,_/_/ /_/\___/_/
"""

# value -> (art, subtitle)
_STRATEGY_BANNERS: Dict[str, Tuple[str, str]] = {
    "pbt": (
        _PBT_ART,
        "Population-Based Training for Automatic Database Parameter Tuning",
    ),
    "lhs": (
        _LHS_ART,
        "Latin-Hypercube Importance-Design Sweep for SCALPEL Knob Analysis",
    ),
    "bo": (
        _BO_ART,
        "BO Database Tuner - Bayesian Optimization Baseline",
    ),
}


def print_startup_banner(
    strategy: Union["TuningStrategy", str] = "pbt",
) -> None:
    """
    Print a colorful per-strategy ASCII art banner directly to stdout.

    Bypasses the logging module to avoid adding timestamps and log levels.

    Parameters
    ----------
    strategy : TuningStrategy | str, optional
        Which strategy banner to render. Accepts a ``TuningStrategy`` enum
        member or its string value ("pbt", "lhs", "bo"). Defaults to the PBT
        banner so the original zero-arg call site is unchanged.
    """
    key = getattr(strategy, "value", strategy)
    if not isinstance(key, str):
        key = str(key)
    art, subtitle = _STRATEGY_BANNERS.get(key.lower(), _STRATEGY_BANNERS["pbt"])

    term_width = shutil.get_terminal_size().columns
    term_width = max(term_width, 100)

    # Calculate banner width based on the longest line
    banner_lines = art.strip("\n").split("\n")
    banner_width = max(len(line) for line in banner_lines) if banner_lines else 105

    # Print the banner (already aligned relative to itself, we can print it directly)
    # If we wanted to center the banner block itself, we could left-pad each line:
    padding = " " * max(0, (term_width - banner_width) // 2)
    for line in banner_lines:
        print(f"{padding}{COLORS.sky_blue}{COLORS.bold}{line}{COLORS.reset}")

    # Center the subtitle text relative to the terminal
    print(
        f"\n{COLORS.bold}{COLORS.italic}{COLORS.sky_blue}{subtitle.center(term_width)}{COLORS.reset}"
    )
    print("\n" + f"{COLORS.bold}{COLORS.purple}={COLORS.reset}" * term_width + "\n")


def get_evaluation_banner(
    session_name: str,
    benchmark: str,
    repetitions: int,
    env_type: str,
) -> str:
    """
    Return a professional box-drawing banner for evaluation comparison runs.

    Parameters
    ----------
    session_name : str
        Name of the tuning session file.
    benchmark : str
        Benchmark identifier (e.g. "sysbench", "tpch").
    repetitions : int
        Number of repetitions per configuration.
    env_type : str
        Environment type ("Docker" or "bare-metal").

    Returns
    -------
    str
        Multiline ANSI-colored banner string.
    """
    bench_display = "TPC-H" if benchmark == "tpch" else "Sysbench"

    content_lines = [
        f"  Session   : {session_name}",
        f"  Benchmark : {bench_display}",
        f"  Reps      : {repetitions} × 2 configurations",
        f"  Env       : {env_type}",
    ]
    inner_width = max(len(line) for line in content_lines) + 4

    lines = [
        f"{COLORS.info}{COLORS.bold}{'═' * inner_width}{COLORS.reset}",
        f"{COLORS.info}{COLORS.bold}  COMPARATIVE EVALUATION{COLORS.reset}",
        f"{COLORS.info}{'─' * inner_width}{COLORS.reset}",
    ]
    for cl in content_lines:
        lines.append(f"{COLORS.debug}{cl}{COLORS.reset}")
    lines.append(f"{COLORS.info}{COLORS.bold}{'═' * inner_width}{COLORS.reset}")

    return "\n".join(lines)


def get_isolation_warning_banner() -> str:
    """
    Return a colorized warning banner about bare-metal execution risks.

    This banner is shown when the system falls back to bare-metal mode
    (either because Docker is unavailable, or because ``--no-docker`` was
    specified). It warns that results lack cgroup-level resource isolation
    and may be noisy.

    Returns
    -------
    str
        Multiline ANSI-colored warning string.
    """
    width = 72
    bar = f"{COLORS.warning}{COLORS.bold}{'━' * width}{COLORS.reset}"

    lines = [
        "",
        bar,
        f"{COLORS.warning}{COLORS.bold}  ⚠  BARE-METAL MODE — REDUCED ISOLATION{COLORS.reset}",
        bar,
        "",
        f"{COLORS.debug}  Running WITHOUT Docker means:{COLORS.reset}",
        "",
        f"{COLORS.error}    •{COLORS.reset} {COLORS.debug}No cgroup resource limits (CPU/RAM uncontrolled){COLORS.reset}",
        f"{COLORS.error}    •{COLORS.reset} {COLORS.debug}No filesystem isolation (shared host state){COLORS.reset}",
        f"{COLORS.error}    •{COLORS.reset} {COLORS.debug}Background processes may skew benchmark results{COLORS.reset}",
        f"{COLORS.error}    •{COLORS.reset} {COLORS.debug}Results are NOT directly comparable to Docker runs{COLORS.reset}",
        "",
        f"{COLORS.warning}  For reproducible, publication-quality results, use Docker.{COLORS.reset}",
        f"{COLORS.warning}  Re-run without --no-docker to enable Docker automatically.{COLORS.reset}",
        "",
        bar,
        "",
    ]
    return "\n".join(lines)
