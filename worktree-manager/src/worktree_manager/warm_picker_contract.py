"""Lightweight identity shared by the resident UI owner and its TTY client."""

from __future__ import annotations

import os
import hashlib
import json
import sys
from pathlib import Path

KIND = "picker-session-v1"
CONTEXT_KEYS = (
    "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT",
    "AGENT_WORKTREES_PAYLOAD_ROOT", "AGENT_HOME",
)
_TRANSPORT_KEYS = {
    "COLUMNS", "LINES", "TERM", "COLORTERM", "PSMUX_SESSION", "TMUX",
    "WT_SESSION", "WT_PROFILE_ID", "_", "SHLVL", "PWD", "OLDPWD", "PROMPT",
    "TMUX_PANE", "PSMUX_TARGET_SESSION",
    "TERM_PROGRAM", "TERM_PROGRAM_VERSION",
    "PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "VIRTUAL_ENV",
    "PYTHONUTF8", "AGENT_WORKTREES_LAUNCH_ID", "AGENT_WORKTREES_BINSTUB_STARTED",
    "UV_INTERNAL__PYTHONHOME", "__PYVENV_LAUNCHER__",
    "WORKTREE_MANAGER_ENGINE_ARGV", "WORKTREE_MANAGER_RESIDENT_BOOTSTRAP",
    "PYTEST_CURRENT_TEST",  # pytest changes its phase marker inside one invocation
}


def _private_key(key: str) -> bool:
    name = key.upper()
    return (
        any(word in name for word in ("TOKEN", "PASSWORD", "SECRET"))
        or any(word in name for word in ("BIND_", "HANDOFF", "UPDATE_AUTH"))
        or name.startswith((
            "COPILOT_AGENT_", "AGENT_WORKTREES_SESSION", "AGENT_WORKTREES_AHP_",
            "AGENT_WORKTREES_UPDATE_", "CE_UPDATE_", "AGENT_DISPATCH_", "AGENT_BRIDGE_",
        ))
    )


def worker_environment(environment: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in environment.items() if not _private_key(key)}


def invocation_context(
    environment: dict[str, str] | None = None, *, manager_root: Path | None = None,
) -> dict[str, str] | None:
    environment = os.environ if environment is None else environment
    if any(value and _private_key(key) for key, value in environment.items()):
        return None
    values = {
        key.upper() if os.name == "nt" else key: value
        for key, value in environment.items() if key.upper() not in _TRANSPORT_KEYS
    }
    selected_root = values.get("WORKTREE_MANAGER_ROOT") or manager_root
    if selected_root is not None:
        if "\0" in str(selected_root):
            return None
        try:
            values["WORKTREE_MANAGER_ROOT"] = os.path.normcase(str(Path(selected_root).expanduser().resolve()))
        except (OSError, RuntimeError, ValueError):
            return None
    digest = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
    return {**{key: environment.get(key, "") for key in CONTEXT_KEYS}, "environment_sha256": digest}


def runtime_identity() -> str:
    return os.path.normcase(str(Path(__file__).resolve().parent))


def mux_arguments(environment: dict[str, str], *, windows: bool = os.name == "nt") -> list[str]:
    if windows:
        return []
    socket = environment.get("TMUX", "").split(",", 1)[0]
    return ["-S", socket] if socket else ["-L", "default"]


def runtime_command(module: str, *arguments: str) -> list[str]:
    prefix = Path(sys.prefix).resolve()
    command = [sys.executable, "-I", "-B", "-X", "utf8"]
    if prefix.parent.name == "versions":
        bootstrap = prefix / ".runtime-bootstrap" / "runtime_launcher.py"
        if not bootstrap.is_file() or not (prefix / ".install-complete.json").is_file():
            raise RuntimeError("warm Picker requires a constructed Manager runtime")
        command += ["-S", str(bootstrap)]
    return [*command, "-m", module, *arguments]
