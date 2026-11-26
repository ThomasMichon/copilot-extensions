"""Optional, content-free `containers.lifecycle.v1` stage records.

A remote-worker launch or attach through a container passes through fixed
stages (container lookup, seed validation, claim, credential relay, launch
payload, plugin preparation, exec, remote embody, bridge registration, ...).
Each instrumented stage notes one closed record in a small process-local
recorder; after the console command returns, the records are offered once
through the shared consumer-telemetry command route.

Without the local admission file `<install dir>/consumer-telemetry.json` the
run is unchanged: nothing is read beyond that one path, and no receiver
starts.  A run that noted no stage never pays for the lookup.

Records carry only fixed enums and bounded integers.  Container, fleet,
image, repository and account names, container ids, paths, hosts, command
lines and error text are never offered (consumer-telemetry-command,
`containers.lifecycle.v1`).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

SOURCE = "agent-containers"
SOURCE_SCHEMA = "containers.lifecycle.v1"
MAX_RECORDS = 32

# The stages this plugin has a real phase for; the shared registry also
# admits `container_start`, which no launch or attach path performs.
STAGES = frozenset({
    "launch", "seed_validation", "plugin_preparation", "credential_relay",
    "bridge_payload", "remote_embody", "claim", "account_resolution",
    "bridge_registration", "container_lookup", "container_connect", "exec",
})

# OpenSSH reserves exit status 255 for its own connection/transport errors.
SSH_TRANSPORT_EXIT = 255

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


def is_open(stage: str) -> bool:
    """Whether `stage` is the currently open stage."""
    return _open is not None and _open.get("stage") == stage


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
        _append(event)
    except Exception:
        pass


def _interrupted(exc: BaseException) -> bool:
    return isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError))


_ENGINE_MARKERS = (
    "docker cli not found", "daemon not reachable", "docker daemon is running",
    "runtime driver for",
)


def lookup_reason(exc: BaseException) -> str:
    """Closed container-lookup reason; the error text itself is never offered."""
    if _interrupted(exc):
        return "interrupted"
    text = str(exc).lower()
    if "container lookup failed" in text:
        if any(marker in text for marker in _ENGINE_MARKERS):
            return "engine_unavailable"
        return "listing_failed"
    if "not a discovered fleet member" in text or "returned no container" in text:
        return "not_found"
    if "is not ready (state=" in text:
        return "not_running"
    if any(marker in text for marker in _ENGINE_MARKERS):
        return "engine_unavailable"
    return "other"


def seed_reason(exc: BaseException) -> str:
    """Closed seed-validation reason."""
    if _interrupted(exc):
        return "interrupted"
    return "seed_too_long" if "(max" in str(exc) else "seed_invalid"


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
    if _interrupted(exc):
        return "interrupted"
    if not isinstance(exc, RuntimeError):
        return "other"
    text = str(exc).lower()
    for reason, markers in _PREPARATION_MARKERS:
        if any(marker in text for marker in markers):
            return reason
    if "plugin preparation failed" in text or "could not stage required plugin preparation" in text:
        return "plugin_install_failed"
    return "other"


def embody_reason(stdout: str, stderr: str) -> str:
    """Closed remote-embody failure reason; the text itself is never offered."""
    text = f"{stderr}\n{stdout}"
    if "agent-worktrees: command not found" in text or "owning installer is unavailable" in text:
        return "installer_unavailable"
    if "python3: command not found" in text or "No such file or directory: 'python3'" in text:
        return "bootstrap_python_unavailable"
    return "other"


def registration_reason(error: Any) -> str:
    """Closed bridge-registration failure reason for a launch payload error."""
    text = error if type(error) is str else ""
    if "resumed native session never registered" in text:
        return "session_unregistered"
    if "never registered with the host bridge" in text:
        return "session_unrepresented"
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
        fail("other")
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
            except BaseException:
                # Delivery never replaces the command's own result or exception,
                # including an interrupt or cancellation raised while offering.
                pass
            reset()

    return run
