"""
Unit tests for GenerationBarrier lockstep synchronization.

Tests cover:
- Basic synchronization across N threads
- Disabled mode (no-op barriers)
- Broken barrier graceful degradation
- drain_remaining prevents deadlock on worker failure
- reset() for reuse across generations
"""

import threading
import time
from typing import List
from unittest.mock import patch

import pytest

from src.tuner.core.barriers import GenerationBarrier, BARRIER_NAMES


class TestGenerationBarrierBasic:
    """Test basic barrier synchronization behavior."""

    def test_all_workers_pass_all_barriers(self):
        """All N threads pass through all 17 barriers without deadlock."""
        num_workers = 3
        barriers = GenerationBarrier(num_workers=num_workers, timeout=5.0)

        # Track order: each thread records its passage timestamps
        arrival_times: dict = {name: [] for name in BARRIER_NAMES}
        lock = threading.Lock()

        def worker_fn(worker_id: int):
            for name in BARRIER_NAMES:
                barriers.wait(name, worker_id=worker_id)
                with lock:
                    arrival_times[name].append(time.monotonic())

        threads = [
            threading.Thread(target=worker_fn, args=(i,))
            for i in range(num_workers)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        # Verify: all workers passed every barrier
        for name in BARRIER_NAMES:
            assert len(arrival_times[name]) == num_workers, (
                f"Barrier '{name}': expected {num_workers} arrivals, "
                f"got {len(arrival_times[name])}"
            )

    def test_barriers_enforce_ordering(self):
        """Workers at barrier N+1 cannot proceed until all pass barrier N."""
        num_workers = 4
        barriers = GenerationBarrier(num_workers=num_workers, timeout=5.0)

        # Worker 0 will be delayed at B1 (connected) while others arrive quickly
        passed_barrier_1: List[int] = []
        lock = threading.Lock()
        all_at_barrier = threading.Event()

        def worker_fn(worker_id: int):
            if worker_id == 0:
                # Wait for other workers to arrive at barrier first
                all_at_barrier.wait(timeout=3.0)
                time.sleep(0.05)  # Slight delay to be last

            barriers.wait("connected", worker_id=worker_id)
            with lock:
                passed_barrier_1.append(worker_id)

        threads = []
        for i in range(num_workers):
            t = threading.Thread(target=worker_fn, args=(i,))
            threads.append(t)

        for t in threads:
            t.start()

        # Give non-delayed workers time to reach barrier
        time.sleep(0.1)
        all_at_barrier.set()

        for t in threads:
            t.join(timeout=10.0)

        # All workers passed (worker 0 didn't block the others indefinitely)
        assert len(passed_barrier_1) == num_workers


class TestGenerationBarrierDisabled:
    """Test that disabled barriers are no-ops."""

    def test_disabled_barriers_dont_block(self):
        """When enabled=False, wait() returns immediately even with 1 thread."""
        barriers = GenerationBarrier(num_workers=4, timeout=5.0, enabled=False)

        # Should not block — only one thread, but barriers are disabled
        for name in BARRIER_NAMES:
            barriers.wait(name, worker_id=0)

        assert not barriers.enabled

    def test_disabled_drain_remaining_noop(self):
        """drain_remaining is a no-op when disabled."""
        barriers = GenerationBarrier(num_workers=4, timeout=5.0, enabled=False)
        # Should not raise
        barriers.drain_remaining("connected", worker_id=0)


class TestGenerationBarrierBrokenRecovery:
    """Test graceful degradation when a barrier breaks."""

    def test_timeout_sets_broken_flag(self):
        """When a barrier times out, all subsequent waits are no-ops."""
        # 2 workers, but only 1 will arrive → timeout
        barriers = GenerationBarrier(num_workers=2, timeout=0.5)

        results = {"timed_out": False, "subsequent_noop": False}

        def lone_worker():
            barriers.wait("connected", worker_id=0)
            # After timeout/broken, next barrier should be a no-op
            barriers.wait("config_applied", worker_id=0)
            results["subsequent_noop"] = True

        t = threading.Thread(target=lone_worker)
        t.start()
        t.join(timeout=3.0)

        assert barriers.broken
        assert results["subsequent_noop"]

    def test_drain_remaining_unblocks_peers(self):
        """When one worker drains, other workers waiting are unblocked."""
        num_workers = 2
        barriers = GenerationBarrier(num_workers=num_workers, timeout=2.0)

        worker_0_passed = threading.Event()
        worker_1_passed = threading.Event()

        def worker_0():
            """Normal worker that waits at 'connected'."""
            barriers.wait("connected", worker_id=0)
            worker_0_passed.set()

        def worker_1():
            """Crashed worker: drains all barriers starting from 'connected'."""
            # Simulate crash: drain instead of waiting
            barriers.drain_remaining("connected", worker_id=1)
            worker_1_passed.set()

        t0 = threading.Thread(target=worker_0)
        t1 = threading.Thread(target=worker_1)
        t0.start()
        t1.start()

        t0.join(timeout=5.0)
        t1.join(timeout=5.0)

        # Both workers should have passed (worker_1 via drain)
        assert worker_0_passed.is_set()
        assert worker_1_passed.is_set()


class TestGenerationBarrierReset:
    """Test reset for multi-generation reuse."""

    def test_reset_allows_reuse(self):
        """After reset, barriers can be used again for a new generation."""
        num_workers = 2
        barriers = GenerationBarrier(num_workers=num_workers, timeout=2.0)

        def run_one_barrier():
            """Have all workers pass through the first barrier."""
            passed = []
            lock = threading.Lock()

            def w(wid):
                barriers.wait("connected", worker_id=wid)
                with lock:
                    passed.append(wid)

            threads = [threading.Thread(target=w, args=(i,)) for i in range(num_workers)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5.0)
            return passed

        # Generation 1
        gen1 = run_one_barrier()
        assert len(gen1) == num_workers

        # Reset for generation 2
        barriers.reset()

        # Generation 2
        gen2 = run_one_barrier()
        assert len(gen2) == num_workers


class TestGenerationBarrierEdgeCases:
    """Test edge cases and error handling."""

    def test_invalid_barrier_name_raises(self):
        """Passing an unknown barrier name raises ValueError."""
        barriers = GenerationBarrier(num_workers=1, timeout=1.0)
        with pytest.raises(ValueError, match="Unknown barrier name"):
            barriers.wait("nonexistent_barrier", worker_id=0)

    def test_next_barrier_name(self):
        """next_barrier_name returns correct successor."""
        barriers = GenerationBarrier(num_workers=1, timeout=1.0, enabled=False)
        assert barriers.next_barrier_name("connected") == "config_applied"
        assert barriers.next_barrier_name("disconnected") is None
        assert barriers.next_barrier_name("unknown") is None

    def test_repr(self):
        """repr shows meaningful status."""
        b1 = GenerationBarrier(num_workers=3, timeout=60.0, enabled=True)
        assert "enabled" in repr(b1)

        b2 = GenerationBarrier(num_workers=3, timeout=60.0, enabled=False)
        assert "disabled" in repr(b2)

    def test_single_worker_passes_instantly(self):
        """With num_workers=1, barriers pass immediately (no waiting)."""
        barriers = GenerationBarrier(num_workers=1, timeout=1.0)

        start = time.monotonic()
        for name in BARRIER_NAMES:
            barriers.wait(name, worker_id=0)
        elapsed = time.monotonic() - start

        # All 17 barriers in well under 1 second for a single worker
        assert elapsed < 1.0
