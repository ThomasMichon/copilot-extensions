"""Isolated POSIX supervisor retaining its reader's group identity until cleanup."""

from __future__ import annotations

import os
import select
import signal
import sys


def main() -> int:
    """Report status without reaping, then acknowledge explicit owned cleanup."""
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    reader: int | None = None
    requested = False
    reported = False
    try:
        if select.select([0], [], [], 0)[0]:
            requested = os.read(0, 1) == b"C"
            return 0
        try:
            reader = os.posix_spawn(
                sys.argv[1], sys.argv[1:], os.environ, setpgroup=0,
                file_actions=[
                    (os.POSIX_SPAWN_OPEN, 0, os.devnull, os.O_RDONLY, 0),
                    (os.POSIX_SPAWN_OPEN, 2, os.devnull, os.O_WRONLY, 0),
                ],
            )  # headless-guard: allow isolated POSIX-only supervisor; Windows never invokes this host
        except OSError:
            os.write(2, b"126\n")
            os.close(1)
        reported = reader is None
        while True:
            if select.select([0], [], [], 0.01)[0]:
                requested = os.read(0, 1) == b"C"
                return 0
            if not reported and reader is not None:
                status = os.waitid(
                    os.P_PID, reader, os.WEXITED | os.WNOHANG | os.WNOWAIT,
                )
                if status is not None:
                    code = status.si_status if status.si_code == os.CLD_EXITED else -status.si_status
                    os.write(2, f"{code}\n".encode("ascii"))
                    os.close(1)
                    reported = True
    finally:
        if not reported:
            os.write(2, b"126\n")
            os.close(1)
        if reader is not None:
            # Only this isolated single-threaded parent can reap its retained root.
            os.killpg(reader, signal.SIGKILL)
            os.waitpid(reader, 0)
        if requested:
            os.write(2, b"cleaned\n")


if __name__ == "__main__":
    raise SystemExit(main())
