"""Fail-open bridge from ``update`` to the shared ``update_telemetry`` library.

The update keeps its own status strings; this module classifies them into the
shared outcome vocabulary, records them as target events, and maps the final
result onto the shared exit-code table. Without the library (or on any
telemetry error) every function degrades to a no-op that preserves the legacy
return value.
"""

from __future__ import annotations

import re
import time

try:
    import update_telemetry as _ut
except ImportError:  # pragma: no cover - a runtime built before the library existed
    _ut = None

_run = None


def start(proc: str = "engine") -> None:
    """Create (owner) or join (participant) the update run for this process."""
    global _run
    if _ut is not None:
        _run = _ut.Run.from_env_or_new(proc)


def enter(phase: str) -> None:
    if _run is not None:
        _run.enter(phase)


def classify_status(status: str) -> tuple[str, str | None]:
    """Map an update status string to ``(outcome, error_code)``."""
    text = str(status).strip()
    low = text.lower()
    if text.startswith("OK"):
        return _ut.VERIFIED_CURRENT, None
    if text.startswith("SKIPPED (current)"):
        return _ut.VERIFIED_CURRENT, None
    if text.startswith("SKIPPED") or text == "payload-only":
        return _ut.SKIPPED, None
    if "timed out" in low:
        return _ut.TIMEOUT, "timeout"
    exited = re.search(r"exit(?:ed)?(?: with code)? (-?\d+)", low)
    if exited:
        return _ut.outcome_for_installer_exit(int(exited.group(1)))
    if "input/output error" in low or "i/o error" in low:
        return _ut.COMMAND_FAILED, _ut.IO_ERROR
    if any(k in low for k in ("os error 33", "winerror 33", "locked a portion of the file")):
        return _ut.BLOCKED_BY_LOCK, "lock_violation"
    if any(k in low for k in ("os error 32", "winerror 32", "used by another process")):
        return _ut.BLOCKED_BY_LOCK, _ut.SHARING_VIOLATION
    if any(k in low for k in ("os error 5", "access is denied", "permission denied")):
        return _ut.COMMAND_FAILED, "permission_denied"
    if any(k in low for k in ("not found", "context invalid", "unsupported", "deadline")):
        return _ut.CONFIGURATION_LIMITATION, "configuration"
    return _ut.COMMAND_FAILED, None


def record(phase: str, component: str, status: str, *, required: bool = True,
           changed: bool | None = None, started: float | None = None) -> None:
    """Record one target's status string as a target event."""
    if _run is None:
        return
    try:
        outcome, code = classify_status(status)
        duration = int((time.monotonic() - started) * 1000) if started is not None else None
        detail = None if status in ("OK", "SKIPPED (current)") else status
        error = {"code": code, "message": status} if code else None
        _run.target(phase, component, outcome, required=required, changed=changed,
                    detail=detail, duration_ms=duration, error=error)
    except Exception:  # telemetry never affects the update
        pass


def finish(rc: int | None, exc: BaseException | None = None) -> int | None:
    """Close the run and map the legacy return code onto the shared table.

    ``rc`` is 0 (success), 1 (failure) or a runtime installer's own exit code.
    With ``exc`` the run is closed as failed and ``rc`` is returned unchanged
    (the caller re-raises).
    """
    global _run
    run, _run = _run, None
    if run is None:
        return rc
    try:
        if exc is not None:
            outcome, error = _ut.classify_exception(exc)
            run.finish(outcome, error=error)
            return rc
        if rc == 0:
            outcome, code = _ut.VERIFIED_CURRENT, None
        elif rc == 1:
            # A generic failure: a more specific recorded target outcome wins.
            outcome = None if _ut.is_failure(run.outcome) else _ut.COMMAND_FAILED
            code = None
        else:
            outcome, code = _ut.outcome_for_installer_exit(int(rc or 1))
        return run.finish(outcome, error={"code": code} if code else None)
    except Exception:
        return rc
