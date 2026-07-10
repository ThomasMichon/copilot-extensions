"""The far-side runner: resolve an agent **locally** and run it inside a
Session Host.

This is the single program every boundary Spawner launches on the *far* side of
its boundary -- an elevated scheduled task (elevation), an ``ssh`` command
(machine-mesh), or an agent-codespaces bootstrap (CodeSpace). In every case the
copilot child ends up owned by a Session Host that:

* runs copilot's stdio as a clean **local** pipe (no tunnel in the hot path), and
* serves a seq/ack reattach endpoint the frontend dials as ``127.0.0.1:<port>``
  (directly, or via a forward that makes the remote look local).

**Resolution happens here, on the far side, on purpose.** The elevated daemon
already resolves a ``requires_admin`` agent locally so it runs elevated with no
per-agent ``gsudo``; the same is true across SSH / on a CodeSpace, where the
worktree / enlistment / auth context is native to that side. So the frontend
hands us a *name*, and we run the same ``build_resolver`` -> ``resolve_async`` ->
``resolve_local_launch`` path a local session uses -- just in the far-side
process -- then hand the resulting copilot argv to :func:`run_host`.

The connect-auth **nonce** rides in via the ``AGENT_BRIDGE_SESSION_HOST_NONCE``
env (set by the Spawner); :func:`run_host` reads it and requires it on ATTACH.
Runnable as ``python -m agent_bridge session-host-agent <name>``.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger("agent-bridge.session-host")


async def run_agent_session_host(
    agent_name: str,
    *,
    port: int = 0,
    state_file: str | os.PathLike[str] | None = None,
    cwd: str | None = None,
    nonce: str = "",
    resolver: Any | None = None,
) -> None:
    """Resolve ``agent_name`` to a local copilot launch and serve it as a Host.

    Builds the agent resolver from local config (unless ``resolver`` is injected
    -- the seam tests use this), resolves the name to a **local** target, turns
    that into ``(argv, cwd, env)`` via :func:`resolve_local_launch`, and runs a
    Session Host owning the copilot child. Blocks until the host is closed.

    Raises if the name resolves to a non-local target: reaching this runner means
    we are already on the target side, so a ``ssh``/``command`` target would be a
    mis-route (e.g. an elevated relay target produced because the caller was not
    actually elevated).
    """
    from ..agent_registry import build_resolver
    from ..config import load_config
    from ..transport import resolve_local_launch
    from .launcher import run_host

    if resolver is None:
        resolver = build_resolver(load_config())
    if resolver is None:
        raise RuntimeError(
            "no agent resolver available (empty topology/agents config)"
        )

    target = await resolver.resolve_async(agent_name)
    if target.type != "local":
        raise RuntimeError(
            f"agent '{agent_name}' resolved to a non-local target "
            f"(type={target.type!r}); the far-side runner only hosts local "
            "targets -- a remote/command target here means a mis-route"
        )

    args, work_dir, env = await resolve_local_launch(target, session_id=agent_name)
    run_cwd = cwd or work_dir or target.cwd
    log.info(
        "Far-side runner: hosting agent %r (cwd=%s, port=%s, secured=%s)",
        agent_name, run_cwd, port or "auto", bool(nonce or os.environ.get(
            "AGENT_BRIDGE_SESSION_HOST_NONCE", "")),
    )
    await run_host(
        args, port=port, state_file=state_file, cwd=run_cwd, env=env, nonce=nonce,
    )
