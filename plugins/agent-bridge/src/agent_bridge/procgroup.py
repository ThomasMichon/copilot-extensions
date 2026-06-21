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

import os
import sys


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
