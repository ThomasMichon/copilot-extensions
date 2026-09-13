"""Pure spawn-conclusion / cleanup-retry decision helpers for
:class:`agent_dispatch.supervisor.Supervisor`.

Extracted from ``supervisor.py`` (Phase 10 componentization,
``efforts/active/review-automation-reliability`` #2423) as the safe first
cut of that module: unlike the bulk of ``Supervisor``'s own large stateful
methods (``reconcile``, ``release_requested_bodies``, and others, which
close over live queue/session state and the module's concurrency
invariants), every function here was already a ``@staticmethod`` or
``@classmethod`` that touched no instance state -- pure classification and
retry-backoff decisions over plain dicts. That is exactly the kind of
state-adjacent pure logic worth testing directly rather than only
indirectly through much larger integration assertions (the same rationale
``spawn_attempt_projection.py`` used for a similar cut in a different leg).

``Supervisor`` re-exposes every name here as a class-level
``staticmethod(...)`` alias (see the bottom of ``supervisor.py``'s import
block) so existing call sites -- both ``self._conclusion_state(...)``
instance calls and ``Supervisor._conclusion_state(...)`` direct
class-attribute access a test uses -- keep working unchanged.
"""

from __future__ import annotations

import json

#: Cleanup-envelope / conclusion-retry states (terminal-worker release and
#: spawn-conclusion tracking share this three-state vocabulary).
_CONCLUSION_PENDING = "pending"
_CONCLUSION_COMPLETE = "complete"
_CONCLUSION_HELD = "held"
#: Conclusion outcomes considered transient (worth a bounded retry) rather
#: than a hard failure.
_TRANSIENT_CONCLUSION_REASONS = frozenset(
    {
        "live-mux",
        "live-session",
        "session-identity-unavailable",
    }
)
#: A retrying component gives up after this many attempts, moving to HELD
#: (preserved, needing an operator/next-cycle nudge) rather than retrying
#: forever.
_CONCLUSION_MAX_ATTEMPTS = 12
_CONCLUSION_RETRY_BASE_SECONDS = 30


def _bounded_cleanup_failure(
    payload: dict,
    *,
    attempts: int,
    now: float,
) -> tuple[str, dict]:
    attempts += 1
    exhausted = attempts >= _CONCLUSION_MAX_ATTEMPTS
    updated = {
        **payload,
        "attempts": attempts,
        "next_attempt_at": (
            0
            if exhausted
            else now
            + min(
                300,
                _CONCLUSION_RETRY_BASE_SECONDS * (2 ** max(0, attempts - 1)),
            )
        ),
    }
    if exhausted:
        updated["action"] = "preserved"
        updated["reason"] = "cleanup-retry-exhausted"
    return (
        _CONCLUSION_HELD if exhausted else _CONCLUSION_PENDING,
        updated,
    )


def _component_retry_meta(component: object) -> tuple[int, float]:
    if not isinstance(component, dict):
        return 0, 0.0
    try:
        return (
            max(0, int(component.get("attempts", 0))),
            max(0.0, float(component.get("next_attempt_at", 0))),
        )
    except (TypeError, ValueError):
        return 0, 0.0


def _bounded_component_failure(
    component: dict,
    *,
    now: float,
) -> tuple[str, dict]:
    attempts, _ = _component_retry_meta(component)
    attempts += 1
    exhausted = attempts >= _CONCLUSION_MAX_ATTEMPTS
    return (
        _CONCLUSION_HELD if exhausted else _CONCLUSION_PENDING,
        {
            **component,
            "state": (_CONCLUSION_HELD if exhausted else _CONCLUSION_PENDING),
            "attempts": attempts,
            "next_attempt_at": (
                0
                if exhausted
                else now
                + min(
                    300,
                    _CONCLUSION_RETRY_BASE_SECONDS * (2 ** max(0, attempts - 1)),
                )
            ),
        },
    )


def _hold_pending_cleanup(payload: dict) -> dict:
    updated = {
        **payload,
        "action": "preserved",
        "reason": "cleanup-retry-exhausted",
        "next_attempt_at": 0,
    }
    for name in ("session_end", "worktree_cleanup"):
        component = updated.get(name)
        if isinstance(component, dict) and component.get("state") == _CONCLUSION_PENDING:
            updated[name] = {
                **component,
                "state": _CONCLUSION_HELD,
            }
    return updated


def _cleanup_envelope_state(payload: dict) -> str:
    states = []
    for name in ("session_end", "worktree_cleanup"):
        component = payload.get(name)
        if isinstance(component, dict):
            states.append(component.get("state"))
    if _CONCLUSION_PENDING in states:
        return _CONCLUSION_PENDING
    if _CONCLUSION_HELD in states:
        return _CONCLUSION_HELD
    return _CONCLUSION_COMPLETE


def _conclusion_state(outcome: dict) -> str:
    action = str(outcome.get("action") or "")
    reason = str(outcome.get("reason") or "")
    if action in {
        "primed",
        "already-primed",
        "removed",
        "already-removed",
    }:
        return _CONCLUSION_COMPLETE
    if action == "failed" or reason in _TRANSIENT_CONCLUSION_REASONS:
        return _CONCLUSION_PENDING
    return _CONCLUSION_HELD


def _append_conclusion_detail(detail: str, outcome: dict | None) -> str:
    if outcome is None:
        return detail
    action = str(outcome.get("action") or "unknown")
    reason = str(outcome.get("reason") or "")
    suffix = f"terminal conclusion {action}"
    if reason:
        suffix += f" ({reason})"
    return f"{detail}; {suffix}"


def _conclusion_retry_payload(reservation: dict) -> dict:
    raw = reservation.get("conclusion_detail")
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    if not payload.get("acp_session_id") and payload.get("session"):
        payload["acp_session_id"] = payload["session"]
    return payload


def _conclusion_retry_meta(reservation: dict) -> tuple[int, float]:
    payload = _conclusion_retry_payload(reservation)
    try:
        attempts = max(0, int(payload.get("attempts", 0)))
        next_at = max(0.0, float(payload.get("next_attempt_at", 0)))
    except (TypeError, ValueError):
        return 0, 0.0
    return attempts, next_at
