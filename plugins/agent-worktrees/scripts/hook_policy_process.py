"""Caller-owned resident policy execution; stdin EOF cancels its actual worker."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import threading

_MAX_INPUT = 16 * 1024 * 1024
_MAX_OUTPUT = 64 * 1024


def _watch_caller() -> None:
    try:
        sys.stdin.buffer.read(1)
    finally:
        # Closing the caller-owned SDK's lifetime handles retires its whole scope,
        # even when this interpreter sits behind a Windows venv launcher.
        os._exit(3)


def main() -> int:
    frame = sys.stdin.buffer.readline(_MAX_INPUT + 1)
    if len(frame) > _MAX_INPUT or not frame.endswith(b"\n"):
        return 2
    threading.Thread(target=_watch_caller, name="policy-caller-lifetime", daemon=True).start()
    try:
        from agent_procutil import direct_python
        from agent_procutil.owned_process import (
            OwnedCleanupError,
            require_caller_owned_support,
            run_caller_owned,
        )

        require_caller_owned_support()
    except Exception:
        sys.stderr.write("agent-worktrees resident policy: owned runtime unavailable\n")
        return 3
    try:
        python, overrides = direct_python(sys.executable)
        environment = dict(os.environ)
        environment.update(overrides)
        environment.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        result = run_caller_owned(
            [
                python,
                "-X",
                "utf8",
                "-B",
                str(Path(__file__).with_name("hook_client.py")),
                "preToolUseStrict",
            ],
            timeout=25.0,
            max_stdout_bytes=_MAX_OUTPUT,
            max_stderr_bytes=_MAX_OUTPUT,
            input_text=frame.decode("utf-8"),
            cwd=Path.home(),
            env=environment,
        )
    except OwnedCleanupError as error:
        sys.stderr.write("agent-worktrees resident policy: cleanup unconfirmed\n")
        sys.stderr.flush()
        # Retain exact SDK custody until the caller closes its lifetime channel.
        threading.Event().wait()
        raise error
    except subprocess.TimeoutExpired:
        return 4
    except Exception:
        return 2
    if result.returncode:
        return 2
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    # The daemon reader owns stdin; normal SDK scopes are settled before this reply.
    os._exit(code)
