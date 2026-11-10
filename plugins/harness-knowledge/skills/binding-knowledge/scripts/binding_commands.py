"""Native command construction and optional locked-worker read dispatch."""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import shlex
import shutil
import subprocess
from typing import cast


def command(agent_worktrees_path: str, args: list[str]) -> list[str]:
    if os.name == "nt" and Path(agent_worktrees_path).suffix.casefold() == ".ps1":
        host = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
        if not host:
            return []
        return [
            host, "-NoProfile", "-NoLogo", "-ExecutionPolicy", "Bypass",
            "-File", agent_worktrees_path, *args,
        ]
    return [agent_worktrees_path, *args]


def run(
    agent_worktrees_path: str, args: list[str], *, cwd: str = "", noninteractive: bool = False,
    reader: object = None,
    command_builder: Callable[[str, list[str]], list[str]] = command,
) -> subprocess.CompletedProcess[str] | None:
    if not agent_worktrees_path:
        return None
    if callable(reader):
        native_read = cast(Callable[..., subprocess.CompletedProcess[str] | None], reader)
        result = native_read(agent_worktrees_path, args, cwd=cwd, noninteractive=noninteractive)
        if result is not None:
            return result
    argv = command_builder(agent_worktrees_path, args)
    if not argv:
        return None
    try:
        return subprocess.run(
            argv, cwd=cwd or None, capture_output=True, text=True,
            stdin=subprocess.DEVNULL if noninteractive else None, timeout=120,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def registration_argv(
    knowledge: str, knowledge_path: str, *, remote: str, default_branch: str,
    account: str, repo_class: str = "knowledge",
) -> list[str]:
    argv = ["repos", "add", knowledge, knowledge_path, "--class", repo_class]
    for flag, value in (("--remote", remote), ("--default-branch", default_branch), ("--account", account)):
        if value:
            argv.extend([flag, value])
    return argv


def render_command(argv: list[str]) -> str:
    if os.name == "nt":
        return "& " + " ".join("'" + value.replace("'", "''") + "'" for value in argv)
    return shlex.join(argv)
