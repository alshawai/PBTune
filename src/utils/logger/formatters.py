"""
Log Formatters for Console and HTML Output
===========================================

Provides colorized formatters that leverage the unified ``ColorPalette``
for severity-based and worker-based visual differentiation.

Classes:

    ColoredFormatter
        ANSI-colorized formatter for terminal output.

    HTMLFormatter
        HTML formatter for browser-renderable log files.

    HTMLFileHandler
        File handler that wraps log records in a styled HTML document.
"""

import datetime
import logging

from src.utils.logger.colors import ColorCode, ColorPalette
from src.utils.logger.helpers import (
    LOGGER_MODULE_WIDTH,
    LOGGER_LEVEL_WIDTH,
    ansi_to_html,
    format_logger_level,
    format_logger_name,
)


class ColoredFormatter(logging.Formatter):
    """
    Custom formatter with color support based on severity and worker ID.

    Format: [TIME] [LEVEL] [MODULE] [WORKER-ID] MESSAGE
    """

    def __init__(
        self,
        enable_colors: bool = True,
        show_module: bool = True,
        module_width: int = LOGGER_MODULE_WIDTH,
        level_width: int = LOGGER_LEVEL_WIDTH,
    ):
        """
        Initialize colored formatter.

        Parameters
        ----------
        enable_colors : bool
            Enable ANSI color codes
        show_module : bool
            Show module name in log output
        """
        self.enable_colors = enable_colors
        self.show_module = show_module
        self.module_width = module_width
        self.level_width = level_width

        if show_module:
            fmt = "%(asctime)s - %(levelname)-7s - %(name)s - %(message)s"
        else:
            fmt = "%(asctime)s - %(levelname)-7s - %(message)s"

        super().__init__(fmt=fmt, datefmt="%Y-%m-%d %H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        """Format log record with colors."""
        if not self.enable_colors:
            return super().format(record)

        message = super().format(record)
        parts = message.split(" - ", 3)
        if len(parts) < 3:
            return message  # Fallback if format doesn't match

        level_color = ColorPalette.get_level_color(record.levelname, "ansi")
        timestamp = parts[0]
        levelname = format_logger_level(record.levelname, self.level_width)

        if self.show_module and len(parts) == 4:
            module = format_logger_name(record.name, self.module_width)
            msg = parts[3]

            module_color = ColorPalette.get_module_color(record.name, "ansi")

            worker_color = ""  # Get worker color if applicable
            if hasattr(record, "worker_id") and record.worker_id is not None:  # type: ignore
                worker_color = ColorPalette.get_worker_color(
                    record.worker_id,  # type: ignore
                    "ansi",
                )

            colored_message = (
                f"{level_color}{timestamp}{ColorCode.RESET} - "
                f"{level_color}{ColorCode.BOLD}{levelname}{ColorCode.RESET} - "
                f"{module_color}{module}{ColorCode.RESET} - "
            )

            if worker_color:
                colored_message += f"{worker_color}{msg}{ColorCode.RESET}"
            else:
                colored_message += msg
        else:  # No module
            msg = parts[2] if len(parts) >= 3 else parts[-1]

            worker_color = ""
            if hasattr(record, "worker_id") and record.worker_id is not None:  # type: ignore
                worker_color = ColorPalette.get_worker_color(
                    record.worker_id,  # type: ignore
                    "ansi",
                )

            colored_message = (
                f"{level_color}{timestamp}{ColorCode.RESET} - "
                f"{level_color}{ColorCode.BOLD}{levelname}{ColorCode.RESET} - "
            )

            if worker_color:
                colored_message += f"{worker_color}{msg}{ColorCode.RESET}"
            else:
                colored_message += msg

        return colored_message


class HTMLFormatter(logging.Formatter):
    """Format log records as HTML with proper color styling."""

    def __init__(
        self,
        show_module: bool = True,
        module_width: int = LOGGER_MODULE_WIDTH,
        level_width: int = LOGGER_LEVEL_WIDTH,
    ):
        """Initialize HTMLFormatter with optional module name display."""
        self.show_module = show_module
        self.module_width = module_width
        self.level_width = level_width

        if show_module:
            fmt = "%(asctime)s - %(levelname)-7s - %(name)s - %(message)s"
        else:
            fmt = "%(asctime)s - %(levelname)-7s - %(message)s"
        super().__init__(fmt=fmt, datefmt="%Y-%m-%d %H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as HTML."""
        message = super().format(record)
        parts = message.split(" - ", 3)

        if len(parts) < 3:
            return self._escape_html(message)

        timestamp = self._escape_html(parts[0])
        levelname = format_logger_level(record.levelname, self.level_width)

        level_color = ColorPalette.get_level_color(levelname, "html")

        html = f'<span style="color: {level_color}">{timestamp}</span> - '
        html += (
            f'<span style="color: {level_color}; '
            f'font-weight: bold">{self._escape_html(levelname)}</span> - '
        )

        if self.show_module and len(parts) == 4:
            module = self._escape_html(format_logger_name(record.name, self.module_width))
            # Convert any raw ANSI in the message to HTML-safe spans
            msg = ansi_to_html(parts[3])

            module_color = ColorPalette.get_module_color(record.name, "html")

            html += (
                f'<span style="color: {module_color}; '
                f'font-weight: bold">{module}</span> - '
            )

            # Use record.worker_id instead of regex parsing
            if hasattr(record, "worker_id") and record.worker_id is not None:  # type: ignore
                worker_color = ColorPalette.get_worker_color(
                    record.worker_id,  # type: ignore
                    "html",
                )
                html += f'<span style="color: {worker_color}">{msg}</span>'
            else:
                html += msg
        else:
            msg = ansi_to_html(parts[2] if len(parts) >= 3 else parts[-1])

            if hasattr(record, "worker_id") and record.worker_id is not None:  # type: ignore
                worker_color = ColorPalette.get_worker_color(
                    record.worker_id,  # type: ignore
                    "html",
                )
                html += f'<span style="color: {worker_color}">{msg}</span>'
            else:
                html += msg

        return html

    @staticmethod
    def _escape_html(text: str) -> str:
        """Escape HTML special characters to prevent HTML injection."""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#x27;")
        )


class HTMLFileHandler(logging.FileHandler):
    """Custom file handler that wraps logs in a styled HTML document."""

    HTML_TEMPLATE = """<!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>PBT Tuning Log - {timestamp}</title>
        <style>
            :root {{
                --bg-color: #1e1e1e;
                --text-color: #d4d4d4;
                --border-color: #3498db;
                --info-color: #2ecc71;
                --warning-color: #f39c12;
                --error-color: #e74c3c;
                --debug-color: #1abc9c;
                --muted-color: #7f8c8d;
            }}

            body {{
                background-color: var(--bg-color);
                color: var(--text-color);
                font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
                font-size: 13px;
                padding: 20px;
                line-height: 1.6;
                margin: 0;
            }}

            .header {{
                border-bottom: 2px solid var(--border-color);
                padding-bottom: 20px;
                margin-bottom: 20px;
            }}

            .header h2 {{
                color: var(--border-color);
                margin: 0 0 10px 0;
            }}

            .header p {{
                color: var(--muted-color);
                margin: 5px 0;
            }}

            .logs {{
                max-width: 100%;
                overflow-x: auto;
            }}

            .log-line {{
                margin: 2px 0;
                white-space: pre-wrap;
                word-wrap: break-word;
                padding: 2px 0;
            }}

            .log-line:hover {{
                background-color: rgba(255, 255, 255, 0.05);
            }}

            .level-info {{ color: var(--info-color); font-weight: bold; }}
            .level-warning {{ color: var(--warning-color); font-weight: bold; }}
            .level-error {{ color: var(--error-color); font-weight: bold; }}
            .level-debug {{ color: var(--debug-color); font-weight: bold; }}

            .footer {{
                border-top: 2px solid var(--border-color);
                padding-top: 20px;
                margin-top: 20px;
                color: var(--muted-color);
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <h2>🔧 PBT PostgreSQL Tuning Log</h2>
            <p>Generated: {timestamp}</p>
        </div>
        <div class="logs">
    """

    HTML_FOOTER = """    </div>
        <div class="footer">
            <p>End of log - Total runtime: {runtime}</p>
        </div>
    </body>
    </html>"""

    def __init__(self, filename, mode="w", encoding="utf-8"):
        """Initialize HTMLFileHandler with HTML header."""
        super().__init__(filename, mode, encoding)
        self.start_time = datetime.datetime.now()
        self._write_html_header()

    def _write_html_header(self):
        """Write HTML header with styling."""
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        header = self.HTML_TEMPLATE.format(timestamp=timestamp)
        self.stream.write(header)
        self.stream.flush()

    def emit(self, record):
        """Emit a record as HTML."""
        try:
            # formats using the specified formatter (in this case, HTMLFormatter)
            msg = self.format(record)
            self.stream.write(f'<div class="log-line">{msg}</div>\n')
            self.stream.flush()
        except OSError:
            self.handleError(record)

    def close(self):
        """Close handler and write HTML footer."""
        runtime = datetime.datetime.now() - self.start_time
        footer = self.HTML_FOOTER.format(runtime=f"{runtime.total_seconds():.1f}s")
        try:
            self.stream.write(footer)
            self.stream.flush()
        except OSError:
            pass
        super().close()
