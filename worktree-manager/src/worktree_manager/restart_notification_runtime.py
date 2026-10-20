"""Notification admission belongs to the existing active daemon lease."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from .manager_config import ManagerConfigError, RestartRecoveryConfig, load_config
from .restart_capabilities import recovery_capabilities
from .restart_attempts import RestartAttemptError, RestartAttempts
from .restart_observer import RestartObserver


log = logging.getLogger(__name__)


class RestartNotificationRuntime:
    """No native connection before active lease acquisition and explicit opt-in."""

    def __init__(
        self, root: Path, observer: RestartObserver, *,
        can_admit: Callable[[], bool] = lambda: True,
    ) -> None:
        self.root = root
        self.observer = observer
        self._config: RestartRecoveryConfig | None = None
        self._adapter = None
        self._capabilities: dict = {}
        self._blocker: str | None = None
        self._paused = True
        self._attempts: dict | None = None
        self._can_admit = can_admit
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._transport_status: dict | None = None
        self._cleanup_failed = False

    @property
    def status(self) -> dict:
        """Return an immutable-by-publication snapshot for server-thread health."""
        transport = self._transport_status
        result = {
            "capabilities": {
                **self._capabilities,
                **({"shutdown_notifications": {
                    "requested": True, **transport,
                }} if transport is not None else {}),
            },
            "automatic_eligible": False,
            "admitted": not self._paused,
        }
        if self._blocker:
            result["blocker"] = self._blocker
        if self._attempts is not None:
            result["attempts"] = self._attempts
        return result

    def activate(self) -> None:
        """Freeze startup evidence before the daemon publishes ordinary service."""
        self.observer.startup(recover_existing=True)
        try:
            self._config = load_config(self.root).restart_recovery
        except ManagerConfigError as exc:
            self._blocker = str(exc)
            log.warning("Restart notification configuration blocked: %s", exc)
            return
        epoch = self.observer.epoch
        self._capabilities = recovery_capabilities(
            self._config, epoch.platform if epoch is not None else None,
        )
        if self._config.automatic_restore:
            try:
                if epoch is None or self.observer.startup_result.get("blocker"):
                    raise RestartAttemptError("Native restart epoch/evidence is unconfirmed.")
                attempts = RestartAttempts(self.root).freeze(
                    self.observer.startup_result["candidates"], epoch,
                )
                self._attempts = {
                    "state": "awaiting-owner-admission",
                    "ids": [item["attempt_id"] for item in attempts],
                    "automatic_eligible": False,
                }
            except RestartAttemptError as exc:
                self._blocker = str(exc)
                log.warning("Restart attempt freezing blocked: %s", exc)
        self._paused = False

    def _start(self) -> None:
        if (
            self._config is None or not self._config.shutdown_notifications
            or self._blocker or self._worker is not None and self._worker.is_alive()
        ):
            return
        epoch = self.observer.epoch
        if epoch is None or self.observer.startup_result.get("blocker"):
            self._blocker = "Native restart epoch/evidence is unconfirmed."
        elif epoch.platform != "linux":
            self._blocker = self._capabilities["shutdown_notifications"]["blocker"]
        else:
            try:
                from .restart_notifications import LogindShutdownAdapter
            except ImportError as exc:
                self._blocker = f"Native shutdown transport dependency unavailable: {exc}"
            else:
                self._stop = threading.Event()
                self._transport_status = {"status": "connecting", "qualified": False}
                self._worker = threading.Thread(
                    target=self._run, args=(LogindShutdownAdapter, epoch, self._stop),
                    name="restart-notifications", daemon=False,
                )
                try:
                    self._worker.start()
                except RuntimeError as exc:
                    self._worker = None
                    self._blocker = f"Could not start native notification I/O: {exc}"
                    self._transport_status = {"status": "blocked", "qualified": False, "blocker": self._blocker}
        if self._blocker:
            log.warning("Restart notification admission blocked: %s", self._blocker)

    def _run(self, factory, epoch, stop: threading.Event) -> None:
        """One daemon-owned thread exclusively owns native I/O and its cleanup."""
        adapter = None
        failed = False
        try:
            adapter = factory(self.observer.journal, epoch)
            self._adapter = adapter
            adapter.start()
            while not stop.is_set():
                adapter.pump(wait_s=0.05)
                self._transport_status = dict(adapter.status)
                if adapter.status["status"] in {"blocked", "closed"}:
                    if adapter.status["status"] == "blocked":
                        self._blocker = adapter.status["blocker"]
                    break
        except Exception as exc:
            failed = True
            self._blocker = f"Native notification I/O failed ({type(exc).__name__}): {exc}"
            self._transport_status = {"status": "blocked", "qualified": False, "blocker": self._blocker}
            log.exception("Restart notification I/O failed")
        finally:
            if adapter is not None:
                previous = dict(adapter.status)
                try:
                    adapter.close()
                except Exception as exc:
                    failed = True
                    self._cleanup_failed = True
                    self._blocker = f"Native notification cleanup failed ({type(exc).__name__}): {exc}"
                    self._transport_status = {"status": "blocked", "qualified": False, "blocker": self._blocker}
                    log.exception("Restart notification cleanup failed")
                if not failed:
                    self._transport_status = previous if previous["status"] == "blocked" else dict(adapter.status)
                if adapter.cleanup_error is not None:
                    self._cleanup_failed = True
                    self._blocker = adapter.cleanup_error
                    self._transport_status = {
                        "status": "blocked", "qualified": False, "blocker": self._blocker,
                        "cleanup_error": adapter.cleanup_error,
                    }

    def pump(self, *, admitted: bool) -> None:
        """Admit one native I/O owner; ordinary daemon work never pumps its loop."""
        if not admitted:
            self.request_pause()
            return
        if self._config is None:
            return
        with self._lock:
            if not self._can_admit():
                self._request_pause()
                return
            self._paused = False
            self._start()

    @property
    def drained(self) -> bool:
        """Drain includes native callbacks, journal writes and descriptor cleanup."""
        with self._lock:
            return not self._cleanup_failed and (self._worker is None or not self._worker.is_alive())

    def _request_pause(self) -> None:
        self._paused = True
        self._stop.set()

    def request_pause(self) -> None:
        """Close admission without blocking a server-thread drain request."""
        with self._lock:
            self._request_pause()

    def pause(self) -> None:
        """Join owned native cleanup before releasing the daemon's active lease."""
        self.request_pause()
        worker = self._worker
        if worker is not None:
            worker.join()
        self._adapter = None

    def close(self) -> None:
        """Release notification ownership without changing enrollment/config."""
        self.pause()
        if self._cleanup_failed:
            raise RuntimeError(self._blocker)
