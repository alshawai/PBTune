"""
Co-Tenant Load Controller for the BO Baseline
=============================================

Fair PBT-vs-BO comparison on a single host.

PBT tunes with ``N`` workers running *in parallel* on one machine; the BO
baseline's optimizer is strictly *sequential* (one ask→eval→tell at a time —
the wall-clock-efficiency claim the paper rests on). Per-instance Docker cgroup
limits (cpuset, memory, blkio) equalize *allotment*, but a solo BO trial still
runs on an otherwise-idle box and enjoys uncontended CPU turbo, memory
bandwidth, LLC, and disk-queue depth that ``N`` concurrent PBT workers do not.
On I/O- and bandwidth-bound workloads (TPC-H especially) this makes BO look
artificially strong *during tuning*.

This controller removes that confound **without** making BO parallel or
devaluing its measured instance. The BO trial keeps running alone on worker 0's
full per-worker slice. Around each trial's measurement window, ``N-1``
**background load instances** (worker ids ``1..N-1``) run the same benchmark on
their own disjoint cpusets, reproducing exactly the cross-worker contention a
PBT generation generates. Synchronization reuses the project's own
:class:`GenerationBarrier`: the foreground trial and every background loader
share one barrier object, so the background workload runs in lockstep precisely
during the foreground warmup→measurement window (barriers B8–B9) and nowhere
else.

Design choices
--------------
- **Same path as PBT.** Background loaders call the *same*
  ``WorkloadOrchestrator.evaluate_worker(..., barriers=...)`` PBT uses (it is
  already thread-safe and barrier-aware), so they traverse B1–B17 identically
  and contend during exactly the measurement window. No orchestrator changes.
- **Fixed LHS load configs.** Each background instance is pinned to one
  seeded Latin-Hypercube-sampled config drawn once from the same
  :class:`KnobSpace` (so memory/CPU knob dependencies resolve through the normal
  pipeline). Constant, reproducible contention across all BO trials, decoupled
  from PBT internals.
- **Apply every round for barrier symmetry.** The fixed config is applied
  (with the single required restart) on the first trial; later trials re-apply
  the same values without restart (``force_restart_next_eval`` is cleared after
  the first). ``apply_config=True`` on every round is required because the
  ``config_applied`` (B2) and ``config_verified`` (B5) barriers are conditional
  on ``apply_config`` inside ``evaluate_worker`` — mismatch causes deadlock.
- **Degree is mandatory and matched.** The controller is constructed with
  ``degree = N`` taken from the matched PBT session's ``num_parallel_workers``;
  the BO runner enforces this whenever a PBT session is supplied.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

from src.config.database import DatabaseConfig
from src.knobs.knob_space import KnobSpace
from src.tuners.engine.barriers import GenerationBarrier
from src.tuners.engine.worker import Worker
from src.tuners.engine.orchestrator import WorkloadOrchestrator
from src.utils.environments.base import DatabaseEnvironment
from src.utils.logger import get_logger

LOGGER = get_logger("CoTenantLoad")


class CoTenantLoadController:
    """Drives ``degree - 1`` background load instances around each BO trial.

    Parameters
    ----------
    degree:
        Total co-tenancy degree ``N`` (= matched PBT ``num_parallel_workers``).
        ``N - 1`` background instances run alongside the single foreground BO
        trial. ``degree <= 1`` disables the controller (no background load).
    env:
        The already-created :class:`DatabaseEnvironment`. Must have been set up
        with ``num_workers = degree`` so worker ids ``1..degree-1`` have live
        containers on disjoint cpusets.
    orchestrator:
        The BO runner's :class:`WorkloadOrchestrator`. Reused read-only for the
        background ``evaluate_worker`` calls (it carries workload + durations).
    knob_space:
        Knob space the background load configs are LHS-sampled from.
    base_db_config:
        Template DB config; per-worker host/port come from ``env.get_db_config``.
    seed:
        Seed for the LHS draw, so the background load is reproducible.
    """

    def __init__(
        self,
        degree: int,
        env: DatabaseEnvironment,
        orchestrator: WorkloadOrchestrator,
        knob_space: KnobSpace,
        base_db_config: DatabaseConfig,
        seed: int = 0,
        bg_restore_interval: int = 10,
    ) -> None:
        self.degree = max(1, int(degree))
        self.env = env
        self.orchestrator = orchestrator
        self.knob_space = knob_space
        self.base_db_config = base_db_config
        self.seed = seed
        self.bg_restore_interval = max(1, int(bg_restore_interval))
        self._round_counter: int = 0

        self._bg_worker_ids: List[int] = list(range(1, self.degree))
        self._bg_workers: List[Worker] = []
        self._pool: Optional[ThreadPoolExecutor] = None

        if self.enabled:
            self._build_background_workers()
            # One persistent pool sized to run all background loaders at once.
            self._pool = ThreadPoolExecutor(
                max_workers=len(self._bg_worker_ids),
                thread_name_prefix="cotenant",
            )
            LOGGER.info(
                "Co-tenancy ENABLED: degree=%d → %d background load instance(s) "
                "(worker ids %s) will contend during each BO measurement window.",
                self.degree,
                len(self._bg_worker_ids),
                self._bg_worker_ids,
            )
        else:
            LOGGER.info(
                "Co-tenancy DISABLED (degree=%d): BO runs without background load.",
                self.degree,
            )

    @property
    def enabled(self) -> bool:
        """Whether any background load instances exist."""
        return self.degree > 1 and bool(self._bg_worker_ids)

    @property
    def barrier_parties(self) -> int:
        """Number of threads that meet at each barrier (foreground + background)."""
        return len(self._bg_worker_ids) + 1

    def _build_background_workers(self) -> None:
        """Create background Workers with fixed, seeded LHS load configs."""
        # One LHS design row per background worker. Deterministic given the
        # seed; ``sample_diverse_configs`` uses Latin Hypercube Sampling and
        # already returns dependency-repaired configs (memory/CPU knob
        # relationships resolved exactly as a real worker config would be).
        designs = self.knob_space.sample_diverse_configs(
            num_samples=len(self._bg_worker_ids), seed=self.seed, quiet=True
        )
        for idx, worker_id in enumerate(self._bg_worker_ids):
            worker = Worker(worker_id=worker_id, knob_space=self.knob_space)
            worker.db_config = self.env.get_db_config(worker_id)
            worker.knob_config = dict(designs[idx])
            # Background instances must restart once to install their config.
            worker.force_restart_next_eval = True
            self._bg_workers.append(worker)

    def _run_one_background(
        self,
        worker: Worker,
        barriers: GenerationBarrier,
        restore_due: bool = False,
        next_eval_will_restore: bool = False,
    ) -> None:
        """Run a single background loader through the lockstep B1–B17 path.

        Metrics and score are discarded — the only purpose is to contend for
        host resources during the foreground worker's measurement window. Any
        failure is swallowed (logged at debug); ``evaluate_worker`` itself
        drains remaining barriers on error so the foreground never deadlocks.

        ``apply_config=True`` on *every* round is required, not optional: the
        ``config_applied`` (B2) and ``config_verified`` (B5) barriers are gated
        by ``apply_config`` inside ``evaluate_worker``. The foreground trial
        always applies its config, so the background must too, or the two sides
        would traverse different barrier sets and deadlock. The fixed load
        config only restarts the instance on the first round
        (``force_restart_next_eval`` is cleared by ``evaluate_worker`` after the
        first restart); later rounds are cheap reloads of the same values.
        """
        try:
            self.orchestrator.evaluate_worker(
                worker,
                apply_config=True,
                barriers=barriers,
                restore_due=restore_due,
                next_eval_will_restore=next_eval_will_restore,
            )
        except Exception as exc:  # noqa: BLE001 - background load is best-effort
            LOGGER.debug(
                "Background loader (worker %d) raised (ignored): %s",
                worker.worker_id,
                exc,
            )

    def make_barrier(self) -> Optional[GenerationBarrier]:
        """Return a fresh barrier for the next trial, or ``None`` if disabled.

        The foreground BO trial passes this object to ``evaluate_config`` /
        ``evaluate_worker`` so it joins the same lockstep as the background
        loaders. A new object per trial avoids cross-trial barrier state.
        """
        if not self.enabled:
            return None
        return GenerationBarrier(num_workers=self.barrier_parties, enabled=True)

    def start_round(self, barriers: Optional[GenerationBarrier]) -> List:
        """Launch all background loaders for one trial; returns their futures.

        Must be paired with :meth:`finish_round`. The foreground trial should be
        invoked *after* this call (both share ``barriers``), then awaited, then
        ``finish_round`` joins the loaders.
        """
        if not self.enabled or barriers is None or self._pool is None:
            return []

        self._round_counter += 1
        bg_restore_due = (
            self._round_counter > 1
            and self._round_counter % self.bg_restore_interval == 0
        )
        bg_next_restore = (
            (self._round_counter + 1) % self.bg_restore_interval == 0
        )

        if bg_restore_due:
            LOGGER.info(
                "Background workers: snapshot restore due (round %d, "
                "interval=%d)",
                self._round_counter,
                self.bg_restore_interval,
            )

        futures = [
            self._pool.submit(
                self._run_one_background,
                worker,
                barriers,
                restore_due=bg_restore_due,
                next_eval_will_restore=bg_next_restore,
            )
            for worker in self._bg_workers
        ]
        return futures

    @staticmethod
    def finish_round(futures: List) -> None:
        """Join all background loaders for the round (best-effort)."""
        for fut in futures:
            try:
                fut.result()
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("Background loader future error (ignored): %s", exc)

    def shutdown(self) -> None:
        """Stop the background pool. Containers are torn down by env.cleanup()."""
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None

    def to_metadata(self) -> dict:
        """Serialise the co-tenancy configuration for the session JSON."""
        return {
            "enabled": self.enabled,
            "degree": self.degree,
            "background_worker_ids": list(self._bg_worker_ids),
            "foreground_worker_id": 0,
            "load_config_seed": self.seed,
            "load_config_source": "lhs_diverse",
            "bg_restore_interval": self.bg_restore_interval,
        }
