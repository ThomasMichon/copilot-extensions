"""Tier locking for agent-machines fleet-update -- mirrors ``self_update_lock.py``
exactly (see there for the full design rationale), scoped to the fleet-update
tier(s) instead of self-update's, so the two subsystems' locks can never
collide or interact.
"""

from __future__ import annotations

import ctypes
import dataclasses
import json
import os
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .fleet_update_state import STATE_VERSION, TIER_SPECS, lock_path, mutex_name


@dataclass
class LockSnapshot:
    pid: int | None
    started_at: str | None
    age_seconds: float | None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


def _parse_iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _read_lock_record(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _write_lock_record(path: Path, tier: str, pid: int, started_at: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": STATE_VERSION,
        "tier": tier,
        "pid": pid,
        "started_at": started_at,
    }
    temp = path.with_name(f".{path.name}.{pid}.tmp")
    temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def lock_snapshot(
    tier: str, home: Path | None = None, *, now: datetime | None = None
) -> LockSnapshot | None:
    record = _read_lock_record(lock_path(tier, home))
    if record is None:
        return None
    started = _parse_iso_utc(record.get("started_at"))
    current = now or _utc_now()
    age = (current - started).total_seconds() if started is not None else None
    pid_value = record.get("pid")
    pid = pid_value if isinstance(pid_value, int) and pid_value > 0 else None
    return LockSnapshot(pid=pid, started_at=record.get("started_at"), age_seconds=age)


class _WindowsMutex:
    WAIT_OBJECT_0 = 0x00000000
    WAIT_ABANDONED = 0x00000080
    WAIT_TIMEOUT = 0x00000102
    INFINITE = 0xFFFFFFFF

    def __init__(self, name: str):
        self.name = name
        self.handle = ctypes.windll.kernel32.CreateMutexW(None, False, name)
        if not self.handle:
            raise OSError(f"CreateMutexW failed for {name}")
        self.acquired = False

    def try_acquire(self) -> str:
        result = ctypes.windll.kernel32.WaitForSingleObject(self.handle, 0)
        if result == self.WAIT_OBJECT_0:
            self.acquired = True
            return "acquired"
        if result == self.WAIT_ABANDONED:
            self.acquired = True
            return "abandoned"
        if result == self.WAIT_TIMEOUT:
            return "timeout"
        raise OSError(f"WaitForSingleObject failed for {self.name}: {result}")

    def release(self) -> None:
        if self.acquired:
            ctypes.windll.kernel32.ReleaseMutex(self.handle)
            self.acquired = False

    def close(self) -> None:
        if self.handle:
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None


class _PosixFileLock:
    """POSIX equivalent of ``_WindowsMutex`` -- see ``self_update_lock.py``."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd: int | None = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
        self.acquired = False

    def try_acquire(self) -> str:
        import fcntl

        if self._fd is None:
            raise RuntimeError(f"POSIX file lock for {self.path} was already closed")
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return "timeout"
        self.acquired = True
        return "acquired"

    def release(self) -> None:
        import fcntl

        if self.acquired and self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            self.acquired = False

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None


def _posix_lock_file_path(tier: str, home: Path | None = None) -> Path:
    return lock_path(tier, home).with_suffix(".flock")


class TierLock(AbstractContextManager["TierLock"]):
    def __init__(
        self,
        *,
        tier: str,
        home: Path | None = None,
        pid: int | None = None,
        now: Callable[[], datetime] = _utc_now,
        pid_alive: Callable[[int | None], bool] = _pid_alive,
    ):
        self.spec = TIER_SPECS[tier]
        self.tier = tier
        self.home = home
        self.pid = pid if pid is not None else os.getpid()
        self._now = now
        self._pid_alive = pid_alive
        self._mutex: _WindowsMutex | _PosixFileLock | None = None
        self.reclaimed = False
        self.started_at: str | None = None
        self.snapshot: LockSnapshot | None = None

    def acquire(self) -> tuple[bool, str]:
        if sys.platform == "win32":
            self._mutex = _WindowsMutex(mutex_name(self.tier))
        else:
            self._mutex = _PosixFileLock(_posix_lock_file_path(self.tier, self.home))
        try:
            state = self._mutex.try_acquire()
        except Exception:
            self._mutex.close()
            self._mutex = None
            raise
        if state == "timeout":
            self.snapshot = lock_snapshot(self.tier, self.home, now=self._now())
            self.release()
            detail = "another run is already active"
            if self.snapshot is not None and self.snapshot.pid:
                detail += f" (pid {self.snapshot.pid})"
            return False, detail
        path = lock_path(self.tier, self.home)
        record = _read_lock_record(path)
        current = self._now()
        if record is not None:
            existing_pid = record.get("pid")
            started = _parse_iso_utc(record.get("started_at"))
            age = (current - started).total_seconds() if started is not None else None
            if (
                isinstance(existing_pid, int)
                and existing_pid > 0
                and existing_pid != self.pid
                and self._pid_alive(existing_pid)
            ):
                self.snapshot = LockSnapshot(
                    pid=existing_pid,
                    started_at=record.get("started_at"),
                    age_seconds=age,
                )
                self.release()
                return False, f"another run is already active (pid {existing_pid})"
            if (
                isinstance(existing_pid, int)
                and existing_pid > 0
                and existing_pid != self.pid
                and age is not None
                and age < self.spec.stale_seconds
            ):
                self.snapshot = LockSnapshot(
                    pid=existing_pid,
                    started_at=record.get("started_at"),
                    age_seconds=age,
                )
                self.release()
                minutes = int(self.spec.stale_seconds / 60)
                return (
                    False,
                    f"previous run died but its {minutes}-minute stale window has not elapsed",
                )
            self.reclaimed = record.get("pid") not in (None, self.pid)
        self.started_at = _iso_utc(current)
        if self.started_at is None:
            raise RuntimeError("could not encode the lock start timestamp")
        _write_lock_record(path, self.tier, self.pid, self.started_at)
        return True, "acquired"

    def release(self) -> None:
        if self.started_at is not None:
            path = lock_path(self.tier, self.home)
            record = _read_lock_record(path)
            if (
                isinstance(record, dict)
                and record.get("pid") == self.pid
                and record.get("started_at") == self.started_at
            ):
                try:
                    path.unlink()
                except OSError:
                    pass
        self.started_at = None
        if self._mutex is not None:
            self._mutex.release()
            self._mutex.close()
            self._mutex = None

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
        return None
