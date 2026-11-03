"""Manager controls for the existing default-installation bootstrap KillSwitch."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from .manager_config import ManagerConfigError
from .maintenance_policy import _revision, update_refusal

CONTROL_ID = "default-automatic-updates"


@dataclass(frozen=True)
class BootstrapPolicy:
    path: Path | None
    paths: tuple[Path, ...]
    revision: str
    file_revisions: tuple[str, ...]
    enabled: bool | None
    error: str = ""
    detail: str = ""
    scope: Literal["default-installation", "independent-cell"] = "default-installation"


def _paths() -> tuple[Path, ...]:
    override = os.environ.get("BOOTSTRAP_KILLSWITCH_STATE_FILE")
    if override:
        paths = (Path(override).expanduser().absolute(),)
    else:
        home = Path.home() / ".copilot-extensions"
        paths = home / "killSwitches/bootstrap-killswitch.json", home / "bootstrap-killswitch.json"
    return tuple(path.parent.resolve() / path.name for path in paths)


def _read(path: Path) -> bytes | None:
    if path.is_symlink():
        raise ManagerConfigError(f"Automatic-update switch is linked: {path}")
    try:
        with path.open("rb") as stream:
            data = stream.read(65537)
    except FileNotFoundError:
        return None
    if len(data) > 65536:
        raise ManagerConfigError(f"Automatic-update switch exceeds its size bound: {path}")
    return data


def inspect_policy() -> BootstrapPolicy:
    """Keep default-installation scope distinct from unrelated namespaced cells."""
    try:
        paths = _paths()
    except (OSError, RuntimeError, ValueError) as error:
        return BootstrapPolicy(None, (), "", (), None, str(error))
    if os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip():
        return BootstrapPolicy(
            paths[0], paths, "", (), None,
            "Not applicable: this default-installation KillSwitch does not control an independent cell.",
            scope="independent-cell",
        )
    try:
        contents = tuple(_read(path) for path in paths)
        revisions = tuple(_revision(path, data) for path, data in zip(paths, contents))
        revision = hashlib.sha256("\n".join(revisions).encode("ascii")).hexdigest()
        selected = next((index for index, data in enumerate(contents) if data is not None), 0)
        data = contents[selected]
        state = {"active": False} if data is None else json.loads(data.decode("utf-8-sig"))
        if not isinstance(state, dict) or type(state.get("active")) is not bool:
            raise ManagerConfigError("Automatic-update switch requires a boolean active field")
        return BootstrapPolicy(
            paths[selected], paths, revision, revisions, not state["active"],
        )
    except (OSError, UnicodeError, ValueError, ManagerConfigError) as error:
        return BootstrapPolicy(paths[0], paths, "", (), None, str(error))


def _guard_command() -> list[str]:
    from .self_install import running_payload_dir

    scripts = running_payload_dir() / "scripts"
    if os.name == "nt":
        host = shutil.which("pwsh") or shutil.which("powershell")
        script = scripts / "bootstrap-killswitch-guard.ps1"
        command = [host, "-NoProfile", "-NonInteractive", "-File", str(script)] if host else []
    else:
        host = shutil.which("bash")
        script = scripts / "bootstrap-killswitch-guard.sh"
        command = [host, str(script)] if host else []
    if not command or not script.is_file():
        raise ManagerConfigError("Canonical automatic-update controller unavailable; update Manager and refresh")
    return command


def set_enabled(enabled: bool, *, expected: str) -> BootstrapPolicy:
    """Invoke the existing controller for the explicitly confirmed state paths."""
    from agent_procutil.owned_process import run_caller_owned

    if type(enabled) is not bool or re.fullmatch(r"[a-f0-9]{64}", expected) is None:
        raise ManagerConfigError("A boolean and current automatic-update revision are required")
    state = inspect_policy()
    if state.error or state.revision != expected:
        raise ManagerConfigError(state.error or "Automatic-update switch changed; refresh before applying")
    command = _guard_command()
    targets = state.paths if enabled else state.paths[:1]
    deadline = time.monotonic() + 15
    capability = run_caller_owned(
        [*command, "capabilities"], timeout=15,
        max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    try:
        support = json.loads(capability.stdout) if capability.returncode == 0 else None
    except ValueError:
        support = None
    if (
        not isinstance(support, dict)
        or support.get("schema") != "copilot-extensions.bootstrap-killswitch-controller"
        or type(support.get("version")) is not int
        or support.get("version") != 1
        or not isinstance(support.get("capabilities"), list)
        or "conditional-mutation-v1" not in support["capabilities"]
    ):
        raise ManagerConfigError(
            "Canonical controller does not declare conditional mutation support; "
            "update Manager and refresh. No switch mutation was requested. "
            + capability.stderr.strip(),
        )
    output = []
    for path in targets:
        index = state.paths.index(path)
        if _revision(path, _read(path)) != state.file_revisions[index]:
            raise ManagerConfigError("Automatic-update switch changed during application; refresh actual state")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ManagerConfigError("Automatic-update control deadline expired; refresh actual state before retrying")
        arguments = ["off"] if enabled else [
            "on", "Disabled in Manager Configuration / Installation & Updates",
        ]
        flag = "-ExpectedState" if Path(command[-1]).suffix == ".ps1" else "--expected-state"
        arguments.extend([flag, state.file_revisions[index]])
        result = run_caller_owned(
            [*command, *arguments], timeout=remaining,
            env={**os.environ, "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(path)},
            max_stdout_bytes=65536, max_stderr_bytes=65536,
        )
        output.extend(part.strip() for part in (result.stdout, result.stderr) if part)
        if result.returncode:
            raise ManagerConfigError(
                f"Automatic-update controller exited {result.returncode}; "
                + "\n".join(output) + "; refresh actual state before retrying",
            )
    saved = inspect_policy()
    if saved.error or saved.enabled is not enabled or saved.paths != state.paths:
        raise ManagerConfigError(saved.error or "Automatic-update state changed; refresh before retrying")
    return replace(saved, detail="\n".join(output))


def configuration_row() -> dict[str, object]:
    state = inspect_policy()
    reason = state.error or update_refusal(["--auto"])
    limitation = ""
    if state.enabled is not None:
        try:
            _guard_command()
        except (ManagerConfigError, OSError, ValueError) as error:
            limitation = str(error)
    enabled = "not applicable" if state.scope == "independent-cell" else (
        "unknown" if state.enabled is None else "ON" if state.enabled else "OFF"
    )
    paths = "\n".join(str(path) for path in state.paths)
    body = (
        f"Default-installation automatic software updates: {enabled}\n\n"
        "This is the existing shared bootstrap KillSwitch, not a second updater. "
        "Allowing updates clears only its named central/legacy state files; it does not "
        "enable Manager's separate launch-time maintenance veto, clear another hold, "
        "start an update, enroll a recurring worker or stop already-running work.\n\n"
        f"Affected switch paths:\n{paths}\n\n"
        + ("Effective automatic admission: " + reason if reason else "Automatic update policy permits submission.")
        + ("\n\nController unavailable: " + limitation if limitation else "")
    )
    return {
        "id": CONTROL_ID, "title": "Automatic software updates", "kind": "default-installation policy",
        "enabled": enabled, "installed": True, "payload": "shared KillSwitch",
        "selected": enabled, "running": "not a worker", "source": str(state.path) if state.path else "unresolved",
        "updatable": False, "limitation": limitation,
        "activity": "unknown" if state.enabled is None or limitation else "blocked" if reason else "allowed",
        "bootstrap_control": state.enabled is not None and not limitation, "bootstrap_enabled": state.enabled,
        "policy_revision": state.revision, "policy_paths": paths,
        "card": {"title": "Automatic software updates", "status": enabled, "body": body},
    }
