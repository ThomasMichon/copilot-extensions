"""``suspend`` for :class:`DispatchClient`.

Split out of ``client.py`` to keep that module under its module-size cap
(same pattern as :mod:`agent_dispatch.client_completion_review`) rather than
grow an already-baselined file. Added for Phase 3 of
``efforts/active/agent-dispatch-monitor-and-confirmed-state/README.md``: the
``cooldown_seconds`` override needs a way to distinguish "not passed" (the
server applies its own default cooldown monitor) from an explicit ``None``
(the server suppresses the monitor entirely).
"""

from __future__ import annotations

from typing import Any

#: Sentinel default for ``cooldown_seconds`` -- see the module docstring.
_UNSET: Any = object()


class SuspendClientMixin:
    """``suspend``, mixed into ``DispatchClient``.

    Relies on ``self._unwrap`` and ``self._http`` from the composing class.
    """

    def suspend(
        self,
        task_id: str,
        worker_id: str,
        *,
        reason: str,
        expected_status: str | None = None,
        expected_generation: int | None = None,
        expected_owner_session_id: str | None = None,
        reject_pending_steer: bool = True,
        cooldown_seconds: float | None = _UNSET,
    ) -> dict:
        """Park a ``started`` task as dormant while preserving its owner.

        ``cooldown_seconds`` left unset lets the server apply its own
        default cooldown monitor (per *suspension-requires-a-monitor*);
        pass an explicit ``float`` to override its duration, or ``None`` to
        suppress the monitor entirely (only for a caller that has arranged
        its own, more specific wait).
        """
        payload: dict[str, object] = {
            "worker_id": worker_id,
            "reason": reason,
            "expected_status": expected_status,
            "expected_generation": expected_generation,
            "expected_owner_session_id": expected_owner_session_id,
            "reject_pending_steer": reject_pending_steer,
        }
        if cooldown_seconds is not _UNSET:
            payload["cooldown_seconds"] = cooldown_seconds
        return self._unwrap(self._http.post(f"/tasks/{task_id}/suspend", json=payload))
