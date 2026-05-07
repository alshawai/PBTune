"""
Logging Helper Functions
========================

This module contains helpers used by formatters and other logging utilities,
kept separate to  provide a clear, testable location for utility functions
like ANSI -> HTML conversion and standardized log section formatting.
"""

import re
import html as _html
import logging
from typing import Optional


LOGGER_LEVEL_WIDTH = 7
LOGGER_MODULE_WIDTH = 17

# Basic xterm 16-color palette (normal + bright)
BASIC_COLORS = {
    0: "#000000",
    1: "#800000",
    2: "#008000",
    3: "#808000",
    4: "#000080",
    5: "#800080",
    6: "#008080",
    7: "#c0c0c0",
}
BRIGHT_COLORS = {
    0: "#808080",
    1: "#ff0000",
    2: "#00ff00",
    3: "#ffff00",
    4: "#0000ff",
    5: "#ff00ff",
    6: "#00ffff",
    7: "#ffffff",
}


def ansi_to_html(text: str) -> str:
    """Convert basic ANSI sequences in `text` to HTML spans.

    - Supports 24-bit foreground colors of the form ``\x1b[38;2;R;G;Bm``.
    - Supports bold ``\x1b[1m``, italic ``\x1b[3m``, underline ``\x1b[4m``, and reset ``\x1b[0m``.
    - Supports clearing bold (22), italic (23), underline (24).

    The function escapes non-ANSI text to prevent HTML injection and
    wraps colored segments in ``<span style="...">``, emitting CSS for
    color, font-weight, font-style, and text-decoration.
    """
    if not text:
        return ""

    def _rgb_to_hex(r: int, g: int, b: int) -> str:
        return f"#{r:02x}{g:02x}{b:02x}"

    def _index256_to_hex(idx: int) -> str:
        """Convert 0-255 xterm color index to hex string.

        Follows the standard xterm 256-color palette mapping:
        - 0-15: system colors (mapped to BASIC/BRIGHT tables)
        - 16-231: 6x6x6 color cube
        - 232-255: grayscale ramp
        """
        if 0 <= idx <= 15:
            if idx <= 7:
                return BASIC_COLORS[idx]
            return BRIGHT_COLORS[idx - 8]
        if 16 <= idx <= 231:
            c = idx - 16
            r = c // 36
            g = (c % 36) // 6
            b = c % 6

            # each component is 0..5 mapped to 0..255 via 0,95,135,175,215,255
            def comp(v: int) -> int:
                return 55 + v * 40 if v > 0 else 0

            return _rgb_to_hex(comp(r), comp(g), comp(b))
        if 232 <= idx <= 255:
            # grayscale ramp 232..255 maps to 8..238
            level = 8 + (idx - 232) * 10
            return _rgb_to_hex(level, level, level)
        # fallback
        return "#000000"

    # Split the text into ANSI SGR sequences and plain text
    parts = re.split(r"(\x1b\[[0-9;]*m)", text)
    out: list[str] = []

    # Current active style (single span representing the combination)
    current_style: dict = {}
    span_open = False

    def _open_span(style: dict) -> None:
        nonlocal out, span_open
        if not style:
            return
        attrs = []
        if style.get("color"):
            attrs.append(f"color: {style['color']}")
        if style.get("background"):
            attrs.append(f"background-color: {style['background']}")
        if style.get("bold"):
            attrs.append("font-weight: bold")
        if style.get("italic"):
            attrs.append("font-style: italic")
        if style.get("underline"):
            attrs.append("text-decoration: underline")
        style_str = "; ".join(attrs)
        out.append(f'<span style="{style_str}">')
        span_open = True

    def _close_span() -> None:
        nonlocal out, span_open
        if span_open:
            out.append("</span>")
            span_open = False

    for part in parts:
        if not part:
            continue

        m = re.match(r"\x1b\[([0-9;]*)m", part)
        if m:
            params_str = m.group(1)
            params = (
                [int(p) for p in params_str.split(";") if p != ""]
                if params_str
                else [0]
            )

            # Work on a copy of current style and mutate
            new_style = dict(current_style)

            i = 0
            while i < len(params):
                p = params[i]
                # Reset
                if p == 0:
                    new_style.clear()
                elif p == 1:
                    new_style["bold"] = True
                elif p == 3:
                    new_style["italic"] = True
                elif p == 4:
                    new_style["underline"] = True
                elif p == 22:
                    new_style.pop("bold", None)
                elif p == 23:
                    new_style.pop("italic", None)
                elif p == 24:
                    new_style.pop("underline", None)
                elif 30 <= p <= 37:
                    new_style["color"] = BASIC_COLORS[p - 30]
                elif 40 <= p <= 47:
                    new_style["background"] = BASIC_COLORS[p - 40]
                elif 90 <= p <= 97:
                    new_style["color"] = BRIGHT_COLORS[p - 90]
                elif 100 <= p <= 107:
                    new_style["background"] = BRIGHT_COLORS[p - 100]
                elif p == 39:
                    new_style.pop("color", None)
                elif p == 49:
                    new_style.pop("background", None)
                elif p == 38:
                    # Extended foreground: either 5;n (256) or 2;r;g;b (truecolor)
                    if (
                        i + 1 < len(params)
                        and params[i + 1] == 2
                        and i + 4 < len(params)
                    ):
                        r, g, b = params[i + 2], params[i + 3], params[i + 4]
                        new_style["color"] = _rgb_to_hex(r, g, b)
                        i += 4
                    elif (
                        i + 1 < len(params)
                        and params[i + 1] == 5
                        and i + 2 < len(params)
                    ):
                        # 256-color index -> convert to exact hex using xterm palette
                        idx = params[i + 2]
                        new_style["color"] = _index256_to_hex(idx)
                        i += 2
                elif p == 48:
                    # Extended background
                    if (
                        i + 1 < len(params)
                        and params[i + 1] == 2
                        and i + 4 < len(params)
                    ):
                        r, g, b = params[i + 2], params[i + 3], params[i + 4]
                        new_style["background"] = _rgb_to_hex(r, g, b)
                        i += 4
                    elif (
                        i + 1 < len(params)
                        and params[i + 1] == 5
                        and i + 2 < len(params)
                    ):
                        idx = params[i + 2]
                        new_style["background"] = _index256_to_hex(idx)
                        i += 2
                # ignore unknown codes
                i += 1

            # If style changed, close previous span and open a new one if needed
            if new_style != current_style:
                _close_span()
                current_style = new_style
                _open_span(current_style)

            continue

        # Plain text
        out.append(_html.escape(part))

    # Close any open span at end
    _close_span()
    return "".join(out)


def normalize_logger_name(name: str, strip_src_prefix: bool = True) -> str:
    """Normalize a logger name for display.

    The logger is primarily used for code under `src/`, so the leading
    `src.` package prefix is removed by default.
    """
    normalized = name.strip()
    if strip_src_prefix and normalized.startswith("src."):
        return normalized.removeprefix("src.")
    return normalized


def format_logger_name(name: str, width: int = LOGGER_MODULE_WIDTH) -> str:
    """Return a left-padded logger label for aligned log output."""
    normalized = normalize_logger_name(name)
    return normalized.ljust(width)


def format_logger_level(level: str, width: int = LOGGER_LEVEL_WIDTH) -> str:
    """Return a centered logger level label for aligned log output."""
    return level.strip().upper().center(width)


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def log_section_header(
    logger: logging.Logger, title: str, width: Optional[int] = None
) -> None:
    """
    Log a formatted section header.

    Parameters
    ----------
    logger : logging.Logger
        Logger instance
    title : str
        Section title
    width : int
        Width of header line

    Example
    -------
    >>> log_section_header(logger, "GENERATION 5")
    # Output:
    # ============
    # GENERATION 5
    # ============
    """
    width = len(title) if width is None else width
    logger.info("=" * width)
    logger.info(title)
    logger.info("=" * width)


def log_generation_summary(
    logger: logging.Logger,
    generation: int,
    best_score: float,
    mean_score: float,
    std_score: float,
    exploited: int,
    restarts: int,
    elapsed: float,
    converged: bool,
) -> None:
    """
    Log a formatted generation summary.

    Parameters
    ----------
    logger : logging.Logger
        Logger instance
    generation : int
        Generation number
    best_score : float
        Best score in generation
    mean_score : float
        Mean score across workers
    std_score : float
        Standard deviation of scores
    exploited : int
        Number of workers exploited
    restarts : int
        Total restart count
    elapsed : float
        Elapsed time in seconds
    converged : bool
        Convergence status
    """
    logger.info("")
    logger.info(f"Generation {generation} Summary:")
    logger.info(f"  Best Score:  {best_score:.4f}")
    logger.info(f"  Mean Score:  {mean_score:.4f}")
    logger.info(f"  Std Dev:     {std_score:.4f}")
    logger.info(f"  Exploited:   {exploited} workers")
    logger.info(f"  Restarts:    {restarts} total")
    logger.info(f"  Elapsed:     {elapsed:.1f}s")
    logger.info(f"  Converged:   {'YES' if converged else 'NO'}")
    logger.info("")
