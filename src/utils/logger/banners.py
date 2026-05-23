"""
Startup and Status Banners
===========================

Provides ASCII-art banners and formatted status messages for both the
PBT tuning pipeline and the evaluation comparison pipeline.

Functions:

    print_startup_banner()
        PBT ASCII art banner for the tuner entry point.

    get_evaluation_banner(session_name, benchmark, reps, env_type)
        Professional box-drawing banner for evaluation runs.

    get_isolation_warning_banner()
        Colorized warning about bare-metal execution risks.
"""

import shutil

from src.utils.logger.context import get_color_context

COLORS = get_color_context()


def print_startup_banner() -> None:
    """
    Print a colorful ASCII art banner directly to stdout.
    Bypasses the logging module to avoid adding timestamps and log levels.
    """
    banner = r"""
    ____  ____  ______   ____             __  ______           _____ ____    __     ______                     
   / __ \/ __ )/_  __/  / __ \____  _____/ /_/ ____/________  / ___// __ \  / /    /_  __/_  ______  ___  _____
  / /_/ / __  | / /    / /_/ / __ \/ ___/ __/ / __/ ___/ _ \  \__ \/ / / / / /      / / / / / / __ \/ _ \/ ___/
 / ____/ /_/ / / /    / ____/ /_/ (__  ) /_/ /_/ / /  /  __/ ___/ / /_/ / / /___   / / / /_/ / / / /  __/ /    
/_/   /_____/ /_/    /_/    \____/____/\__/\____/_/   \___/ /____/\___\_\/_____/  /_/  \__,_/_/ /_/\___/_/     
"""

    term_width = shutil.get_terminal_size().columns
    term_width = max(term_width, 100)

    # Calculate banner width based on the longest line
    banner_lines = banner.strip("\n").split("\n")
    banner_width = max(len(line) for line in banner_lines) if banner_lines else 105

    # Print the banner (already aligned relative to itself, we can print it directly)
    # If we wanted to center the banner block itself, we could left-pad each line:
    padding = " " * max(0, (term_width - banner_width) // 2)
    for line in banner_lines:
        print(f"{padding}{COLORS.sky_blue}{COLORS.bold}{line}{COLORS.reset}")

    subtitle = "Population-Based Training for Automatic Database Parameter Tuning"
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
