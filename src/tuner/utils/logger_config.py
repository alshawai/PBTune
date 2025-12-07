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
from enum import Enum
import re


class VerbosityLevel(Enum):
    """Verbosity levels for logging control."""
    QUIET = logging.WARNING      # Only warnings and errors
    NORMAL = logging.INFO         # Standard operational messages
    VERBOSE = logging.DEBUG       # Detailed debug information
    TRACE = 5                     # Ultra-detailed trace (custom level)


class ColorCode:
    """ANSI color codes for terminal output."""
    RESET = '\033[0m'
    BOLD = '\033[1m'

    DEBUG = '\033[36m'      # Cyan
    INFO = '\033[32m'       # Green
    WARNING = '\033[33m'    # Yellow
    ERROR = '\033[31m'      # Red
    CRITICAL = '\033[35m'   # Magenta

    WORKER_COLORS = [
        '\033[94m',   # Bright Blue (Worker-0)
        '\033[92m',   # Bright Green (Worker-1)
        '\033[96m',   # Bright Cyan (Worker-2)
        '\033[93m',   # Bright Yellow (Worker-3)
        '\033[95m',   # Bright Magenta (Worker-4)
        '\033[91m',   # Bright Red (Worker-5)
        '\033[97m',   # Bright White (Worker-6)
        '\033[90m',   # Bright Gray (Worker-7)
    ]

    MODULE_MAIN = '\033[1;34m'        # Bold Blue (main)
    MODULE_EVALUATOR = '\033[1;36m'   # Bold Cyan (evaluator)
    MODULE_APPLICATOR = '\033[1;35m'  # Bold Magenta (applicator)
    MODULE_POPULATION = '\033[1;32m'  # Bold Green (population)
    MODULE_WORKER = '\033[1;33m'      # Bold Yellow (worker)
    MODULE_RESTART = '\033[38;5;214m' # Orange/Amber (restart_manager)
    MODULE_INSTANCE = '\033[38;5;51m' # Bright Teal/Turquoise (instance_manager)
    MODULE_EVOLUTION = '\033[38;5;141m' # Purple (evolution)


class ColoredFormatter(logging.Formatter):
    """
    Custom formatter with color support based on severity and worker ID.
    
    Format: [TIME] [LEVEL] [MODULE] [WORKER-ID] MESSAGE
    """

    LEVEL_COLORS = {
        'DEBUG': ColorCode.DEBUG,
        'INFO': ColorCode.INFO,
        'WARNING': ColorCode.WARNING,
        'ERROR': ColorCode.ERROR,
        'CRITICAL': ColorCode.CRITICAL,
    }

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
        level_color = self.LEVEL_COLORS.get(record.levelname, '')
        module_color = ''
        if self.show_module:
            module_name = record.name.lower()
            if 'main' in module_name or '__main__' in module_name:
                module_color = ColorCode.MODULE_MAIN
            elif 'evaluator' in module_name:
                module_color = ColorCode.MODULE_EVALUATOR
            elif 'applicator' in module_name:
                module_color = ColorCode.MODULE_APPLICATOR
            elif 'population' in module_name:
                module_color = ColorCode.MODULE_POPULATION
            elif 'worker' in module_name:
                module_color = ColorCode.MODULE_WORKER
            elif 'restart' in module_name:
                module_color = ColorCode.MODULE_RESTART
            elif 'instance' in module_name:
                module_color = ColorCode.MODULE_INSTANCE
            elif 'evolution' in module_name:
                module_color = ColorCode.MODULE_EVOLUTION
            else:
                module_color = '\033[37m'  # White for other modules

        worker_color = ''
        if hasattr(record, 'worker_id') and record.worker_id is not None:  # type: ignore
            worker_idx = record.worker_id % len(ColorCode.WORKER_COLORS)  # type: ignore
            worker_color = ColorCode.WORKER_COLORS[worker_idx]

        parts = message.split(' - ', 3)
        if len(parts) < 3:
            return message  # Fallback if format doesn't match

        timestamp = parts[0]
        levelname = parts[1].strip()

        if self.show_module and len(parts) == 4:
            module = parts[2]
            msg = parts[3]

            colored_message = (
                f"{level_color}{timestamp}{ColorCode.RESET} - "
                f"{level_color}{ColorCode.BOLD}{levelname}{ColorCode.RESET} - "
                f"{module_color}{module}{ColorCode.RESET} - "
            )

            if worker_color:
                colored_message += f"{worker_color}{msg}{ColorCode.RESET}"
            else:
                colored_message += msg
        else:
            msg = parts[2] if len(parts) >= 3 else parts[-1]
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

    ANSI_TO_HTML = {
        '30': '#000000', '31': '#e74c3c', '32': '#2ecc71', '33': '#f39c12',
        '34': '#3498db', '35': '#9b59b6', '36': '#1abc9c', '37': '#ecf0f1',
        '90': '#7f8c8d', '91': '#e74c3c', '92': '#2ecc71', '93': '#f1c40f',
        '94': '#3498db', '95': '#e91e63', '96': '#00bcd4', '97': '#ffffff',
        # 256 color codes
        '38;5;214': '#ff8700', '38;5;51': '#00ffff', '38;5;141': '#af87ff',
    }

    LEVEL_COLORS = {
        'DEBUG': '#1abc9c',
        'INFO': '#2ecc71', 
        'WARNING': '#f39c12',
        'ERROR': '#e74c3c',
        'CRITICAL': '#9b59b6',
    }

    def __init__(self, show_module: bool = True):
        self.show_module = show_module
        if show_module:
            fmt = '%(asctime)s - %(levelname)-8s - %(name)s - %(message)s'
        else:
            fmt = '%(asctime)s - %(levelname)-8s - %(message)s'
        super().__init__(fmt=fmt, datefmt='%Y-%m-%d %H:%M:%S')
        self.log_lines = []

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as HTML."""
        message = super().format(record)
        parts = message.split(' - ', 3)

        if len(parts) < 3:
            return self._escape_html(message)

        timestamp = self._escape_html(parts[0])
        levelname = parts[1].strip()
        level_color = self.LEVEL_COLORS.get(levelname, '#ecf0f1')

        html = f'<span style="color: #7f8c8d">{timestamp}</span> - '
        html += f'<span style="color: {level_color}; font-weight: bold">{self._escape_html(levelname)}</span> - '

        if self.show_module and len(parts) == 4:
            module = self._escape_html(parts[2])
            msg = self._escape_html(parts[3])

            module_color = '#3498db'  # Default blue
            if 'evaluator' in parts[2].lower():
                module_color = '#1abc9c'
            elif 'population' in parts[2].lower():
                module_color = '#2ecc71'
            elif 'instance' in parts[2].lower():
                module_color = '#00bcd4'

            html += f'<span style="color: {module_color}">{module}</span> - '

            if '[Worker-' in msg:
                worker_match = re.match(r'\[Worker-(\d+)\]', msg)
                if worker_match:
                    worker_id = int(worker_match.group(1))
                    worker_colors = ['#3498db', '#2ecc71', '#00bcd4', '#f1c40f', 
                                   '#e91e63', '#e74c3c', '#ffffff', '#7f8c8d']
                    worker_color = worker_colors[worker_id % len(worker_colors)]
                    html += f'<span style="color: {worker_color}">{msg}</span>'
                else:
                    html += msg
            else:
                html += msg
        else:
            msg = self._escape_html(parts[2] if len(parts) >= 3 else parts[-1])
            html += msg

        return html

    @staticmethod
    def _escape_html(text: str) -> str:
        """Escape HTML special characters."""
        return (text.replace('&', '&amp;')
                   .replace('<', '&lt;')
                   .replace('>', '&gt;')
                   .replace('"', '&quot;')
                   .replace("'", '&#x27;'))


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
    log_file: Optional[str] = None,
    show_module: bool = True
) -> None:
    """
    Setup global logging configuration.
    
    Call this once at application startup.
    
    Parameters
    ----------
    verbosity : str
        Logging level: 'QUIET', 'NORMAL', 'VERBOSE', 'TRACE'
    enable_colors : bool
        Enable colored output (disable for file output)
    log_file : Optional[str]
        Optional file path to write logs
    show_module : bool
        Show module name in output
    
    Example
    -------
    >>> setup_logging(verbosity='VERBOSE', enable_colors=True)
    >>> logger = get_logger(__name__)
    >>> logger.info("Application started")
    """
    level_map = {
        'QUIET': VerbosityLevel.QUIET.value,
        'NORMAL': VerbosityLevel.NORMAL.value,
        'VERBOSE': VerbosityLevel.VERBOSE.value,
        'TRACE': VerbosityLevel.TRACE.value,
        'DEBUG': logging.DEBUG,
        'INFO': logging.INFO,
        'WARNING': logging.WARNING,
        'ERROR': logging.ERROR,
    }

    log_level = level_map.get(verbosity.upper(), logging.INFO)

    console_formatter = ColoredFormatter(
        enable_colors=enable_colors,
        show_module=show_module
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(console_formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()  # Remove existing handlers
    root_logger.addHandler(console_handler)

    if log_file:
        html_formatter = HTMLFormatter(show_module=show_module)

        class HTMLFileHandler(logging.FileHandler):
            """Custom file handler that wraps logs in HTML structure."""
            def __init__(self, filename, mode='w', encoding='utf-8'):
                super().__init__(filename, mode, encoding)
                self.log_lines = []
                self._write_html_header()
            
            def _write_html_header(self):
                """Write HTML header with styling."""
                import datetime
                header = '''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>PBT Tuning Log</title>
    <style>
        body {
            background-color: #1e1e1e;
            color: #d4d4d4;
            font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
            font-size: 13px;
            padding: 20px;
            line-height: 1.6;
        }
        .log-line {
            margin: 2px 0;
            white-space: pre-wrap;
            word-wrap: break-word;
        }
        .timestamp { color: #7f8c8d; }
        .level-info { color: #2ecc71; font-weight: bold; }
        .level-warning { color: #f39c12; font-weight: bold; }
        .level-error { color: #e74c3c; font-weight: bold; }
        .level-debug { color: #1abc9c; font-weight: bold; }
        .module { color: #3498db; }
        .worker { font-weight: bold; }
    </style>
</head>
<body>
<h2 style="color: #3498db;">🔧 PBT PostgreSQL Tuning Log</h2>
<p style="color: #7f8c8d;">Generated: ''' + datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S') + '''</p>
<hr style="border-color: #3498db;">
<div class="logs">
'''
                self.stream.write(header)
                self.stream.flush()
            
            def emit(self, record):
                """Emit a record as HTML."""
                try:
                    msg = self.format(record)
                    self.stream.write(f'<div class="log-line">{msg}</div>\n')
                    self.stream.flush()
                except Exception:
                    self.handleError(record)
            
            def close(self):
                """Close handler and write HTML footer."""
                footer = '''</div>
<hr style="border-color: #3498db;">
<p style="color: #7f8c8d;">End of log</p>
</body>
</html>'''
                try:
                    self.stream.write(footer)
                    self.stream.flush()
                except:
                    pass
                super().close()
        
        # Use .html extension for proper rendering
        html_log_file = log_file.replace('.log', '.html') if log_file.endswith('.log') else log_file + '.html'
        file_handler = HTMLFileHandler(html_log_file, mode='w', encoding='utf-8')
        file_handler.setLevel(log_level)
        file_handler.setFormatter(html_formatter)
        root_logger.addHandler(file_handler)

    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('psycopg2').setLevel(logging.WARNING)


def get_logger(name: str, worker_id: Optional[int] = None) -> logging.Logger:
    """
    Get a logger instance with optional worker ID.
    
    Parameters
    ----------
    name : str
        Module name (typically __name__)
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
    # ============================================================
    # GENERATION 5
    # ============================================================
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


def get_module_logger(module_name: str = __name__) -> logging.Logger:
    """Get logger for current module (convenience function)."""
    return logging.getLogger(module_name)
