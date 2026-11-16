"""Use the serving Bridge's transport without replacing a retained Worktrees writer."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import sys

from . import _peer_launch, install_paths


def execution_prefix(prefix: Sequence[str]) -> list[str]:
    """Adapt only Bridge-owned pinned wrappers; leave their journal bytes intact."""
    from .session_lifecycle_cli import _agent_bridge_owner_root

    args = list(prefix)
    if (
        args[1:6] == ["-I", "-X", "utf8", "-m", "agent_bridge.worktree_owner_launch"]
        and args[7:8] == ["--pinned-runtime"]
    ):
        if (
            len(args) != 11 or args[7] != "--pinned-runtime"
            or args[9] != "--runtime-identity"
        ):
            raise _peer_launch.ContextRefused("retained legacy writer wrapper is incomplete")
        root = Path(args[6])
        if install_paths.normalized_path(root) != install_paths.normalized_path(_agent_bridge_owner_root()):
            raise _peer_launch.ContextRefused("retained writer belongs to another Bridge installation")
        python = Path(sys.executable)
        if python.name.lower() == "pythonw.exe":
            python = python.with_name("python.exe")
        return [str(python), *args[1:]]
    if (
        len(args) >= 6 and args[1:5] == ["-I", "-B", "-X", "utf8"]
        and Path(args[5]).name == "_peer_launch.py"
        and args[6:7] == ["--pinned-runtime"]
    ):
        if (
            len(args) != 14 or args[6] != "--pinned-runtime"
            or args[8] != "--runtime-identity" or args[10] != "agent-bridge"
            or args[13] != "agent-worktrees"
        ):
            raise _peer_launch.ContextRefused("retained same-cell writer wrapper is incomplete")
        root = Path(args[11])
        if install_paths.normalized_path(root) != install_paths.normalized_path(_agent_bridge_owner_root()):
            raise _peer_launch.ContextRefused("retained writer belongs to another Bridge installation")
        command = _peer_launch.launch_prefix("agent-bridge", root, args[12], "agent-worktrees")
        command[6:6] = args[6:10]
        return command
    return args
