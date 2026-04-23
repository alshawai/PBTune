"""Unit tests for bare-metal worker memory utilization normalization."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.config.database import DatabaseConfig
from src.utils.environments.bare_metal import BareMetalEnvironment


class _StubCursor:
    """Cursor stub returning a fixed backend PID."""

    def execute(self, _query: str) -> None:
        return None

    def fetchone(self) -> tuple[int]:
        return (12345,)

    def close(self) -> None:
        return None


class _StubConnection:
    """Connection stub for backend PID lookup."""

    def cursor(self) -> _StubCursor:
        return _StubCursor()

    def close(self) -> None:
        return None


class _StubPostmasterProcess:
    """Process stub with parent+children RSS accounting support."""

    def __init__(self, rss_bytes: int, child_rss_bytes: list[int]) -> None:
        self._rss_bytes = rss_bytes
        self._children = [SimpleNamespace(memory_info=lambda rss=v: SimpleNamespace(rss=rss)) for v in child_rss_bytes]

    def memory_info(self) -> SimpleNamespace:
        return SimpleNamespace(rss=self._rss_bytes)

    def children(self, recursive: bool = True) -> list[SimpleNamespace]:
        _ = recursive
        return self._children


class _StubBackendProcess:
    """Backend process stub exposing parent() to postmaster."""

    def __init__(self, postmaster: _StubPostmasterProcess) -> None:
        self._postmaster = postmaster

    def parent(self) -> _StubPostmasterProcess:
        return self._postmaster


def _make_env(ram_bytes: int) -> BareMetalEnvironment:
    return BareMetalEnvironment(
        run_id="test-run",
        db_config=DatabaseConfig(
            user="postgres",
            password="postgres",
            host="127.0.0.1",
            port=5440,
            dbname="test_dataset",
        ),
        schema_provider=MagicMock(),
        ram_bytes=ram_bytes,
    )


def test_collect_memory_utilization_uses_worker_budget_when_available() -> None:
    """RSS should be normalized by worker RAM budget to avoid host-scale dilution."""
    env = _make_env(ram_bytes=4 * 1024)
    postmaster = _StubPostmasterProcess(rss_bytes=1024, child_rss_bytes=[1024])
    backend = _StubBackendProcess(postmaster=postmaster)

    with (
        patch("src.utils.environments.bare_metal.get_connection", return_value=_StubConnection()),
        patch("src.utils.environments.bare_metal.psutil.Process", return_value=backend),
        patch(
            "src.utils.environments.bare_metal.psutil.virtual_memory",
            return_value=SimpleNamespace(total=8 * 1024),
        ),
    ):
        ratio = env.collect_memory_utilization(worker_id=0)

    assert ratio == pytest.approx(0.5)


def test_collect_memory_utilization_falls_back_to_host_total_without_budget() -> None:
    """When worker budget is unavailable, host-total fallback should still work."""
    env = _make_env(ram_bytes=0)
    postmaster = _StubPostmasterProcess(rss_bytes=1024, child_rss_bytes=[1024])
    backend = _StubBackendProcess(postmaster=postmaster)

    with (
        patch("src.utils.environments.bare_metal.get_connection", return_value=_StubConnection()),
        patch("src.utils.environments.bare_metal.psutil.Process", return_value=backend),
        patch(
            "src.utils.environments.bare_metal.psutil.virtual_memory",
            return_value=SimpleNamespace(total=8 * 1024),
        ),
    ):
        ratio = env.collect_memory_utilization(worker_id=0)

    assert ratio == pytest.approx(0.25)