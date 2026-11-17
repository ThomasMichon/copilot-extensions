"""Bounded historical owner observation within the existing active daemon."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

from .engine_client import EngineError
from .restart_attempts import RestartAttemptError, RestartAttempts
from .restart_observer import HostEpoch
from .restart_owner_check import inspect_launch


log = logging.getLogger(__name__)


class RestartOutcomeObserver:
    """Read at most one unresolved receipt per twenty seconds; never launch."""

    def __init__(self, root: Path) -> None:
        self.attempts = RestartAttempts(root)
        self.status: dict = {"applied": False, "reason": "not-observed", "automatic_eligible": False}
        self._next_at = 0.0
        self._last_id: str | None = None

    def observe(
        self, epoch: HostEpoch | None, *, can_admit: Callable[[], bool],
        now: float | None = None,
    ) -> None:
        """Recheck active admission after slow owner I/O, before retaining bytes.

        The caller owns the active lease and loop-mutation fence. Historical
        ACKs are not live-session proof, and uncertainty never enables retry.
        """
        started = time.monotonic() if now is None else now
        if not can_admit() or started < self._next_at:
            return
        self._next_at = started + 20.0
        try:
            if epoch is None:
                raise ValueError("Native restart outcome epoch is unconfirmed.")
            attempts = self.attempts.snapshot(recover_existing=True)
            candidates = [
                item for item in attempts
                if all(item["target_epoch"][key] == getattr(epoch, key) for key in (
                    "platform", "environment_id",
                ))
                and not item.get("outcome", {}).get("seed_acknowledged", False)
            ]
            if not candidates:
                self.status = {
                    "applied": False, "reason": "no-unacknowledged-attempt",
                    "automatic_eligible": False,
                }
                return
            attempt = next(
                (item for item in candidates if self._last_id is None or item["attempt_id"] > self._last_id),
                candidates[0],
            )
            attempt_id = attempt["attempt_id"]
            self._last_id = attempt_id
            if not can_admit():
                self.status = {"applied": False, "reason": "admission-closed", "automatic_eligible": False}
                return
            value = inspect_launch(attempt_id, attempts, epoch)
            if not can_admit():
                self.status = {"applied": False, "reason": "admission-closed", "automatic_eligible": False}
                return
            retained = self.attempts.record_outcome(attempt_id, value)
            self.status = {
                "applied": True, "reason": "owner-receipt-retained", "attempt_id": attempt_id,
                "state": retained["outcome"]["state"], "automatic_eligible": False,
            }
        except (EngineError, RestartAttemptError, OSError, ValueError, TypeError, KeyError) as exc:
            self.status = {
                "applied": False, "reason": "outcome-unavailable",
                "blocker": str(exc), "automatic_eligible": False,
            }
            log.warning("Restart outcome observation blocked: %s", exc)
        finally:
            if now is None:
                self._next_at = time.monotonic() + 20.0
