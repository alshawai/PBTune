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

from src.utils.logger.colors import ColorCode, ColorPalette


def print_startup_banner(enable_colors: bool = True) -> None:
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

    # Use the INFO color (Green) for the banner and standard bold text for the subtitle.
    # Allow disabling ANSI colors for plain-text terminal output.
    color = ColorPalette.get_level_color("INFO", "ansi") if enable_colors else ""
    reset = ColorCode.RESET if enable_colors else ""
    bold = ColorCode.BOLD if enable_colors else ""

    # Get terminal width
    term_width = shutil.get_terminal_size().columns

    # Ensure a reasonable minimum width (e.g., 100) if terminal is very narrow
    term_width = max(term_width, 100)

    # Calculate banner width based on the longest line
    banner_lines = banner.strip("\n").split("\n")
    banner_width = max(len(line) for line in banner_lines) if banner_lines else 105

    # Print the banner (already aligned relative to itself, we can print it directly)
    # If we wanted to center the banner block itself, we could left-pad each line:
    padding = " " * max(0, (term_width - banner_width) // 2)
    for line in banner_lines:
        print(f"{padding}{color}{bold}{line}{reset}")

    subtitle = "Population-Based Training for Automatic Database Parameter Tuning"
    # Center the subtitle text relative to the terminal
    print(f"\n{bold}{subtitle.center(term_width)}{reset}")
    print("\n" + "=" * term_width + "\n")


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
    color = ColorPalette.get_level_color("INFO", "ansi")
    bold = ColorCode.BOLD
    reset = ColorCode.RESET
    dim = ColorPalette.get_level_color("DEBUG", "ansi")

    content_lines = [
        f"  Session   : {session_name}",
        f"  Benchmark : {bench_display}",
        f"  Reps      : {repetitions} × 2 configurations",
        f"  Env       : {env_type}",
    ]
    inner_width = max(len(line) for line in content_lines) + 4

    lines = [
        f"{color}{bold}{'═' * inner_width}{reset}",
        f"{color}{bold}  COMPARATIVE EVALUATION{reset}",
        f"{color}{'─' * inner_width}{reset}",
    ]
    for cl in content_lines:
        lines.append(f"{dim}{cl}{reset}")
    lines.append(f"{color}{bold}{'═' * inner_width}{reset}")

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
    warn_color = ColorPalette.get_level_color("WARNING", "ansi")
    err_color = ColorPalette.get_level_color("ERROR", "ansi")
    bold = ColorCode.BOLD
    reset = ColorCode.RESET
    dim = ColorPalette.get_level_color("DEBUG", "ansi")

    width = 72
    bar = f"{warn_color}{bold}{'━' * width}{reset}"

    lines = [
        "",
        bar,
        f"{warn_color}{bold}  ⚠  BARE-METAL MODE — REDUCED ISOLATION{reset}",
        bar,
        "",
        f"{dim}  Running WITHOUT Docker means:{reset}",
        "",
        f"{err_color}    •{reset} {dim}No cgroup resource limits (CPU/RAM uncontrolled){reset}",
        f"{err_color}    •{reset} {dim}No filesystem isolation (shared host state){reset}",
        f"{err_color}    •{reset} {dim}Background processes may skew benchmark results{reset}",
        f"{err_color}    •{reset} {dim}Results are NOT directly comparable to Docker runs{reset}",
        "",
        f"{warn_color}  For reproducible, publication-quality results, use Docker.{reset}",
        f"{warn_color}  Re-run without --no-docker to enable Docker automatically.{reset}",
        "",
        bar,
        "",
    ]
    return "\n".join(lines)
