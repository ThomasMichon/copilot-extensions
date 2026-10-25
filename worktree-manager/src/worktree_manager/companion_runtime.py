"""Installation-owned launch recipe for same-window Companion actions."""

from __future__ import annotations

import base64
import os
import shlex
import sys
from pathlib import Path

from .mux_daemon_process import scrub_session_credentials

_CLEAR_ENVIRONMENT = (
    "GH_TOKEN", "GITHUB_TOKEN", "AGENT_WORKTREES_AHP_AUTH_TOKEN",
    "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__",
)


def companion_command(arguments: list[str]) -> tuple[list[str], dict[str, str]]:
    """Build a foreground invocation pinned to this constructed runtime."""
    prefix = Path(sys.prefix).resolve()
    bootstrap = prefix / ".runtime-bootstrap" / "runtime_launcher.py"
    python = prefix / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if (
        prefix.parent.name != "versions"
        or prefix.name == "dev"
        or not python.is_file()
        or not bootstrap.is_file()
        or not (prefix / ".install-complete.json").is_file()
    ):
        raise RuntimeError("Companion pane requires a constructed Manager runtime")
    if arguments[:2] not in (
        ["-m", "worktree_manager"],
        ["-m", "worktree_manager.mux_companion"],
    ):
        raise ValueError("Companion invocation requires a Manager module")
    env = scrub_session_credentials(dict(os.environ))
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"):
        env.pop(name, None)
    env["WORKTREE_MANAGER_ROOT"] = str(prefix.parent.parent)
    return [str(python), "-I", "-S", "-B", "-X", "utf8", str(bootstrap), *arguments], env


def companion_shell_command(arguments: list[str]) -> str:
    """Encode a mux shell command without relying on native argv joining."""
    command, env = companion_command(arguments)
    root = env["WORKTREE_MANAGER_ROOT"]
    if os.name != "nt":
        unset = [part for name in _CLEAR_ENVIRONMENT for part in ("-u", name)]
        return shlex.join(["env", *unset, f"WORKTREE_MANAGER_ROOT={root}", *command])

    def quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    script = "$ErrorActionPreference='Stop'; "
    script += "; ".join(f"$env:{name}=$null" for name in _CLEAR_ENVIRONMENT) + "; "
    script += "$env:WORKTREE_MANAGER_ROOT=" + quote(root) + "; "
    script += "& " + " ".join(quote(argument) for argument in command) + "; exit $LASTEXITCODE"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return "pwsh -NoLogo -NoProfile -EncodedCommand " + encoded
