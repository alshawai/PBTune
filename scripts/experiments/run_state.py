"""Crash-safe process coordination for long-running experiment campaigns."""

from __future__ import annotations

import fcntl
import json
import os
import socket
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp with an explicit ``Z`` suffix."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class RunnerIdentity:
    """Stable ownership metadata for one experiment-runner process."""

    runner_id: str
    hostname: str
    pid: int
    started_at: str

    @classmethod
    def create(cls) -> RunnerIdentity:
        """Build an identity for the current process."""
        return cls(
            runner_id=str(uuid.uuid4()),
            hostname=socket.gethostname(),
            pid=os.getpid(),
            started_at=utc_now(),
        )

    def to_dict(self) -> dict[str, str | int]:
        """Serialize the identity into manifest-compatible data."""
        return {
            "runner_id": self.runner_id,
            "hostname": self.hostname,
            "pid": self.pid,
            "started_at": self.started_at,
        }


class CampaignLockError(RuntimeError):
    """Raised when another process already owns the campaign lock."""


class CampaignFileLock:
    """Non-blocking POSIX advisory lock retained for a campaign's lifetime."""

    def __init__(self, path: Path, owner: RunnerIdentity) -> None:
        self.path = path
        self.owner = owner
        self._file: IO[str] | None = None

    def acquire(self) -> None:
        """Acquire the lock or fail with the current owner's metadata."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            lock_file.seek(0)
            owner = lock_file.read().strip() or "owner metadata unavailable"
            lock_file.close()
            raise CampaignLockError(
                f"Another experiment runner owns {self.path}: {owner}"
            ) from exc

        lock_file.seek(0)
        lock_file.truncate()
        json.dump(self.owner.to_dict(), lock_file, indent=2)
        lock_file.write("\n")
        lock_file.flush()
        os.fsync(lock_file.fileno())
        self._file = lock_file

    def release(self) -> None:
        """Release the lock while retaining its last-owner audit record."""
        if self._file is None:
            return
        fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        self._file.close()
        self._file = None

    @contextmanager
    def held(self) -> Iterator[None]:
        """Acquire and reliably release the campaign lock."""
        self.acquire()
        try:
            yield
        finally:
            self.release()


def atomic_write_json(path: Path, payload: dict) -> None:
    """Durably replace a JSON file without exposing a partial write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temp_path, path)
        temp_path = None

        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
