"""Caller-owned resident policy execution; stdin EOF cancels its actual worker."""

from __future__ import annotations

import base64
import binascii
import os
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

_MAX_INPUT = 16 * 1024 * 1024
_MAX_OUTPUT = 64 * 1024


def _watch_caller() -> None:
    try:
        sys.stdin.buffer.read(1)
    finally:
        # Closing the caller-owned SDK's lifetime handles retires its whole scope,
        # even when this interpreter sits behind a Windows venv launcher.
        os._exit(3)


def execute(frame: bytes, *, legacy: bool = False, settlement_deadline=None) -> int:
    """Supervise the one strict policy worker before forwarding any decision."""
    settlement_deadline = settlement_deadline or time.monotonic() + 23.0
    try:
        from agent_procutil import direct_python
        from agent_procutil.owned_budget import OwnedExecutionBudget
        from agent_procutil.owned_process import (
            OwnedCleanupError,
            require_caller_owned_support,
            run_caller_owned,
        )

        require_caller_owned_support()
    except Exception:
        sys.stderr.write("agent-worktrees resident policy: owned runtime unavailable\n")
        return 3
    settled = []
    try:
        python, overrides = direct_python(sys.executable)
        environment = dict(os.environ)
        environment.update(overrides)
        environment.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        environment["AGENT_WORKTREES_PRE_SETTLEMENT_DEADLINE"] = str(settlement_deadline)
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
            budget=OwnedExecutionBudget(settlement_deadline + 2.0, 2.0),
            on_settled=lambda: settled.append(True),
        )
    except OwnedCleanupError as error:
        sys.stderr.write("agent-worktrees resident policy: cleanup unconfirmed\n")
        sys.stderr.flush()
        # Retain exact SDK custody until the caller closes its lifetime channel.
        threading.Event().wait()
        raise error
    except subprocess.TimeoutExpired as error:
        if settled and error.output:
            try:
                envelope = json.loads(error.output)
                pending = envelope["_ownedCleanupPending"]
                if set(envelope) != {"_ownedCleanupPending"} or not isinstance(pending, dict):
                    return 4
                from hook_pre_policy import valid_decision

                if not valid_decision(pending):
                    return 4
            except (ValueError, KeyError, TypeError):
                return 4
            sys.stdout.write(json.dumps(pending, separators=(",", ":")))
            return 0
        return 4
    except Exception:
        return 2
    if result.returncode:
        return 2
    if result.stdout and not (legacy and result.stdout.strip() == "{}"):
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    return 0


def main() -> int:
    settlement_deadline = time.monotonic() + 23.0
    if sys.argv[1:] not in ([], ["--base64-input"]):
        return 2
    encoded = bool(sys.argv[1:])
    limit = 4 * ((_MAX_INPUT + 2) // 3) + 1 if encoded else _MAX_INPUT
    frame = sys.stdin.buffer.readline(limit + 1)
    if len(frame) > limit or not frame.endswith(b"\n"):
        return 2
    if encoded:
        try:
            frame = base64.b64decode(frame[:-1], validate=True)
        except (binascii.Error, ValueError):
            return 2
        if len(frame) > _MAX_INPUT:
            return 2
    threading.Thread(target=_watch_caller, name="policy-caller-lifetime", daemon=True).start()
    return execute(frame, settlement_deadline=settlement_deadline)


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    # The daemon reader owns stdin; normal SDK scopes are settled before this reply.
    os._exit(code)
