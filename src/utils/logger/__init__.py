"""
Enhanced Logging for PBT PostgreSQL Tuning
===========================================

Provides colorized, structured logging with severity-based colors,
worker-specific coloring for parallel execution, configurable verbosity,
and professional banners for tuner and evaluation workflows.

Usage::

    from src.utils.logger import setup_logging, get_logger

    # Setup once at application start
    setup_logging(verbosity='INFO', enable_colors=True)

    # Get logger for any module
    logger = get_logger(__name__, worker_id=0)
    logger.info("Worker-specific log message")
"""

# Colors & control codes
from src.utils.logger.colors import (
    ColorCode,
    ColorPalette,
    colors_enabled,
    set_colors_enabled,
)

# Formatters
from src.utils.logger.formatters import (
    ColoredFormatter,
    HTMLFileHandler,
    HTMLFormatter,
)

# Logger adapter
from src.utils.logger.adapters import WorkerLoggerAdapter

# Setup & factory
from src.utils.logger.setup import (
    add_html_file_logging,
    get_logger,
    setup_logging,
)

# Color context
from src.utils.logger.context import (
    ColorContext,
    get_color_context,
)

# Banners
from src.utils.logger.banners import (
    get_evaluation_banner,
    get_isolation_warning_banner,
    print_startup_banner,
)

# Helper functions
from src.utils.logger.helpers import (
    format_worker_metrics_table,
    log_generation_summary,
    log_worker_metrics_table,
    log_section_header,
    log_final_summary,
)

__all__ = [
    # Colors
    "ColorCode",
    "ColorPalette",
    "colors_enabled",
    "set_colors_enabled",
    # Color context
    "ColorContext",
    "get_color_context",
    # Formatters
    "ColoredFormatter",
    "HTMLFormatter",
    "HTMLFileHandler",
    # Adapters
    "WorkerLoggerAdapter",
    # Setup
    "setup_logging",
    "add_html_file_logging",
    "get_logger",
    # Banners
    "print_startup_banner",
    "get_evaluation_banner",
    "get_isolation_warning_banner",
    # Helpers
    "format_worker_metrics_table",
    "log_section_header",
    "log_generation_summary",
    "log_worker_metrics_table",
    "log_final_summary",
]
