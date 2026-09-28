"""Process-group teardown helper shared by the transport and ACP client.

Tearing down a spawned agent signals the *child's* process group so the whole
tree dies (``ssh -> remote shell``, ``cmd -> pwsh -> copilot``, ...). That is
only safe when the child leads its **own** process group, which every agent
spawn arranges with ``start_new_session=True``. If a spawn path ever omits it,
the child inherits the bridge's process group and a naive
``os.killpg(os.getpgid(pid), ...)`` resolves to the **bridge's own** group --
SIGTERM-ing the daemon itself (uvicorn logs "Shutting down" and the HTTP server
stops serving). That is exactly how stopping a remote/SSH session took the whole
bridge down -- see agent-bridge #1001.

``safe_killpg`` makes that failure mode impossible: it refuses to signal our own
process group, so the worst a bad spawn path can do is fail to reap a child
(handled by the caller's direct-child fallback) -- never take the bridge down.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from typing import Any


def safe_killpg(pid: int, sig: int) -> bool:
    """Signal the process group led by ``pid`` -- but never our own group.

    Returns ``True`` if the group signal was delivered. Returns ``False`` when
    it was unsafe or impossible (no such pid, or the child shares this
    process' group), so the caller can fall back to signaling only the direct
    child (``proc.terminate()`` / ``proc.kill()``).
    """
    if sys.platform == "win32":
        return False
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, OSError):
        return False
    # Never signal our own process group: that would take the bridge down.
    if pgid == os.getpgid(0):
        return False
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


async def terminate_windows_tree(
    proc: Any, *, grace: float = 3.0, kill_timeout: float = 5.0,
) -> None:
    """Windows tree-kill, graceful stdin-close first (shared, see #4031).

    A forceful ``taskkill /T /F`` alone vanishes the local process instantly,
    but gives a child like ``ssh.exe`` no chance to tear its own remote
    session down -- the remote end then notices only via its own keepalive
    timeout (tens of seconds). Closing stdin first lets a well-behaved child
    exit on its own within ``grace`` seconds; only then do we escalate to the
    forceful whole-tree kill, unchanged.
    """
    pid = proc.pid
    with contextlib.suppress(Exception):
        if proc.stdin and not proc.stdin.is_closing():
            proc.stdin.close()
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace)
        return
    except (TimeoutError, asyncio.TimeoutError):
        pass
    try:
        killer = await asyncio.create_subprocess_exec(
            "taskkill", "/PID", str(pid), "/T", "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(killer.wait(), timeout=kill_timeout)
    except (TimeoutError, asyncio.TimeoutError, OSError, ProcessLookupError):
        pass
