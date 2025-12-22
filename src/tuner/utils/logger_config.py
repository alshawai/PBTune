"""
Enhanced Logging Configuration for PBT Tuner
============================================

Provides colorized, structured logging with:
- Severity-based colors (INFO=green, DEBUG=blue, WARNING=yellow, ERROR=red)
- Worker-specific sub-colors for parallel execution tracking
- Configurable verbosity levels
- Clean formatting for production vs development

Usage:
------
from src.tuner.utils.logger_config import setup_logging, get_logger

# Setup once at application start
setup_logging(verbosity='INFO', enable_colors=True)

# Get logger for any module
logger = get_logger(__name__, worker_id=0)
logger.info("Worker-specific log message")
"""

import logging
import sys
from typing import Optional
from pathlib import Path
import datetime
import colorsys
from enum import Enum


class ModuleName(Enum):
    """Module name identifiers for color mapping."""
    MAIN = 'main'
    EVALUATOR = 'evaluator'
    APPLICATOR = 'applicator'
    POPULATION = 'population'
    WORKER = 'worker'
    RESTART = 'restart'
    INSTANCE = 'instance'
    EVOLUTION = 'evolution'


class ColorPalette:
    """
    Unified color palette for consistent colors across ANSI (terminal) and HTML.
    
    This ensures that a given semantic color (e.g., INFO, Worker-0) appears
    the same in both console logs and HTML output.
    """

    _LEVEL_COLORS_RGB = {
        'DEBUG': (26, 142, 188),     # Cyan
        'INFO': (46, 204, 113),      # Green
        'WARNING': (243, 156, 18),   # Orange
        'ERROR': (231, 76, 60),      # Red
        'CRITICAL': (155, 89, 182),  # Purple
    }

    _MODULE_COLORS_RGB = {
        ModuleName.MAIN: (52, 152, 219),       # Blue
        ModuleName.EVALUATOR: (26, 188, 156),  # Teal
        ModuleName.APPLICATOR: (155, 89, 182), # Purple
        ModuleName.POPULATION: (46, 204, 113), # Green
        ModuleName.WORKER: (241, 196, 15),     # Yellow
        ModuleName.RESTART: (230, 126, 34),    # Orange
        ModuleName.INSTANCE: (52, 231, 228),   # Bright Cyan
        ModuleName.EVOLUTION: (175, 122, 197), # Light Purple
    }

    _WORKER_COLORS_BASE_RGB = [
        (52, 152, 219),   # Blue (Worker-0)
        (46, 204, 113),   # Green (Worker-1)
        (0, 188, 212),    # Cyan (Worker-2)
        (241, 196, 15),   # Yellow (Worker-3)
        (233, 30, 99),    # Pink/Magenta (Worker-4)
        (231, 76, 60),    # Red (Worker-5)
        (236, 240, 241),  # White (Worker-6)
        (149, 165, 166),  # Gray (Worker-7)
    ]

    @staticmethod
    def _rgb_to_ansi(r: int, g: int, b: int) -> str:
        """Convert RGB to ANSI 24-bit color code."""
        return f'\033[38;2;{r};{g};{b}m'

    @staticmethod
    def _rgb_to_hex(r: int, g: int, b: int) -> str:
        """Convert RGB to hex color code."""
        return f'#{r:02x}{g:02x}{b:02x}'

    @classmethod
    def get_level_color(cls, level: str, format_type: str = 'ansi') -> str:
        """Get color for log level."""
        rgb = cls._LEVEL_COLORS_RGB.get(level, (236, 240, 241))  # Default white
        if format_type == 'ansi':
            return cls._rgb_to_ansi(*rgb)
        return cls._rgb_to_hex(*rgb)

    @classmethod
    def get_module_color(cls, module_name: str, format_type: str = 'ansi') -> str:
        """Get color for module name."""
        module_lower = module_name.lower()

        # Detect module type
        for module_type in ModuleName:
            if module_type.value in module_lower or (
                module_type == ModuleName.MAIN and '__main__' in module_lower
            ):
                rgb = cls._MODULE_COLORS_RGB[module_type]
                if format_type == 'ansi':
                    return f'\033[1m{cls._rgb_to_ansi(*rgb)}'  # Bold
                return cls._rgb_to_hex(*rgb)

        # Default color for unknown modules
        if format_type == 'ansi':
            return '\033[37m'  # White
        return '#ecf0f1'  # Light gray

    @classmethod
    def get_worker_color(cls, worker_id: int, format_type: str = 'ansi') -> str:
        """
        Get color for worker ID with dynamic generation for >8 workers.
        
        For workers 0-7: Use predefined colors (optimized contrast)
        For workers 8+:  Generate HSL-based colors dynamically
        
        Parameters
        ----------
        worker_id : int
            Worker identifier (0-indexed)
        format_type : str
            'ansi' for terminal, 'html' for HTML output
            
        Returns
        -------
        str
            ANSI color code or hex color
            
        Examples
        --------
        >>> ColorPalette.get_worker_color(0, 'ansi')   # Predefined blue ANSI
        >>> ColorPalette.get_worker_color(0, 'html')   # Predefined blue hex
        >>> ColorPalette.get_worker_color(15, 'ansi')  # Generated color ANSI
        """
        # Use predefined colors for first 8 workers
        if worker_id < len(cls._WORKER_COLORS_BASE_RGB):
            rgb = cls._WORKER_COLORS_BASE_RGB[worker_id]
        else:
            # Generate color dynamically using HSL
            hue = (worker_id * 137.5) % 360  # Golden angle for good distribution
            saturation = 0.7
            lightness = 0.6

            # Convert HSL to RGB
            r, g, b = colorsys.hls_to_rgb(hue / 360, lightness, saturation)
            rgb = (int(r * 255), int(g * 255), int(b * 255))

        if format_type == 'ansi':
            return cls._rgb_to_ansi(*rgb)
        return cls._rgb_to_hex(*rgb)


class ColorCode:
    """ANSI control codes for terminal formatting."""
    RESET = '\033[0m'
    BOLD = '\033[1m'


class ColoredFormatter(logging.Formatter):
    """
    Custom formatter with color support based on severity and worker ID.
    
    Format: [TIME] [LEVEL] [MODULE] [WORKER-ID] MESSAGE
    """

    def __init__(self, enable_colors: bool = True, show_module: bool = True):
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

        if show_module:
            fmt = '%(asctime)s - %(levelname)-8s - %(name)s - %(message)s'
        else:
            fmt = '%(asctime)s - %(levelname)-8s - %(message)s'

        super().__init__(fmt=fmt, datefmt='%Y-%m-%d %H:%M:%S')

    def format(self, record: logging.LogRecord) -> str:
        """Format log record with colors."""
        if not self.enable_colors:
            return super().format(record)

        message = super().format(record)
        parts = message.split(' - ', 3)
        if len(parts) < 3:
            return message  # Fallback if format doesn't match

        level_color = ColorPalette.get_level_color(record.levelname, 'ansi')
        timestamp = parts[0]
        levelname = parts[1].strip()

        if self.show_module and len(parts) == 4:
            module = parts[2]
            msg = parts[3]

            module_color = ColorPalette.get_module_color(record.name, 'ansi')

            worker_color = ''  # Get worker color if applicable
            if hasattr(record, 'worker_id') and record.worker_id is not None:  # type: ignore
                worker_color = ColorPalette.get_worker_color(
                    record.worker_id,  # type: ignore
                    'ansi'
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

            worker_color = ''
            if hasattr(record, 'worker_id') and record.worker_id is not None:  # type: ignore
                worker_color = ColorPalette.get_worker_color(
                    record.worker_id,  # type: ignore
                    'ansi'
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

    def __init__(self, show_module: bool = True):
        self.show_module = show_module
        if show_module:
            fmt = '%(asctime)s - %(levelname)-8s - %(name)s - %(message)s'
        else:
            fmt = '%(asctime)s - %(levelname)-8s - %(message)s'
        super().__init__(fmt=fmt, datefmt='%Y-%m-%d %H:%M:%S')

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as HTML."""
        message = super().format(record)
        parts = message.split(' - ', 3)

        if len(parts) < 3:
            return self._escape_html(message)

        timestamp = self._escape_html(parts[0])
        levelname = parts[1].strip()

        level_color = ColorPalette.get_level_color(levelname, 'html')

        html = f'<span style="color: {level_color}">{timestamp}</span> - '
        html += (
            f'<span style="color: {level_color}; '
            f'font-weight: bold">{self._escape_html(levelname)}</span> - '
        )

        if self.show_module and len(parts) == 4:
            module = self._escape_html(parts[2])
            msg = self._escape_html(parts[3])

            module_color = ColorPalette.get_module_color(record.name, 'html')

            html += (
                f'<span style="color: {module_color}; '
                f'font-weight: bold">{module}</span> - '
            )

            # Use record.worker_id instead of regex parsing
            if hasattr(record, 'worker_id') and record.worker_id is not None:  # type: ignore
                worker_color = ColorPalette.get_worker_color(
                    record.worker_id,  # type: ignore
                    'html'
                )
                html += f'<span style="color: {worker_color}">{msg}</span>'
            else:
                html += msg
        else:
            msg = self._escape_html(parts[2] if len(parts) >= 3 else parts[-1])

            if hasattr(record, 'worker_id') and record.worker_id is not None:  # type: ignore
                worker_color = ColorPalette.get_worker_color(
                    record.worker_id,  # type: ignore
                    'html'
                )
                html += f'<span style="color: {worker_color}">{msg}</span>'
            else:
                html += msg

        return html

    @staticmethod
    def _escape_html(text: str) -> str:
        """Escape HTML special characters to prevent HTML injection."""
        return (
            text.replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&#x27;')
        )


class WorkerLoggerAdapter(logging.LoggerAdapter):
    """
    Logger adapter that injects worker_id into all log records.
    
    Usage:
    ------
    logger = WorkerLoggerAdapter(base_logger, {'worker_id': 0})
    logger.info("This message will show [Worker-0]")
    """

    def process(self, msg, kwargs):
        """Add worker_id to log record."""
        worker_id = self.extra.get('worker_id')  # type: ignore
        if worker_id is not None:
            msg = f"[Worker-{worker_id}] {msg}"

        if 'extra' not in kwargs:
            kwargs['extra'] = {}
        kwargs['extra'].update(self.extra)  # type: ignore

        return msg, kwargs


def setup_logging(
    verbosity: str = 'INFO',
    enable_colors: bool = True,
    output_file: Optional[Path] = None,
    show_module: bool = True
) -> None:
    """
    Setup global logging configuration.
    
    Called once at application startup.
    
    Parameters
    ----------
    verbosity : str
        Logging level: 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'TRACE'
    enable_colors : bool
        Enable colored output (disable for file output)
    log_file : Optional[str]
        Optional file path to write logs
    show_module : bool
        Show module name in output
    
    Example
    -------
    >>> setup_logging(verbosity='DEBUG', enable_colors=True)
    >>> logger = get_logger(__name__)
    >>> logger.info("Application started")
    """
    level_map = {
        'DEBUG': logging.DEBUG,
        'INFO': logging.INFO,
        'WARNING': logging.WARNING,
        'ERROR': logging.ERROR,
        'TRACE': 5,  # Custom level for TRACE
    }

    log_level = level_map.get(verbosity.upper())

    console_formatter = ColoredFormatter(
        enable_colors=enable_colors,
        show_module=show_module
    )

    # A console handler that sends log records to stdout (terminal/console)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)  # type: ignore
    console_handler.setFormatter(console_formatter)

    # A root logger that all module loggers inherit from.
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)  # type: ignore
    # Remove existing handlers
    root_logger.handlers.clear()  # handles cases where `setup_logging` is called multiple times.
    root_logger.addHandler(console_handler)

    if output_file:
        # Writing logs to an HTML file for better rendering with colors
        html_formatter = HTMLFormatter(show_module=show_module)

        class HTMLFileHandler(logging.FileHandler):
            """Custom file handler that wraps logs in HTML structure."""
            HTML_TEMPLATE = '''<!DOCTYPE html>
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
            '''

            HTML_FOOTER = '''    </div>
                <div class="footer">
                    <p>End of log - Total runtime: {runtime}</p>
                </div>
            </body>
            </html>'''

            def __init__(self, filename, mode='w', encoding='utf-8'):
                super().__init__(filename, mode, encoding)
                self.start_time = datetime.datetime.now()
                self._write_html_header()

            def _write_html_header(self):
                """Write HTML header with styling."""
                timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
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
                footer = self.HTML_FOOTER.format(
                    runtime=f"{runtime.total_seconds():.1f}s"
                )
                try:
                    self.stream.write(footer)
                    self.stream.flush()
                except OSError:
                    pass
                super().close()

        html_log_file = output_file.with_suffix('.html')
        file_handler = HTMLFileHandler(html_log_file, mode='w', encoding='utf-8')

        file_handler.setLevel(log_level)  # type: ignore
        file_handler.setFormatter(html_formatter)
        root_logger.addHandler(file_handler)

    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('psycopg2').setLevel(logging.WARNING)


def get_logger(
        name: str = __name__,
        worker_id: Optional[int] = None
    ) -> logging.Logger:
    """
    Get a logger instance with optional worker ID.
    
    Parameters
    ----------
    name : str
        Module name, defaults to __name__
    worker_id : Optional[int]
        Worker ID for parallel execution tracking
    
    Returns
    -------
    logging.Logger or WorkerLoggerAdapter
        Configured logger instance
    
    Example
    -------
    >>> logger = get_logger(__name__, worker_id=0)
    >>> logger.info("Processing task")
    # Output: [2025-12-02 10:30:00] INFO [Worker-0] Processing task
    """
    base_logger = logging.getLogger(name)

    if worker_id is not None:
        return WorkerLoggerAdapter(base_logger, {'worker_id': worker_id})  # type: ignore

    return base_logger


def log_section_header(
        logger: logging.Logger,
        title: str,
        width: Optional[int] = None
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
    converged: bool
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
