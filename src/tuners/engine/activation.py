"""
Configuration Activation
========================

The reload/restart/none decision for a worker's knob configuration:

- :func:`apply_configuration` — write knobs (ALTER SYSTEM only) then activate via
  :func:`~src.tuners.engine.restart_policy.should_restart` (reload, restart, or
  skip when a snapshot restore is due)
- :func:`perform_restart` — close the connection and restart the instance via the
  environment

Both are free functions taking explicit ``config``/``env`` handles rather than an
orchestrator instance. The recorder-span wrapping stays inside the functions so
timing behavior is identical. ``WorkloadOrchestrator`` keeps thin delegating
methods over them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

import psycopg2
from psycopg2.extensions import connection as PostgresConnection

from src.tuners.engine.restart_policy import should_restart
from src.tuners.engine.worker import BaseWorker
from src.utils.applicator import KnobApplicator
from src.utils.environments.base import DatabaseEnvironment
from src.utils.types import TuningMode
from src.utils.timing import TimingRecorder
from src.utils.logger import get_color_context

if TYPE_CHECKING:
    # Imported for typing only — importing at runtime would be circular
    # (orchestrator imports this module).
    from src.tuners.engine.orchestrator import WorkloadOrchestratorConfig

COLORS = get_color_context()


def apply_configuration(
    config: "WorkloadOrchestratorConfig",
    env: DatabaseEnvironment,
    connection: PostgresConnection,
    worker: BaseWorker,
    knob_applicator: KnobApplicator,
    force_restart: bool = False,
    generation: Optional[int] = None,
    restore_due: bool = False,
    recorder: Optional[TimingRecorder] = None,
    restart_fn: Optional[Callable[[PostgresConnection, BaseWorker], bool]] = None,
) -> bool:
    """
    Apply knob configuration and optionally restart via policy.

    This writes knobs via apply_only (ALTER SYSTEM only), then decides
    activation strategy (reload/restart/none) via RestartPolicy. When
    ``restore_due`` is True, activation is skipped because the caller will
    perform a snapshot restore that serves as the restart.

    ``restart_fn`` is the seam used to perform the restart; it defaults to the
    module-level :func:`perform_restart` bound to ``env``. The orchestrator
    passes its own ``_perform_restart`` method so subclass overrides and test
    patches remain effective.

    Returns
    -------
    bool
        True if restart occurred during this application
    """
    if restart_fn is None:
        def restart_fn(conn: PostgresConnection, wkr: BaseWorker) -> bool:
            return perform_restart(env, conn, worker=wkr)

    try:
        if recorder is not None:
            with recorder.span("apply_only"):
                result = knob_applicator.apply_only(worker.knob_config)  # type: ignore
        else:
            result = knob_applicator.apply_only(worker.knob_config)  # type: ignore

        restart_required = bool(
            result.restart_required and len(result.restart_required) > 0
        )

        if restart_required:
            restart_required_params = list(result.restart_required)
            first_three = (
                restart_required_params[:3] + ["..."]
                if len(restart_required_params) > 3
                else restart_required_params
            )

            worker.logger.info(
                " %s➤ Restart required for %d parameter(s): %s%s",
                COLORS.bold,
                len(restart_required_params),
                ", ".join(first_three),
                COLORS.reset,
            )

        # When snapshot restore is due, the restore IS the restart.
        # Skip activation here; the orchestrator handles it.
        if restore_due:
            worker.logger.debug(
                " Snapshot restore due — skipping activation (restore IS the restart)"
            )
            return False

        do_restart = should_restart(
            mode=config.tuning_mode,
            restart_required=restart_required,
            generation=generation,
            adaptive_restart_interval=config.adaptive_restart_interval,
            force=force_restart,
        )

        if do_restart:
            worker.logger.debug(" Restarting PostgreSQL instance...")
            if recorder is not None:
                with recorder.span("activate_restart", strategy="restart"):
                    return restart_fn(connection, worker)
            return restart_fn(connection, worker)

        if restart_required and not do_restart:
            if config.tuning_mode == TuningMode.ADAPTIVE:
                interval = config.adaptive_restart_interval
                next_restart = (
                    ((generation // interval) + 1) * interval
                    if generation is not None
                    else interval
                )
                worker.logger.info(
                    " ➤ Deferring restart (will restart at generation %s%d%s)",
                    COLORS.bold,
                    next_restart,
                    COLORS.reset,
                )
            elif config.tuning_mode == TuningMode.ONLINE:
                worker.logger.info(
                    " %s➤ ONLINE mode: restart-required knobs written but restart skipped%s",
                    COLORS.bold,
                    COLORS.reset,
                )

        # Non-restart activation: reload for sighup params
        if not do_restart and result.applied_count > 0 and not restart_required:
            # Reload to pick up sighup/user params without restart
            if recorder is not None:
                with recorder.span("activate_reload", strategy="reload"):
                    activation = knob_applicator.activate(
                        restart_required=False,
                        env=env,
                        worker_id=worker.worker_id,
                    )
            else:
                activation = knob_applicator.activate(
                    restart_required=False,
                    env=env,
                    worker_id=worker.worker_id,
                )
            if not activation.success:
                worker.logger.warning(
                    " ➤ Configuration reload failed: %s", activation.message
                )

        return False

    except Exception as e:
        worker.logger.error("Failed to apply configuration: %s", e)
        raise


def perform_restart(
    env: DatabaseEnvironment,
    connection: PostgresConnection,
    worker: BaseWorker,
) -> bool:
    """Restart PostgreSQL via the injected environment.

    Closes the connection before restart, then restarts the worker's
    instance through the environment abstraction.

    Returns
    -------
    bool
        True if restart succeeded
    """
    try:
        # Close connection before restart
        try:
            if connection and not connection.closed:
                connection.close()
        except (psycopg2.Error, AttributeError):
            pass

        if env.restart_instance(worker.worker_id, quiet=True):
            worker.logger.info(" ➤ Restart successful")

            return True
        else:
            worker.logger.error(" ➤ Restart failed")
            return False

    except Exception as e:
        worker.logger.error("➤ Restart failed with exception: %s", e)
        return False
