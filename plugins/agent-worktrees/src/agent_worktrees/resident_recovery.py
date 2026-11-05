"""Local maintenance admission for ordinary resident wake, without repair."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from . import config as cfg


class ResidentRecoveryHeld(RuntimeError):
    """Maintenance or unreadable maintenance evidence prevents automatic wake."""


def refuse_recovery(error: ResidentRecoveryHeld, *, json_output: bool) -> int:
    """Use the ordinary CLI refusal envelope without reporting an ambiguous write."""
    from . import output

    if json_output:
        return output._json_error(str(error))
    output.err(str(error))
    return 1


def dispatch_claim(verb: str, verb_args: dict) -> dict:
    """Use the existing versioned normal claim writer and resident ensure policy."""
    from . import locks, status_monitor_runtime as monitor, tracking_write

    return tracking_write.dispatch(
        verb, verb_args,
        read_lock_data=lambda: locks.read_lock(monitor._monitor_lock_path()),
        ensure_monitor=monitor._ensure_status_monitor if monitor._status_monitor_enabled() else None,
        min_version=4,
    )


def _user_profile() -> Path:
    from . import _installation_context as installation_context

    try:
        return installation_context._current_environment(
            environment=os.environ, os_profile=None, platform=None, wsl_distro=None,
        )[1]
    except (ValueError, OSError) as error:
        raise ResidentRecoveryHeld("Canonical maintenance profile is unavailable") from error


def require_wake_allowed() -> None:
    """Read existing maintenance signals without starting any runtime or service."""
    roots = (_user_profile() / ".copilot-extensions", cfg.install_dir())
    for root in dict.fromkeys(roots):
        marker = root / "maintenance"
        notice = root / "maintenance.json"
        try:
            marker.lstat()
        except FileNotFoundError:
            pass
        except OSError as error:
            raise ResidentRecoveryHeld("Resident maintenance flag is unreadable") from error
        else:
            raise ResidentRecoveryHeld("Resident wake is held by an installation maintenance flag")
        try:
            if not stat.S_ISREG(notice.lstat().st_mode):
                raise ResidentRecoveryHeld("Resident maintenance notice is not a regular file; wake is unqualified")
            with notice.open("rb") as stream:
                raw = stream.read(16385)
        except FileNotFoundError:
            continue
        except OSError as error:
            raise ResidentRecoveryHeld("Resident maintenance notice is unreadable") from error
        try:
            if len(raw) > 16384:
                raise ValueError("oversized maintenance notice")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("maintenance notice must be an object")
            # Installation sidecars without their marker are not standby notices.
            if value.get("schema") == "copilot-extensions.installation-maintenance":
                continue
            if (
                type(value.get("version")) is not int or value["version"] != 1
                or not isinstance(value.get("status"), str)
                or value.get("status") not in {"active", "complete"}
                or any(not isinstance(value.get(key), str) or not value[key].strip()
                       for key in ("ownerSessionId", "summary", "startedAt"))
            ):
                raise ValueError("invalid maintenance notice")
        except (ValueError, UnicodeError) as error:
            raise ResidentRecoveryHeld("Resident maintenance notice is invalid; wake is unqualified") from error
        if value["status"] == "active":
            raise ResidentRecoveryHeld("Resident wake is held by an active maintenance notice")


def wake_allowed() -> bool:
    """Allow the normal ensure boundary to refuse without attempting repair."""
    try:
        require_wake_allowed()
    except (ResidentRecoveryHeld, OSError):
        return False
    return True
