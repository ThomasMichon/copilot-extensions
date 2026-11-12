"""Final normalized Copilot handoff when no worktree launch record exists."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from agent_procutil import no_window_kwargs

from . import agent_policy, config, launch_execution
from .launch_seed_exec import _interactive_console
from .launch_seed_native import _headed_command
from .process_launcher import bind_execution_directory


def launch(command: list[str]) -> int:
    selected = _headed_command(command)
    if selected is None:
        if os.name != "nt":
            os.execvp(command[0], command)
        # headless-guard: allow an interactive backend to inherit its caller's pane
        process = subprocess.Popen(
            command, **({} if _interactive_console() else no_window_kwargs()),
        )
        while True:
            try:
                return process.wait()
            except KeyboardInterrupt:
                continue

    from agent_remote_driver.headed.caller import execute

    cwd = str(Path.cwd().resolve())
    agent_policy.require(cwd)
    argv, session_id = selected
    argv = bind_execution_directory(argv, cwd, ValueError)
    attempt = config.install_dir() / "launch-attempts" / uuid4().hex
    environment = os.environ.copy()
    environment.pop(launch_execution.NATIVE_HINT_ENV, None)
    code, certain = execute(
        argv, session_id=session_id, attempt_directory=attempt,
        state_file=attempt / "headed.json", cwd=cwd, environment=environment,
        admit=lambda: agent_policy.require(cwd),
        # The core validates its native receipt; there is no worktree record to update.
        spawned=lambda _pid, _birth: None,
        interactive=_interactive_console(),
    )
    if not certain:
        print(
            "Headed native receipt is unconfirmed; the original command was not replayed.",
            file=sys.stderr,
        )
    return code if code or certain else 3


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] != "--":
        print("Usage: normalized_backend -- <copilot> [arguments]", file=sys.stderr)
        return 2
    try:
        return launch(sys.argv[2:])
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Could not run normalized Copilot backend: {exc}; no fallback was started.", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
