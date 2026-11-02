"""Local maintenance control and read-only automatic-update admission."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import manager_config, source_config

CONTROL_ID = "manager-automatic-maintenance"


@dataclass(frozen=True)
class MaintenancePolicy:
    path: Path
    revision: str
    enabled: bool | None
    error: str = ""


def _content(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _revision(path: Path, content: bytes | None) -> str:
    marker = b"absent" if content is None else b"present\0" + content
    return hashlib.sha256(os.fsencode(path.absolute()) + b"\0" + marker).hexdigest()


def inspect_policy(root: Path | None = None) -> MaintenancePolicy:
    """Report invalid policy as unknown, never as an enabled default."""
    path = source_config.config_path(root)
    try:
        content = _content(path)
        enabled = manager_config.load_config(root).maintenance.automatic
        if _content(path) != content:
            raise manager_config.ManagerConfigError("Manager configuration changed; refresh and retry")
        return MaintenancePolicy(path, _revision(path, content), enabled)
    except (OSError, UnicodeError, manager_config.ManagerConfigError) as error:
        return MaintenancePolicy(path, "", None, str(error))


def set_enabled(enabled: bool, *, expected: str, root: Path | None = None) -> MaintenancePolicy:
    """Change only the existing maintenance veto after explicit local confirmation."""
    import tomlkit

    if type(enabled) is not bool or re.fullmatch(r"[a-f0-9]{64}", expected) is None:
        raise manager_config.ManagerConfigError("A boolean and current policy revision are required")
    current = inspect_policy(root)
    if current.error:
        raise manager_config.ManagerConfigError(current.error)
    if current.revision != expected:
        raise manager_config.ManagerConfigError("Manager configuration changed; refresh before changing policy")
    path = current.path
    if path.is_symlink():
        raise manager_config.ManagerConfigError("Manager configuration is linked; edit its owning source instead")
    original = _content(path)
    if _revision(path, original) != expected:
        raise manager_config.ManagerConfigError("Manager configuration changed; refresh before changing policy")
    document = tomlkit.parse(original.decode("utf-8") if original is not None else "")
    if "maintenance" not in document:
        document["maintenance"] = tomlkit.table()
    document["maintenance"]["automatic"] = enabled
    replacement = tomlkit.dumps(document)
    expected_data = source_config.tomllib.loads(original.decode("utf-8") if original is not None else "")
    expected_data.setdefault("maintenance", {})["automatic"] = enabled
    if json.dumps(source_config.tomllib.loads(replacement), sort_keys=True, default=str) != json.dumps(
        expected_data, sort_keys=True, default=str,
    ):
        raise manager_config.ManagerConfigError("Maintenance edit would change unrelated Manager configuration")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent,
            prefix=".maintenance-policy-", suffix=".toml", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(replacement)
            stream.flush()
            os.fsync(stream.fileno())
        if _content(path) != original:
            raise manager_config.ManagerConfigError("Manager configuration changed; refresh before changing policy")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    saved = inspect_policy(root)
    if saved.error or saved.enabled is not enabled or saved.path != path:
        raise manager_config.ManagerConfigError(
            saved.error or "Manager policy changed after saving; refresh to observe the current value"
        )
    return saved


def update_refusal(arguments: list[str], *, root: Path | None = None) -> str | None:
    """Use the execution owner's policy without enqueueing or starting work."""
    if "--auto" not in arguments:
        return None
    from update_driver import RequestSpec
    from update_driver.core import automatic_update_refusal

    context = {"WORKTREE_MANAGER_ROOT": str(root or source_config.default_root())}
    if pointer := os.environ.get("COPILOT_EXTENSIONS_CONTEXT"):
        context["COPILOT_EXTENSIONS_CONTEXT"] = pointer
    return automatic_update_refusal(RequestSpec(
        "manager", tuple(["update", *arguments]), context=tuple(context.items()),
        killswitch_path=os.environ.get("BOOTSTRAP_KILLSWITCH_STATE_FILE", ""),
    ))


def admit_update(arguments: list[str], *, as_json: bool = False) -> bool:
    reason = update_refusal(arguments)
    if reason is None:
        return True
    print(f"Automatic update blocked: {reason}. See Configuration / Installation & Updates.", file=sys.stderr)
    if as_json:
        print(json.dumps({"state": "blocked", "exit_code": 3, "error": reason, "request_id": None}))
    return False


def configuration_row() -> dict[str, object]:
    state = inspect_policy()
    reason = state.error or update_refusal(["--auto"])
    enabled = "unknown" if state.enabled is None else "ON" if state.enabled else "OFF"
    body = (
        f"Manager launch-time automatic maintenance: {enabled}\n\n"
        f"Configuration: {state.path}\n\n"
        "This existing switch also controls automatic housekeeping, repair and monitor startup. "
        "It does not block explicit user/agent updates or bypass installation re-entrancy guards. "
        "Enabling it does not enroll a recurring worker or clear an independent installation hold.\n\n"
        + ("Automatic update admission: " + reason if reason else "Automatic update policy permits submission.")
    )
    return {
        "id": CONTROL_ID, "title": "Launch-time maintenance", "kind": "local Manager policy",
        "enabled": enabled, "installed": True, "payload": "local setting",
        "selected": enabled, "running": "not a worker", "source": str(state.path),
        "updatable": False, "limitation": "", "activity": "unknown" if state.error else "blocked" if reason else "allowed",
        "maintenance_control": state.enabled is not None, "maintenance_enabled": state.enabled,
        "policy_revision": state.revision,
        "card": {"title": "Launch-time maintenance", "status": enabled, "body": body},
    }
