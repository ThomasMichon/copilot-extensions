"""One final local-integration pass against the update's selected payload."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path

from . import installer, output, picker_profiles_cli, update_events


@contextmanager
def _selected_payload(payload: Path, environment: Mapping[str, str]):
    values = {
        "AGENT_WORKTREES_PAYLOAD_ROOT": str(payload),
        "COPILOT_EXTENSIONS_CONTEXT": environment.get("COPILOT_EXTENSIONS_CONTEXT"),
    }
    previous = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def finish(payload: Path, platform_name: str, environment: Mapping[str, str]) -> bool:
    """Required local outputs cannot be downgraded to advisory skipped work."""
    from .manager_recovery import recover

    update_events.enter("binstubs")
    recovery_started = time.monotonic()
    try:
        # The installer also covers an old running updater during self-upgrade.
        with _selected_payload(payload, environment):
            recovery = recover()
        status = {
            "current": "SKIPPED (current)",
            "not-adopted": "SKIPPED (Manager not adopted)",
            "recovered": "OK (Manager recovered)",
        }.get(recovery["status"])
        if status is None:
            raise ValueError("Manager recovery returned an invalid status")
    except (OSError, ValueError, RuntimeError) as error:
        output.err(f"Adopted Manager recovery failed: {error}")
        update_events.record("binstubs", "manager-recovery", f"failed: {error}", started=recovery_started)
        return False
    update_events.record(
        "binstubs", "manager-recovery", status, started=recovery_started,
    )
    started = time.monotonic()
    current = True
    try:
        with _selected_payload(payload, environment):
            result = installer.reconcile_binstubs()
        if not isinstance(result, dict):
            raise ValueError("Project command reconciliation returned an invalid result")
        blocked = result.get("blocked")
        if blocked is None:
            blocked = sorted(set(result.get("preserved", ())) & set(result.get("registered", ())))
        if blocked:
            raise RuntimeError("Unresolved project command ownership: " + ", ".join(blocked))
    except (OSError, ValueError, RuntimeError) as error:
        current = False
        status = f"project command reconciliation failed: {error}"
        output.err(status)
    else:
        status = "OK"
    update_events.record("binstubs", "project-commands", status, started=started)
    if platform_name == "windows" and recovery["status"] != "not-adopted":
        started = time.monotonic()
        with _selected_payload(payload, environment):
            profiles_current = picker_profiles_cli._refresh_terminal_profiles()
        update_events.record(
            "binstubs", "terminal-profiles", "OK" if profiles_current else "profile refresh failed",
            started=started,
        )
        current = current and profiles_current
    return current
