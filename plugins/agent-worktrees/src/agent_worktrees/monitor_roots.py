"""Non-session liveness roots for the resident status monitor."""

from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from . import config as cfg
from . import locks

_PICKER_HEARTBEAT_INTERVAL = 10.0
_PICKER_STALE_AFTER = 30.0


def _roots_dir() -> Path:
    return cfg.install_dir() / "status-monitor-roots.d"


class PickerHeartbeat:
    """Provable-liveness Picker registration refreshed while its TUI is open."""

    def __init__(
        self,
        project: str,
        *,
        interval: float = _PICKER_HEARTBEAT_INTERVAL,
        ensure_monitor: Callable[[], bool] | None = None,
    ) -> None:
        self.project = project
        self.interval = interval
        token = uuid.uuid4().hex[:12]
        self.path = _roots_dir() / f"picker-{os.getpid()}-{token}.json"
        self._ensure_monitor = ensure_monitor
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _write(self) -> bool:
        return locks.write_lock(
            self.path,
            extra={"kind": "picker", "project": self.project},
        )

    def _ensure(self) -> None:
        if self._ensure_monitor is None:
            return
        try:
            self._ensure_monitor()
        except Exception:
            pass

    def start(self) -> bool:
        """Write the root and start its background heartbeat."""
        if not self._write():
            return False
        self._ensure()
        self._thread = threading.Thread(
            target=self._run,
            name="status-monitor-picker-heartbeat",
            daemon=True,
        )
        self._thread.start()
        return True

    def _run(self) -> None:
        try:
            while not self._stop.wait(self.interval):
                self._write()
                self._ensure()
        finally:
            if self._stop.is_set():
                locks.remove_lock(self.path)

    def close(self) -> None:
        """Stop heartbeating and remove the root immediately."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        locks.remove_lock(self.path)


def live_picker_projects(
    *,
    now: float | None = None,
    stale_after: float = _PICKER_STALE_AFTER,
) -> set[str]:
    """Return live Picker projects and prune stale/invalid registrations."""
    now = time.time() if now is None else now
    projects: set[str] = set()
    try:
        paths = list(_roots_dir().glob("picker-*.json"))
    except OSError:
        return projects
    for path in paths:
        data = locks.read_lock(path)
        project = data.get("project") if isinstance(data, dict) else None
        created_at = data.get("created_at") if isinstance(data, dict) else None
        try:
            fresh = now - float(created_at) <= stale_after
        except (TypeError, ValueError):
            fresh = False
        if (isinstance(data, dict)
                and data.get("kind") == "picker"
                and isinstance(project, str)
                and project
                and fresh
                and locks.lock_is_live(data)):
            projects.add(project)
            continue
        locks.remove_lock(path)
    return projects
