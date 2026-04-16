"""
Worker-Aware Logger Adapter
============================

Provides ``WorkerLoggerAdapter`` that injects ``worker_id`` into all
log records, enabling per-worker coloring through the ``ColorPalette``.

Usage::

    from src.utils.logger.adapters import WorkerLoggerAdapter

    logger = WorkerLoggerAdapter(base_logger, {'worker_id': 0})
    logger.info("This message will show [Worker-0]")
"""

import logging


class WorkerLoggerAdapter(logging.LoggerAdapter):
    """
    Logger adapter that injects worker_id into all log records.

    The ``worker_id`` is prepended to the message as ``[Worker-N]`` and
    also attached to the ``LogRecord`` so formatters can apply
    worker-specific coloring.
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
