"""Unit tests for the RestartPolicy module (TuningMode + should_restart)."""

from __future__ import annotations


from src.tuner.benchmark.restart_policy import TuningMode, should_restart


# ── ONLINE mode ──────────────────────────────────────────────────────────


class TestOnlineMode:
    """ONLINE mode should never restart (runtime knobs only)."""

    def test_no_restart_when_restart_required(self) -> None:
        assert (
            should_restart(TuningMode.ONLINE, restart_required=True, generation=0)
            is False
        )

    def test_no_restart_even_at_interval_boundary(self) -> None:
        assert (
            should_restart(TuningMode.ONLINE, restart_required=True, generation=10)
            is False
        )

    def test_force_overrides_online(self) -> None:
        assert (
            should_restart(
                TuningMode.ONLINE, restart_required=True, generation=0, force=True
            )
            is True
        )

    def test_no_restart_when_not_required(self) -> None:
        assert (
            should_restart(TuningMode.ONLINE, restart_required=False, generation=0)
            is False
        )


# ── OFFLINE mode ─────────────────────────────────────────────────────────


class TestOfflineMode:
    """OFFLINE mode should restart every generation when restart-required knobs are present."""

    def test_restart_when_required(self) -> None:
        assert (
            should_restart(TuningMode.OFFLINE, restart_required=True, generation=1)
            is True
        )

    def test_restart_every_generation(self) -> None:
        for gen in range(20):
            assert (
                should_restart(
                    TuningMode.OFFLINE, restart_required=True, generation=gen
                )
                is True
            )

    def test_no_restart_when_not_required(self) -> None:
        assert (
            should_restart(TuningMode.OFFLINE, restart_required=False, generation=1)
            is False
        )

    def test_force_restart_even_without_required(self) -> None:
        assert (
            should_restart(
                TuningMode.OFFLINE, restart_required=False, generation=1, force=True
            )
            is True
        )


# ── ADAPTIVE mode ────────────────────────────────────────────────────────


class TestAdaptiveMode:
    """ADAPTIVE mode should restart only at interval boundaries."""

    def test_restart_at_interval_boundary(self) -> None:
        assert (
            should_restart(
                TuningMode.ADAPTIVE,
                restart_required=True,
                generation=10,
                adaptive_restart_interval=10,
            )
            is True
        )

    def test_no_restart_off_boundary(self) -> None:
        assert (
            should_restart(
                TuningMode.ADAPTIVE,
                restart_required=True,
                generation=7,
                adaptive_restart_interval=10,
            )
            is False
        )

    def test_restart_at_generation_zero(self) -> None:
        """Generation 0 is always a boundary (0 % N == 0)."""
        assert (
            should_restart(
                TuningMode.ADAPTIVE,
                restart_required=True,
                generation=0,
                adaptive_restart_interval=10,
            )
            is True
        )

    def test_custom_interval(self) -> None:
        assert (
            should_restart(
                TuningMode.ADAPTIVE,
                restart_required=True,
                generation=5,
                adaptive_restart_interval=5,
            )
            is True
        )
        assert (
            should_restart(
                TuningMode.ADAPTIVE,
                restart_required=True,
                generation=4,
                adaptive_restart_interval=5,
            )
            is False
        )

    def test_no_restart_when_not_required(self) -> None:
        assert (
            should_restart(
                TuningMode.ADAPTIVE,
                restart_required=False,
                generation=10,
                adaptive_restart_interval=10,
            )
            is False
        )

    def test_force_override(self) -> None:
        assert (
            should_restart(
                TuningMode.ADAPTIVE, restart_required=False, generation=3, force=True
            )
            is True
        )


# ── Edge cases ───────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases that span modes."""

    def test_none_generation_adaptive(self) -> None:
        """None generation should not trigger restart in ADAPTIVE."""
        assert (
            should_restart(TuningMode.ADAPTIVE, restart_required=True, generation=None)
            is False
        )

    def test_none_generation_offline(self) -> None:
        """None generation should still restart in OFFLINE when required."""
        assert (
            should_restart(TuningMode.OFFLINE, restart_required=True, generation=None)
            is True
        )

    def test_force_always_wins(self) -> None:
        """Force=True should override any mode/state combination."""
        for mode in TuningMode:
            assert (
                should_restart(
                    mode, restart_required=False, generation=None, force=True
                )
                is True
            )

    def test_tuning_mode_values(self) -> None:
        """Enum values should be lowercase strings."""
        assert TuningMode.ONLINE.value == "online"
        assert TuningMode.OFFLINE.value == "offline"
        assert TuningMode.ADAPTIVE.value == "adaptive"
