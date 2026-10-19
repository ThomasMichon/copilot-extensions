"""Notification admission belongs to the existing active daemon lease."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

from .manager_config import ManagerConfigError, RestartRecoveryConfig, load_config
from .restart_capabilities import recovery_capabilities
from .restart_observer import RestartObserver


log = logging.getLogger(__name__)


class RestartNotificationRuntime:
    """No native connection before active lease acquisition and explicit opt-in."""

    def __init__(self, root: Path, observer: RestartObserver) -> None:
        self.root = root
        self.observer = observer
        self._config: RestartRecoveryConfig | None = None
        self._adapter = None
        self._capabilities: dict = {}
        self._blocker: str | None = None
        self._paused = True

    @property
    def status(self) -> dict:
        """Return an immutable-by-publication snapshot for server-thread health."""
        adapter = self._adapter
        result = {
            "capabilities": {
                **self._capabilities,
                **({"shutdown_notifications": {
                    "requested": True, **adapter.status,
                }} if adapter is not None else {}),
            },
            "automatic_eligible": False,
            "admitted": not self._paused,
        }
        if self._blocker:
            result["blocker"] = self._blocker
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
        self._paused = False

    def _start(self) -> None:
        if (
            self._config is None or not self._config.shutdown_notifications
            or self._blocker or self._adapter is not None
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
                self._adapter = LogindShutdownAdapter(self.observer.journal, epoch)
                self._adapter.start()
        if self._blocker:
            log.warning("Restart notification admission blocked: %s", self._blocker)

    def pump(self, *, admitted: bool, wait_s: float = 0.0) -> None:
        """Run only inside the owning daemon loop's current mutation admission."""
        if not admitted:
            self.pause()
            return
        if self._config is None:
            return
        self._paused = False
        self._start()
        if self._adapter is not None:
            if wait_s:
                self._adapter.pump(wait_s=wait_s)
            else:
                self._adapter.pump()

    def wait(
        self, seconds: float, *, admitted: Callable[[], bool],
        mutation: Callable[[], AbstractContextManager],
    ) -> None:
        """Service native I/O during the existing idle wait, on its owning loop."""
        deadline = time.monotonic() + seconds
        while (remaining := deadline - time.monotonic()) > 0:
            adapter = self._adapter
            if adapter is None or adapter.status["status"] in {"blocked", "closed"}:
                time.sleep(remaining)
                return
            with mutation():
                self.pump(admitted=admitted(), wait_s=min(0.05, remaining))

    def pause(self) -> None:
        """Close native resources on the owning loop before releasing its lease."""
        self._paused = True
        if self._adapter is not None:
            self._adapter.close()
            self._adapter = None

    def close(self) -> None:
        """Release notification ownership without changing enrollment/config."""
        self.pause()
