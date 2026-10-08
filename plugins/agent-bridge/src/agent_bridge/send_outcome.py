"""Typed ``agent-bridge send`` outcomes and their refusal exit codes.

Every ``send --json`` result carries one ``outcome``. Accepted outcomes keep the
path's existing keys and exit 0:

- ``delivered``: a prompt started a turn on a bridge-managed session.
- ``queued``: durably queued -- a ``--queue`` prompt behind a busy turn, or a
  live-session message with ``--delivery queue``.
- ``steered`` / ``interrupted``: a live-session message with that delivery.
- ``duplicate``: an identical ``--idempotency-key`` retry; ``message_id`` is the
  original message's, and nothing new was enqueued.

Refusals print ``{"outcome", "target", "retryable", "reason", "error"}`` and
exit with a distinct code, so a caller never parses text:

- ``refused_busy`` (75): the target is running a turn (no ``--force``/``--queue``).
- ``refused_unavailable`` (69): the target isn't a fresh, current live session
  (``not_found``, ``stale``, ``superseded``, ``expected_mismatch``).
- ``refused_conflict`` (65): the idempotency key is bound to a different request.
"""

from __future__ import annotations

import contextlib
import json
import sys
from typing import Any, Callable

SEND_BUSY_EXIT = 75
SEND_UNAVAILABLE_EXIT = 69
SEND_CONFLICT_EXIT = 65

_EXIT_CODES = {
    "refused_busy": SEND_BUSY_EXIT,
    "refused_unavailable": SEND_UNAVAILABLE_EXIT,
    "refused_conflict": SEND_CONFLICT_EXIT,
}
_LIVE_DELIVERY_OUTCOMES = {"queue": "queued", "steer": "steered", "interrupt": "interrupted"}

#: The real stdout while a ``--json`` send runs (progress text goes to stderr).
_json_stdout: Any = None


class SendRefused(Exception):
    """A send the bridge refused; carries its typed outcome."""

    def __init__(self, outcome: str, *, reason: str, error: str, retryable: bool,
                 target: str | None = None) -> None:
        super().__init__(error)
        self.outcome = outcome
        self.reason = reason
        self.error = error
        self.retryable = retryable
        self.target = target

    @property
    def exit_code(self) -> int:
        return _EXIT_CODES[self.outcome]

    def payload(self) -> dict[str, Any]:
        return {"outcome": self.outcome, "target": self.target, "retryable": self.retryable,
                "reason": self.reason, "error": self.error}


def live_outcome(result: dict[str, Any], delivery: str) -> str:
    if result.get("duplicate"):
        return "duplicate"
    return _LIVE_DELIVERY_OUTCOMES.get(delivery, "queued")


def prompt_outcome(result: dict[str, Any]) -> str:
    return "queued" if result.get("queued") else "delivered"


def refusal_from_live_error(exc: Any, target: str) -> SendRefused | None:
    """Map the live-message route's 404/409 refusals; ``None`` for anything else."""
    status, detail = getattr(exc, "status", None), str(getattr(exc, "detail", "") or exc)
    if status == 404:
        return SendRefused("refused_unavailable", reason="not_found", error=detail,
                           retryable=False, target=target)
    if status != 409:
        return None
    if "idempotency key" in detail:
        return SendRefused("refused_conflict", reason="idempotency_conflict", error=detail,
                           retryable=False, target=target)
    if "superseded" in detail:
        return SendRefused("refused_unavailable", reason="superseded", error=detail,
                           retryable=True, target=target)
    if "no longer fresh" in detail:
        return SendRefused("refused_unavailable", reason="stale", error=detail,
                           retryable=True, target=target)
    if "expected live session" in detail:
        return SendRefused("refused_unavailable", reason="expected_mismatch", error=detail,
                           retryable=False, target=target)
    return None


def emit(payload: dict[str, Any]) -> None:
    """Print one ``send --json`` result to the real stdout."""
    print(json.dumps(payload, indent=2, default=str), file=_json_stdout or sys.stdout)


def run_send(args: Any, send: Callable[[Any], None]) -> None:
    """Run ``send`` and turn a refusal into its exit code (and, with ``--json``,
    its typed payload). Under ``--json`` progress text goes to stderr, so stdout
    is exactly one JSON document."""
    global _json_stdout
    as_json = bool(getattr(args, "json", False))
    target = getattr(args, "target", None)
    previous = _json_stdout
    if as_json:
        _json_stdout = sys.stdout
    try:
        with contextlib.redirect_stdout(sys.stderr) if as_json else contextlib.nullcontext():
            send(args)
    except SendRefused as refused:
        refused.target = refused.target or target
        print(f"[FAIL] {refused.error}", file=sys.stderr)
        if as_json:
            emit(refused.payload())
        sys.exit(refused.exit_code)
    except SystemExit as exc:
        if as_json and exc.code == SEND_BUSY_EXIT:
            emit(SendRefused("refused_busy", reason="busy", retryable=True, target=target,
                             error=f"target {target!r} is running a turn").payload())
        raise
    finally:
        _json_stdout = previous
