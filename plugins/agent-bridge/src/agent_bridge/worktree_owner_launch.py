"""Revalidate legacy owner availability immediately before native execution."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from agent_procutil import no_window_kwargs

from ._peer_launch import ContextRefused
from .session_lifecycle_cli import (
    _agent_worktrees_environment, _legacy_worktrees_owner_root, _legacy_worktrees_runtime_prefix,
)
from ._peer_launch import retained_runtime_prefix


def main() -> int:
    try:
        if len(sys.argv) < 2:
            raise ContextRefused("missing bridge installation root")
        arguments = sys.argv[2:]
        if arguments[:1] == ["--pinned-runtime"]:
            if len(arguments) < 2:
                raise ContextRefused("missing pinned runtime interpreter")
            root = _legacy_worktrees_owner_root(Path(sys.argv[1]))
            prefix = retained_runtime_prefix(root, Path(arguments[1]), "agent_worktrees")
            arguments = arguments[2:]
        else:
            prefix = _legacy_worktrees_runtime_prefix(Path(sys.argv[1]))
        if not prefix:
            raise ContextRefused("completed worktree owner runtime is unavailable")
        command = [*prefix, *arguments]
        environment = _agent_worktrees_environment()
        if os.name != "nt":
            os.execve(command[0], command, environment)
        return subprocess.call(
            command, env=environment,
            stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
            **no_window_kwargs(),
        )
    except (OSError, ValueError, ImportError, ContextRefused) as error:
        print(f"worktree owner launch refused: {error}", file=sys.stderr)
        return 126


if __name__ == "__main__":
    raise SystemExit(main())
