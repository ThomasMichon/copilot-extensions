"""Bounded, secret-free credential protocol probes for required relay admission."""

from __future__ import annotations

import shlex
import socket
import time


def relay_ping(port: int, timeout: float = 2.0) -> bool:
    """Require a pong, not merely a TCP listener, from the host relay."""
    deadline = time.monotonic() + timeout
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as connection:
            connection.sendall(b"ping\n\n")
            response = b""
            while len(response) < 6:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                connection.settimeout(remaining)
                chunk = connection.recv(6 - len(response))
                if not chunk:
                    break
                response += chunk
            return response == b"pong\n\n"
    except (OSError, ValueError):
        return False


def remote_ping_command(port: int) -> str:
    """Probe the exact remote listener without helper discovery or cache fallback."""
    program = (
        "import socket,sys,time\n"
        "deadline=time.monotonic()+2\n"
        "with socket.create_connection(('127.0.0.1',int(sys.argv[1])),timeout=2) as s:\n"
        " s.sendall(b'ping\\n\\n')\n"
        " data=b''\n"
        " while len(data)<6:\n"
        "  remaining=deadline-time.monotonic()\n"
        "  if remaining<=0: sys.exit(1)\n"
        "  s.settimeout(remaining)\n"
        "  chunk=s.recv(6-len(data))\n"
        "  if not chunk: break\n"
        "  data+=chunk\n"
        "sys.exit(0 if data==b'pong\\n\\n' else 1)\n"
    )
    return f"python3 -c {shlex.quote(program)} {int(port)}"


async def remote_relay_ready(manager, name: str, port: int) -> bool:
    """Require a bounded round trip through the CodeSpace's reverse forward."""
    try:
        result = await manager.exec_command(name, remote_ping_command(port), timeout=5.0)
        return result.exit_code == 0 and not getattr(result, "timed_out", False)
    except Exception:
        return False
