"""Background-recovery dormancy/backoff bookkeeping for ``SessionManager``.

Split out of ``session_manager.py`` (module-size guard) to keep the
explicit-stop dormancy gate, exponential reconnect backoff, and idle-unwatched
auto-dormancy (#2445/#2378/#2379) in one small, focused unit. A mixin, not a
standalone service -- it reads/writes ``SessionManager``'s own dicts and calls
its other methods (``stop_session``, ``_recover_remote_host_records``).
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

from .models import SessionStatus

if TYPE_CHECKING:
    from .session_manager import Session

log = logging.getLogger("agent-bridge")

# recover_disconnected_hosts() heartbeat-driven reattach (#2998): a remote
# host is always "presumed alive" until ATTACH proves otherwise, so a
# genuinely, silently dead far side was retried every 15s heartbeat forever.
# Past this many CONSECUTIVE failures, force the authoritative liveness check
# (_recover_remote_host_records) instead of retrying blindly again.
_DISCONNECTED_REATTACH_ESCALATE_AFTER = 3
_BACKGROUND_RECOVERY_BACKOFF_BASE_S = 30.0
_BACKGROUND_RECOVERY_BACKOFF_MAX_S = 300.0
_BACKGROUND_RECOVERY_IDLE_DORMANCY_AFTER = 6


def _background_recovery_backoff_seconds(failures: int) -> float:
    """Exponential backoff for repeated background recovery failures."""
    if failures <= 0:
        return 0.0
    return min(
        _BACKGROUND_RECOVERY_BACKOFF_MAX_S,
        _BACKGROUND_RECOVERY_BACKOFF_BASE_S * (2 ** (failures - 1)),
    )


class _RecoveryDormancyMixin:
    """Explicit-stop dormancy, reconnect backoff, and idle auto-dormancy."""

    def _clear_disconnected_reattach_retry_state(self, session_id: str) -> None:
        self._disconnected_reattach_failures.pop(session_id, None)
        self._disconnected_reattach_retry_at.pop(session_id, None)

    def _set_background_recovery_enabled(
        self, session: "Session", enabled: bool
    ) -> None:
        session.background_recovery_enabled = enabled
        self._db.update_session_background_recovery(session.session_id, enabled)
        if not enabled:
            self._clear_disconnected_reattach_retry_state(session.session_id)

    def _can_background_dormant_after_failure(
        self, session: "Session", failures: int
    ) -> bool:
        return (
            failures >= _BACKGROUND_RECOVERY_IDLE_DORMANCY_AFTER
            and session.status == SessionStatus.IDLE
            and session.subscriber_count == 0
            and not session.has_active_background_tasks
        )

    async def _note_disconnected_reattach_failure(
        self,
        rec: Any,
        session: "Session",
        *,
        now: float,
        apply_backoff: bool = True,
    ) -> None:
        """Count a failed heartbeat reattach; escalate past threshold (#2998).

        A remote host is "presumed alive" until ATTACH proves otherwise, so a
        silently-dead far side was retried forever. Past
        ``_DISCONNECTED_REATTACH_ESCALATE_AFTER`` consecutive failures, force
        the authoritative liveness check instead -- it prunes the record
        outright if confirmed dead/absent, ending the loop.

        ``apply_backoff=False`` (an unavailable CodeSpace venue, #2378/#2379):
        still counts toward escalation/idle-dormancy but skips arming the
        retry-delay clock -- a cheap read-only check, not a connection
        attempt, must keep polling every pass.
        """
        failures = self._disconnected_reattach_failures.get(rec.session_id, 0) + 1
        self._disconnected_reattach_failures[rec.session_id] = failures
        if failures % _DISCONNECTED_REATTACH_ESCALATE_AFTER == 0:
            log.warning(
                "Session %s failed reattach %d times; forcing an authoritative "
                "liveness check", rec.session_id, failures,
            )
            with contextlib.suppress(Exception):
                await self._recover_remote_host_records(
                    allow_wake=True, session_ids={rec.session_id},
                )
            if self._host_index is not None and self._host_index.get(rec.session_id) is None:
                self._clear_disconnected_reattach_retry_state(rec.session_id)
                return
        if self._can_background_dormant_after_failure(session, failures):
            await self.stop_session(
                rec.session_id,
                cancel_turn=False,
                allow_background_recovery=False,
            )
            log.info(
                "Session %s went dormant after %d failed background recovery "
                "attempt(s) while idle and unwatched",
                rec.session_id,
                failures,
            )
            return
        if not apply_backoff:
            return
        delay = _background_recovery_backoff_seconds(failures)
        self._disconnected_reattach_retry_at[rec.session_id] = now + delay

    async def _reattach_or_note_failure(
        self,
        rec: Any,
        session: "Session",
        *,
        keep: Any,
        codespace_availability: dict[str, bool],
        now: float,
    ) -> bool:
        """Attempt one disconnected-host reattach under the lifecycle lock,
        recording success/failure bookkeeping; returns whether it recovered.

        An unavailable CodeSpace venue counts toward idle dormancy
        (#2378/#2379) but keeps polling every pass, unthrottled by the
        reconnect backoff clock (a cheap read-only check, not a connection
        attempt). Held outside the lock's ``async with`` (non-reentrant) since
        a noted failure can itself call ``stop_session``, which also locks.
        """
        reattach_ok: bool | None = None
        availability_failure = False
        async with session._lifecycle_lock:
            is_codespace = getattr(rec, "boundary", "local") == "codespace"
            if is_codespace and not await self._codespace_host_available(
                rec, session, codespace_availability,
            ):
                reattach_ok = False
                availability_failure = True
            else:
                retry_at = self._disconnected_reattach_retry_at.get(rec.session_id)
                if retry_at is not None and now < retry_at:
                    return False
                reattach_ok = await self._reattach_one(
                    rec, session, new_status=keep,
                    send_resume=getattr(rec, "resume_on_reattach", False),
                )
        if reattach_ok:
            self._remote_recovery_inconclusive.discard(rec.session_id)
            self._remote_recovery_skipped.discard(rec.session_id)
            self._clear_disconnected_reattach_retry_state(rec.session_id)
            return True
        await self._note_disconnected_reattach_failure(
            rec, session, now=now, apply_backoff=not availability_failure,
        )
        return False
