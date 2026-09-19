"""Build the remote command for a fenced Session Host launch."""
from __future__ import annotations
import shlex
from .launcher import _NONCE_ENV

def build_remote_launch(
    bundle_remote: str,
    state_remote: str,
    log_remote: str,
    child_argv: list[str],
    *,
    nonce: str = "",
    cwd: str | None = None,
    session_id: str = "",
    host_version: str = "",
    reverse_forwards: list[str] | None = None,
    unexpected_reap_seconds: float = 60.0,
    active_reap_seconds: float = 0.0,
    venue_id: str = "",
) -> str:
    """Assemble the far-side bash command that launches a survivable Host.

    ``setsid nohup … </dev/null &`` detaches the Host from the launch SSH channel
    so it **outlives the channel closing** (the POSIX survival seam, validated in
    the #145 live proof), while ``PR_SET_PDEATHSIG`` inside the Host still ties
    the copilot child's life to the Host. The nonce rides in via the env (off the
    command line). ``unexpected_reap_seconds`` / ``active_reap_seconds`` bound how
    long a front-less idle / active child is held before the detached Host lets
    it go (so a reconnecting front can resume). Paths are POSIX (the far side is
    Linux).
    """
    import posixpath

    state_dir = posixpath.dirname(state_remote)
    dirs = sorted({
        posixpath.dirname(p)
        for p in (state_remote, log_remote)
        if posixpath.dirname(p)
    })
    prep = ""
    if dirs:
        prep = (
            "mkdir -p "
            + " ".join(shlex.quote(d) for d in dirs)
            + " || exit 1; "
        )
    if state_dir:
        prep += (
            f"chmod 700 {shlex.quote(state_dir)} || exit 1; "
        )
    host_cmd = (
        f"python3 {shlex.quote(bundle_remote)} --port 0 "
        f"--state-file {shlex.quote(state_remote)} "
        f"--unexpected-reap-seconds {unexpected_reap_seconds} "
        f"--active-reap-seconds {active_reap_seconds} "
    )
    if session_id:
        host_cmd += f"--session-id {shlex.quote(session_id)} "
    if host_version:
        host_cmd += f"--host-version {shlex.quote(host_version)} "
    for spec in reverse_forwards or []:
        host_cmd += f"--reverse-forward {shlex.quote(spec)} "
    if cwd:
        host_cmd += f"--cwd {shlex.quote(cwd)} "
    host_cmd += "-- " + " ".join(shlex.quote(a) for a in child_argv)
    env_prefix = f"{_NONCE_ENV}={shlex.quote(nonce)} " if nonce else ""
    if venue_id:
        env_prefix += f"AGENT_BRIDGE_HOST_VENUE={shlex.quote(venue_id)} "
    launch = (
        f"{env_prefix}setsid nohup {host_cmd} "
        f"</dev/null >{shlex.quote(log_remote)} 2>&1 & echo launched"
    )
    return f"bash -lc {shlex.quote(prep + launch)}"

