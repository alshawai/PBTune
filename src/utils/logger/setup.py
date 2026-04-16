"""
Logging Setup and Logger Factory
==================================

Configures the root logger with colorized console output and optional
HTML file output. Provides ``get_logger()`` as the canonical way to
obtain a logger instance throughout the project.

Usage::

    from src.utils.logger import setup_logging, get_logger

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

from src.utils.logger.adapters import WorkerLoggerAdapter
from src.utils.logger.formatters import ColoredFormatter, HTMLFormatter, HTMLFileHandler


def add_html_file_logging(
    output_file: Path,
    show_module: bool = True,
) -> Path:
    """
    Attach an HTML log handler to the root logger without resetting other handlers.

    If an HTML handler already exists for a different file, it is replaced so each
    process writes to a single active HTML log destination.

    Parameters
    ----------
    output_file : Path
        Desired output path. The handler always writes to ``.html`` suffix.
    show_module : bool
        Show module names in HTML log output.

    Returns
    -------
    Path
        The normalized HTML log path actually used.
    """
    root_logger = logging.getLogger()
    html_log_file = output_file.with_suffix('.html')
    target = html_log_file.resolve()

    for handler in list(root_logger.handlers):
        if isinstance(handler, HTMLFileHandler):
            existing = Path(handler.baseFilename).resolve()
            if existing == target:
                return html_log_file
            root_logger.removeHandler(handler)
            handler.close()

    html_formatter = HTMLFormatter(show_module=show_module)
    file_handler = HTMLFileHandler(html_log_file, mode='w', encoding='utf-8')
    file_handler.setLevel(root_logger.level or logging.INFO)
    file_handler.setFormatter(html_formatter)
    root_logger.addHandler(file_handler)

    return html_log_file


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
    output_file : Optional[Path]
        Optional file path to write HTML logs
    show_module : bool
        Show module name in output
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
        file_path = add_html_file_logging(output_file=output_file, show_module=show_module)
        for handler in root_logger.handlers:
            if isinstance(handler, HTMLFileHandler):
                handler.setLevel(log_level)  # type: ignore
                if Path(handler.baseFilename).resolve() == file_path.resolve():
                    break

    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('psycopg2').setLevel(logging.WARNING)
    logging.getLogger('docker').setLevel(logging.WARNING)


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
