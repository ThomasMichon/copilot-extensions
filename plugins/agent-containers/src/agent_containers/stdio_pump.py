"""Pump a transport child's stdio over explicit pipes.

Inherited pipes are unreliable under ``CREATE_NO_WINDOW`` on Windows, so the
``exec --stdio`` wrapper forwards bytes between its own stdio and the child.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

from agent_procutil import no_window_flags as _creation_flags

log = logging.getLogger("agent-containers")


def exec_stdio(spawn_cmd: list[str], env: dict[str, str]) -> int:
    """Run a transport command, pumping stdio over explicit pipes."""
    import threading

    proc = subprocess.Popen(
        spawn_cmd,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=_creation_flags(),
    )

    def _forward_in() -> None:
        try:
            in_fd = sys.stdin.buffer.fileno()
            while True:
                data = os.read(in_fd, 65536)
                if not data:
                    break
                proc.stdin.write(data)
                proc.stdin.flush()
        except (OSError, ValueError) as exc:
            log.error("transport stdin forwarding failed: %s", exc)
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass

    def _forward(src, dst) -> None:
        try:
            src_fd = src.fileno()
            while True:
                data = os.read(src_fd, 65536)
                if not data:
                    break
                dst.write(data)
                dst.flush()
        except (OSError, ValueError) as exc:
            log.error("transport output forwarding failed: %s", exc)

    threads = [
        threading.Thread(target=_forward_in, daemon=True),
        threading.Thread(target=_forward, args=(proc.stdout, sys.stdout.buffer), daemon=True),
        threading.Thread(target=_forward, args=(proc.stderr, sys.stderr.buffer), daemon=True),
    ]
    for t in threads:
        t.start()
    rc = proc.wait()
    # Let the output pumps drain anything buffered after exit.
    for t in threads[1:]:
        t.join(timeout=2)
    return rc

