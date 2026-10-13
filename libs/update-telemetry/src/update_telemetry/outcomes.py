"""Outcome vocabulary, severities, exit codes and classifiers for update runs."""

from __future__ import annotations

import errno
import subprocess
from collections.abc import Iterable

from .redact import redact

VERIFIED_CURRENT = "verified_current"
REQUIRES_RESTART = "requires_restart"
IN_PROGRESS = "in_progress"
CONFIGURATION_LIMITATION = "configuration_limitation"
BLOCKED_BY_LOCK = "blocked_by_lock"
AUTH_REQUIRED = "auth_required"
TIMEOUT = "timeout"
COMMAND_FAILED = "command_failed"
SKIPPED = "skipped"

# Higher is worse. ``skipped`` has no severity: it never affects a fold.
SEVERITY = {
    VERIFIED_CURRENT: 0,
    REQUIRES_RESTART: 1,
    IN_PROGRESS: 2,
    CONFIGURATION_LIMITATION: 3,
    BLOCKED_BY_LOCK: 4,
    AUTH_REQUIRED: 5,
    TIMEOUT: 6,
    COMMAND_FAILED: 7,
}
OUTCOMES = (*SEVERITY, SKIPPED)

# Stable ``error.code`` tokens with a meaning beyond the outcome itself.
INTERRUPTED = "interrupted"
USAGE = "usage"
LOCK_TIMEOUT = "lock_timeout"
SHARING_VIOLATION = "sharing_violation"
PERMISSION_DENIED = "permission_denied"
IO_ERROR = "io_error"

# Windows ERROR_SHARING_VIOLATION / ERROR_LOCK_VIOLATION: another process holds
# the file. Plain access denial (winerror 5, EACCES, EPERM) is not contention.
_WINERROR_CONTENTION = {32: SHARING_VIOLATION, 33: "lock_violation"}


class LockTimeout(TimeoutError):
    """An explicit lock could not be acquired before its deadline."""

EXIT_USAGE = 2
EXIT_INTERRUPTED = 130
EXIT_CODES = {
    VERIFIED_CURRENT: 0,
    REQUIRES_RESTART: 0,
    COMMAND_FAILED: 1,
    CONFIGURATION_LIMITATION: 3,
    AUTH_REQUIRED: 4,
    BLOCKED_BY_LOCK: 5,
    TIMEOUT: 6,
}

# Installer exit codes with a defined cutover meaning.
INSTALLER_CUTOVER_NOT_COMPLETED = 75
INSTALLER_CUTOVER_DEGRADED = 76
INSTALLER_WATCHDOG_TIMEOUT = 124


def is_failure(outcome: str | None) -> bool:
    return SEVERITY.get(outcome or "", 0) > SEVERITY[REQUIRES_RESTART]


def worst(outcomes: Iterable[str | None]) -> str:
    """The most severe known outcome, ignoring ``skipped``/unknown/None."""
    known = [o for o in outcomes if o in SEVERITY]
    return max(known, key=SEVERITY.__getitem__) if known else VERIFIED_CURRENT


def exit_code_for(outcome: str | None, error_code: str | None = None) -> int:
    """Map a final outcome to the process exit code; success is always 0."""
    if error_code == INTERRUPTED:
        return EXIT_INTERRUPTED
    if error_code == USAGE:
        return EXIT_USAGE
    return EXIT_CODES.get(outcome or "", 1)


def outcome_for_exit_code(code: int) -> tuple[str, str | None]:
    """Map a child orchestrator's exit code (the same table) back to an outcome."""
    if code == EXIT_INTERRUPTED:
        return COMMAND_FAILED, INTERRUPTED
    if code == EXIT_USAGE:
        return COMMAND_FAILED, USAGE
    if code == 0:
        return VERIFIED_CURRENT, None
    for outcome, value in EXIT_CODES.items():
        if value == code:
            return outcome, None
    return COMMAND_FAILED, f"exit_{code}"


def outcome_for_installer_exit(code: int) -> tuple[str, str | None]:
    """Map a runtime installer's exit code to an outcome and error token."""
    if code == 0:
        return VERIFIED_CURRENT, None
    if code == INSTALLER_CUTOVER_NOT_COMPLETED:
        return BLOCKED_BY_LOCK, "installer_exit_75"
    if code == INSTALLER_CUTOVER_DEGRADED:
        return COMMAND_FAILED, "installer_exit_76"
    if code == INSTALLER_WATCHDOG_TIMEOUT:
        return TIMEOUT, "installer_exit_124"
    if code == EXIT_INTERRUPTED:
        return COMMAND_FAILED, INTERRUPTED
    return COMMAND_FAILED, f"exit_{code}"


def classify_exception(exc: BaseException, *, op: str | None = None) -> tuple[str, dict]:
    """Map an exception raised inside a phase to ``(outcome, error)``."""
    error: dict = {"message": redact(str(exc))[:2048]}
    if op:
        error["op"] = op
    if isinstance(exc, subprocess.TimeoutExpired):
        error["code"] = TIMEOUT
        return TIMEOUT, error
    if isinstance(exc, KeyboardInterrupt):
        error["code"] = INTERRUPTED
        return COMMAND_FAILED, error
    if isinstance(exc, LockTimeout):
        error["code"] = LOCK_TIMEOUT
        return BLOCKED_BY_LOCK, error
    if isinstance(exc, OSError):
        winerror = getattr(exc, "winerror", None)
        if exc.errno is not None:
            error["os_errno"] = exc.errno
        if winerror is not None:
            error["winerror"] = winerror
        if exc.filename is not None:
            error["path"] = str(exc.filename)
        if winerror in _WINERROR_CONTENTION:
            error["code"] = _WINERROR_CONTENTION[winerror]
            return BLOCKED_BY_LOCK, error
        if (isinstance(exc, PermissionError) or winerror == 5
                or exc.errno in (errno.EACCES, errno.EPERM)):
            error["code"] = PERMISSION_DENIED
        elif exc.errno == errno.EIO and winerror is None:
            error["code"] = IO_ERROR
        else:
            error["code"] = f"os_error_{winerror or exc.errno}"
        return COMMAND_FAILED, error
    error["code"] = type(exc).__name__
    return COMMAND_FAILED, error


_FIXES = {
    TIMEOUT: "Re-run the update; if one installer keeps timing out, raise its "
    "<NAME>_INSTALL_DEADLINE_SEC (or COPILOT_PLUGIN_INSTALL_DEADLINE_SEC).",
    BLOCKED_BY_LOCK: "Another process holds a lock or the file in use; wait for it or "
    "close it, then re-run the update.",
    CONFIGURATION_LIMITATION: "Fix the reported configuration problem, then re-run "
    "the update.",
    AUTH_REQUIRED: "Sign in to the failing source, then re-run the update.",
}


_CODE_FIXES = {
    PERMISSION_DENIED: "Check the ownership and permissions of the reported path, "
    "then re-run the update.",
    IO_ERROR: "Check the disk or filesystem holding the reported path for I/O errors, "
    "then re-run the update.",
}


def fix_for(outcome: str, events_path: str | None = None,
            error_code: str | None = None) -> str:
    """One actionable line for a failed run's ``last_failure.fix``."""
    if error_code in _CODE_FIXES:
        return _CODE_FIXES[error_code]
    if outcome in _FIXES:
        return _FIXES[outcome]
    where = f" ({events_path})" if events_path else ""
    return f"Inspect the run's events{where}, fix the failing step, then re-run the update."
