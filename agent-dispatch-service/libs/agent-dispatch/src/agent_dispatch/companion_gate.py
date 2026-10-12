"""Start a contributed command only after the supervisor confirms containment."""

from __future__ import annotations

import base64
import json
import os
import signal
import subprocess
import time


def _read_gate() -> bytes:
    if os.name == "nt":
        import msvcrt

        handle = int(os.environ.pop("COPILOT_COMPANION_GATE_HANDLE"))
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    else:
        fd = int(os.environ.pop("COPILOT_COMPANION_GATE_FD"))
    try:
        return os.read(fd, 1)
    finally:
        os.close(fd)


def main() -> int:
    encoded = os.environ.pop("COPILOT_COMPANION_COMMAND")
    command = json.loads(base64.urlsafe_b64decode(encoded).decode())
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(value, str) and value for value in command)
    ):
        return 2
    if _read_gate() != b"1":
        return 3
    completed = subprocess.run(  # noqa: S603 -- supervisor-validated argv
        command,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if os.name != "nt":
        # Remain the process-group leader for the entire companion lifetime.
        # If the declared root exits after spawning descendants, retire the
        # complete group instead of leaving it leaderless and unrecoverable.
        group = os.getpgrp()
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        os.killpg(group, signal.SIGTERM)
        time.sleep(1)
        os.killpg(group, signal.SIGKILL)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
