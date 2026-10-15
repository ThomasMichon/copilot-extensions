"""Fail-open bridge from ``update`` to the shared ``update_telemetry`` library.

The update keeps its own status strings; this module classifies them into the
shared outcome vocabulary, records them as target events, and maps the final
result onto the shared exit-code table. Without the library (or on any
telemetry error) every function degrades to a no-op that preserves the legacy
return value.
"""

from __future__ import annotations

import os
import re
import sys
import time

try:
    import update_telemetry as _ut
except ImportError:  # pragma: no cover - a runtime built before the library existed
    _ut = None

_run = None
_human_phase = None
_human_phase_failed = False


def start(proc: str = "engine") -> None:
    """Create (owner) or join (participant) the update run for this process."""
    global _run
    if _ut is not None:
        _run = _ut.Run.from_env_or_new(proc)


def enter(phase: str) -> None:
    global _human_phase, _human_phase_failed
    from . import output

    _close_human_phase()
    if _run is not None:
        _run.enter(phase)
    if output.current_operation() is not None:
        _human_phase = output.human_operation(phase)
        _human_phase.__enter__()
        _human_phase_failed = False


def _close_human_phase(exc: BaseException | None = None) -> None:
    global _human_phase, _human_phase_failed
    phase, _human_phase = _human_phase, None
    failed, _human_phase_failed = _human_phase_failed, False
    if phase is not None:
        try:
            phase.complete("failed" if exc or failed else "done")
        finally:
            error_info = sys.exc_info()
            if error_info[0] is None and exc is not None:
                error_info = (type(exc), exc, exc.__traceback__)
            phase.__exit__(*error_info)


def redact(text: str) -> str:
    """``text`` with credentials masked; without the library, at least URL
    userinfo and authorization headers."""
    if _ut is not None:
        return _ut.redact(text)
    text = re.sub(r"(?i)(authorization\s*:)[^\r\n]*", r"\1 ***", text)
    return re.sub(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@]+@", r"\1***@", text)


def bounded(text: str, limit: int, *, tail: bool = False) -> str:
    """Redact, then truncate: a cut taken first can leave a credential fragment
    (for example the middle of URL userinfo) that the redactor no longer
    recognizes."""
    text = redact(text)
    return text[-limit:] if tail else text[:limit]


_WINDOWS = os.name == "nt"

# Statuses this update produces itself for a configuration problem, optionally
# behind a stable ``<plugin>: `` prefix (anchored, so arbitrary tool output that
# merely contains "not found" is not reclassified).
_CONFIGURATION = re.compile(
    r"(?:plugin update failed: |[\w.@-]+: )?"
    r"(?:(?:installer|powershell|source dir) not found|--force-runtime target not found"
    r"|installation context invalid|invalid installer deadline|invalid runtimescope"
    r"|machine detection failed|forced namespaced runtime reconciliation is unsupported"
    r"|'copilot' cli not found)"
)
# A runtime or module installer's own exit (not a ``copilot plugin`` command's).
_EXITED = re.compile(r"(?:installer|install) exited (-?\d+)\b")
_TIMED_OUT = re.compile(r"(?:uninstall )?timed out\b")


_WINERROR, _ERRNO, _OSERROR = r"\bwinerror", r"\[errno", r"\bos error"


def _marker(low: str, kind: str, code: int) -> bool:
    """A locale-independent ``[WinError N]`` / ``[Errno N]`` / ``(os error N)`` marker."""
    return re.search(rf"{kind} {code}\b", low) is not None


def _os_error(low: str, *, windows: tuple[int, ...] = (), posix: tuple[int, ...] = ()) -> bool:
    """``os error N`` carries the host's own error numbering (Windows vs errno)."""
    return any(_marker(low, _OSERROR, c) for c in (windows if _WINDOWS else posix))


def classify_status(status: str) -> tuple[str, str | None]:
    """Map an update status string to ``(outcome, error_code)``.

    Error classes are matched on stable numeric markers (``[WinError N]``,
    ``[Errno N]``, ``os error N``) before English phrases, so localized OS
    messages keep their codes.
    """
    text = str(status).strip()
    low = text.lower()
    if text.startswith("OK"):
        return _ut.VERIFIED_CURRENT, None
    if text.startswith("SKIPPED (current)"):
        return _ut.VERIFIED_CURRENT, None
    if text.startswith("SKIPPED") or text == "payload-only":
        return _ut.SKIPPED, None
    if _TIMED_OUT.match(low):
        return _ut.TIMEOUT, "timeout"
    exited = _EXITED.match(low)
    if exited:
        return _ut.outcome_for_installer_exit(int(exited.group(1)))
    if (_marker(low, _ERRNO, 5) or _os_error(low, posix=(5,))
            or "input/output error" in low or "i/o error" in low):
        return _ut.COMMAND_FAILED, _ut.IO_ERROR
    if (_marker(low, _WINERROR, 33) or _os_error(low, windows=(33,))
            or "locked a portion of the file" in low):
        return _ut.BLOCKED_BY_LOCK, "lock_violation"
    if (_marker(low, _WINERROR, 32) or _os_error(low, windows=(32,))
            or "used by another process" in low):
        return _ut.BLOCKED_BY_LOCK, _ut.SHARING_VIOLATION
    if (_marker(low, _WINERROR, 5) or _os_error(low, windows=(5,), posix=(1, 13))
            or _marker(low, _ERRNO, 1) or _marker(low, _ERRNO, 13)
            or "access is denied" in low or "permission denied" in low):
        return _ut.COMMAND_FAILED, _ut.PERMISSION_DENIED
    if _CONFIGURATION.match(low):
        return _ut.CONFIGURATION_LIMITATION, "configuration"
    return _ut.COMMAND_FAILED, None


def record(phase: str, component: str, status: str, *, required: bool = True,
           changed: bool | None = None, started: float | None = None) -> None:
    """Record one target's status string as a target event."""
    global _human_phase_failed
    # These are the update owner's own success/skip statuses, independent of
    # whether the optional telemetry library or durable event store exists.
    text = str(status).strip()
    if required and not (
        text.startswith(("OK", "SKIPPED")) or text == "payload-only"
    ):
        _human_phase_failed = True
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
    _close_human_phase(exc)
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
