"""Optional, content-free `codespaces.lifecycle.v1` stage records.

A remote-worker launch or connect passes through fixed stages (seed
validation, credential relay, claim, bridge payload, remote embody, bridge
registration, ...).  Each instrumented stage notes one closed record in a
small process-local recorder; after the console command returns, the records
are offered once through the shared consumer-telemetry command route.

Without the local admission file `<install dir>/consumer-telemetry.json` the
run is unchanged: nothing is read beyond that one path, and no receiver
starts.  A run that noted no stage never pays for the lookup.

Records carry only fixed enums and bounded integers.  CodeSpace, machine,
repository and account names, paths, command lines and error text are never
offered (consumer-telemetry-command, `codespaces.lifecycle.v1`).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

SOURCE = "agent-codespaces"
SOURCE_SCHEMA = "codespaces.lifecycle.v1"
MAX_RECORDS = 32

STAGES = frozenset({
    "launch", "seed_validation", "plugin_preparation", "credential_relay",
    "bridge_payload", "remote_embody", "claim", "account_resolution",
    "bridge_registration", "codespace_lookup", "codespace_connect", "ssh",
})

# Credential preflight reason codes (auth_preflight) -> closed relay reasons.
_RELAY_REASONS = {
    "github-credential-unavailable": "credential_fill_failed",
    "github-credential-ambiguous": "credential_fill_failed",
}

_records: list[dict[str, Any]] = []
_open: dict[str, Any] | None = None
_launch: dict[str, Any] | None = None
_last_reason = "other"


def _now() -> float:
    return time.monotonic()


def clock() -> float:
    """A start mark for `note(..., started=...)`."""
    return _now()


def _elapsed(started: float) -> int:
    return max(0, int((_now() - started) * 1000))


def elapsed_ms(started: float) -> int:
    """Milliseconds since a `clock()` mark."""
    return _elapsed(started)


def _append(event: dict[str, Any]) -> None:
    if len(_records) < MAX_RECORDS and event.get("stage") in STAGES:
        _records.append(event)


def _close(outcome: str, reason: str, **extra: Any) -> None:
    global _open, _last_reason
    current, _open = _open, None
    if current is None:
        return
    if outcome != "success":
        _last_reason = reason
    _append({
        "kind": "stage", "stage": current["stage"], "outcome": outcome,
        "reason": reason, "duration_ms": _elapsed(current["started"]),
        "attempt": current["attempt"], **extra,
    })


def begin(stage: str, *, attempt: int = 1) -> None:
    """Open `stage`; a still-open earlier stage closes as a success."""
    global _open
    try:
        _close("success", "none")
        _open = {"stage": stage, "started": _now(), "attempt": max(1, min(attempt, 2**16))}
    except Exception:
        _open = None


def succeed() -> None:
    """Close the open stage, if any, as a success."""
    try:
        _close("success", "none")
    except Exception:
        pass


def fail(reason: str = "other", *, outcome: str = "failure", limit_ms: int | None = None) -> None:
    """Close the open stage, if any, as a failure (or timeout) with `reason`."""
    try:
        extra = {"limit_ms": limit_ms} if type(limit_ms) is int and limit_ms >= 0 else {}
        _close(outcome, reason or "other", **extra)
    except Exception:
        pass


def fail_with_cause(default: str = "other") -> None:
    """Fail the open stage with the cause an inner stage noted, else `default`.

    An enclosing stage (the launch's SSH session, say) that catches a failure
    already recorded by a nested stage keeps that recorded cause instead of
    replacing it with its own generic reason.
    """
    try:
        cause = _open.get("cause") if _open is not None else None
        fail(cause if type(cause) is str else default)
    except Exception:
        pass


def retry(reason: str = "transport_retry") -> None:
    """Record a retry of the open stage and reopen it as the next attempt."""
    try:
        current = _open
        if current is None:
            return
        attempt = current["attempt"]
        _close("retry", reason)
        begin(current["stage"], attempt=attempt + 1)
    except Exception:
        pass


def note(
    stage: str, outcome: str, reason: str, *,
    started: float | None = None, attempt: int = 1, limit_ms: int | None = None,
    duration_ms: int | None = None,
) -> None:
    """Record one closed stage outside the open-stage sequence."""
    try:
        event: dict[str, Any] = {
            "kind": "stage", "stage": stage, "outcome": outcome, "reason": reason,
            "attempt": max(1, min(int(attempt), 2**16)),
        }
        if type(limit_ms) is int and limit_ms >= 0:
            event["limit_ms"] = limit_ms
        if type(duration_ms) is int and duration_ms >= 0:
            event["duration_ms"] = duration_ms
        elif started is not None:
            event["duration_ms"] = _elapsed(started)
        if outcome != "success":
            global _last_reason
            _last_reason = reason
            if _open is not None:
                _open["cause"] = reason
        _append(event)
    except Exception:
        pass


def relay_reason(reason_code: Any) -> str:
    """Closed relay reason for a credential preflight reason code."""
    if type(reason_code) is str:
        return _RELAY_REASONS.get(reason_code.lower().replace("_", "-"), "credential_fill_failed")
    return "credential_fill_failed"


_PREPARATION_MARKERS = (
    ("relay_not_listening", ("credential relay is not ready",)),
    ("clone_auth_failed", (
        "authentication failed", "could not read username", "terminal prompts disabled",
        "permission denied (publickey)",
    )),
    ("marketplace_add_failed", ("marketplace",)),
)


def preparation_reason(exc: BaseException) -> str:
    """Closed plugin-preparation reason; the error text itself is never offered."""
    if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
        return "interrupted"
    if not isinstance(exc, RuntimeError):
        return "other"
    text = str(exc).lower()
    for reason, markers in _PREPARATION_MARKERS:
        if any(marker in text for marker in markers):
            return reason
    if "plugin preparation failed" in text:
        return "plugin_install_failed"
    return "other"


def start_launch() -> None:
    """Mark this run as a launch: one overall `launch` record closes it."""
    global _launch
    if _launch is None:
        _launch = {"started": _now()}


def reset() -> None:
    """Forget every noted record (tests and repeated in-process runs)."""
    global _open, _launch, _last_reason
    _records.clear()
    _open = None
    _launch = None
    _last_reason = "other"


def finish(code: int, *, interrupted: bool = False) -> list[dict[str, Any]]:
    """Close the run: settle the open stage and the launch, return the records."""
    if interrupted or code == 130:
        fail("interrupted")
    elif code == 0:
        succeed()
    else:
        fail_with_cause(_last_reason)
    if _launch is not None:
        if interrupted or code == 130:
            outcome, reason = "failure", "interrupted"
        elif code == 0:
            outcome, reason = "success", "none"
        else:
            outcome, reason = "failure", _last_reason
        _append({
            "kind": "stage", "stage": "launch", "outcome": outcome, "reason": reason,
            "duration_ms": _elapsed(_launch["started"]), "attempt": 1,
        })
    return list(_records)


def _offer(events: list[dict[str, Any]]) -> None:
    """Admit, offer and close within the contract's bounded shutdown; fail open."""
    try:
        from process_telemetry.finite_owner import FiniteOwner

        from .config import RUNTIME_DIR

        owner = FiniteOwner.from_directory(
            RUNTIME_DIR, source=SOURCE, source_schema=SOURCE_SCHEMA,
        )
        if owner.legacy or owner.refusal is not None:
            return
        with owner:
            for event in events:
                owner.emit(event)
    except Exception:
        return


def _exit_code(value: Any) -> int:
    if value is None:
        return 0
    if type(value) is int:
        return value
    return 1


def instrument(main: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap the console `main`; a run that noted no stage offers nothing."""

    def run(*args: Any, **kwargs: Any) -> Any:
        reset()
        code, interrupted = 1, False
        try:
            result = main(*args, **kwargs)
            code = _exit_code(result)
            return result
        except SystemExit as exc:
            code = _exit_code(exc.code)
            raise
        except KeyboardInterrupt:
            interrupted = True
            raise
        finally:
            try:
                events = finish(code, interrupted=interrupted)
                if events:
                    _offer(events)
            except Exception:
                pass
            reset()

    return run
