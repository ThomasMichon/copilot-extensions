"""POSIX group owner: stay alive until the caller closes its control pipe."""

from __future__ import annotations

import os
import select
import signal
import subprocess
import sys


def main() -> int:
    """Report reader status, then terminate this live owned group on control EOF."""
    os.setpgid(0, 0)
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    try:
        if select.select([0], [], [], 0)[0]:
            return 126
        try:
            reader = subprocess.Popen(
                sys.argv[1:], stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )  # headless-guard: allow POSIX-only group owner; Windows never invokes this host
        except OSError:
            os.write(2, b"126\n")
            os.close(1)
            os.close(2)
            reader = None
        reported = reader is None
        while True:
            if select.select([0], [], [], 0.01)[0]:
                return 126
            if not reported and reader is not None:
                status = reader.poll()
                if status is not None:
                    os.write(2, f"{status}\n".encode("ascii"))
                    os.close(1)
                    os.close(2)
                    reported = True
    finally:
        # The owner itself is still alive; its group identity cannot have been reused.
        os.killpg(os.getpgrp(), signal.SIGKILL)


if __name__ == "__main__":
    raise SystemExit(main())
