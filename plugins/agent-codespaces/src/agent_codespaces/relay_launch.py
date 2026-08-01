"""Relay launch prelude for the detached Session Host path.

agent-bridge's ``CodeSpaceSpawner`` launches copilot **detached** on the
CodeSpace (``setsid nohup``), not via ``agent-codespaces ssh``, so it must
reproduce the relay env prelude the ssh path injects: neutralize injected static
PATs (#160/#77) so a dispatched agent never relies on a stale token instead of
the credential relay, export ``LC_GIT_CREDENTIAL_RELAY`` + the per-codespace
token, and disable interactive git prompts. This is the **public seam**
agent-bridge calls (guarded import) so the ssh path and the Session-Host path
stay in lockstep.
"""

from __future__ import annotations

import os
from pathlib import Path

# Static PATs a CodeSpace injects that must be neutralized so a dispatched agent
# never relies on a stale/expired token instead of the credential relay.
SCRUB_ENV_VARS: tuple[str, ...] = ("MS_ADO_PAT",)

# Directory on the CodeSpace where the launch prelude publishes credential-relay
# port-mapping files (one JSON per live relay port). The ADO auth helpers
# discover a working relay by enumerating these and probing each for a live TCP
# channel -- so a dispatched tool shell that never inherited
# ``LC_GIT_CREDENTIAL_RELAY`` (or inherited a since-torn-down port) can still
# find an active channel back to the caller (dotfiles #489/#187/#19). Kept under
# the same ``~/.agent-bridge`` dir the connect breadcrumb uses.
RELAY_PORTMAP_DIR = "$HOME/.agent-bridge/relay-ports"


def build_relay_portmap_write(relay_port: int) -> str:
    """POSIX snippet that publishes a relay port-mapping file (best-effort).

    Writes ``<RELAY_PORTMAP_DIR>/<port>.json`` = ``{"port","token","ts"}`` with a
    restrictive umask, reading the secret from the just-exported
    ``LC_GIT_CREDENTIAL_RELAY_TOKEN`` (never re-interpolated). Never aborts the
    prelude (``|| true``). Keyed by port so repeat launches to the same relay are
    idempotent; a stale file whose channel later dies is pruned by the auth
    helpers' liveness probe (the discovery reader), not here.
    """
    d = RELAY_PORTMAP_DIR
    return (
        f'mkdir -p "{d}" 2>/dev/null; '
        '( umask 177; printf \'{"port":%s,"token":"%s","ts":%s}\\n\' '
        f'{relay_port} "$LC_GIT_CREDENTIAL_RELAY_TOKEN" '
        '"$(date +%s 2>/dev/null || echo 0)" '
        f'> "{d}/{relay_port}.json" ) 2>/dev/null || true; '
    )


def build_relay_env(
    relay_port: int, relay_token: str | None, *, use_relay: bool
) -> str:
    """Build the CodeSpace launch-prelude env string.

    ALWAYS prepends the PAT scrub (so it can never be clobbered by the relay
    exports); appends the relay exports when ``use_relay``. ``GIT_TERMINAL_PROMPT=0``
    keeps git from blocking on an interactive prompt when a credential can't be
    resolved. When ``use_relay``, also publishes a port-mapping file so the auth
    helpers can rediscover this relay channel by liveness probe even if the env
    is not inherited by a later tool shell (see :func:`build_relay_portmap_write`).
    """
    env = "".join(f"unset {v}; " for v in SCRUB_ENV_VARS)
    if use_relay:
        env += (
            f"export LC_GIT_CREDENTIAL_RELAY={relay_port}; "
            f"export LC_GIT_CREDENTIAL_RELAY_TOKEN={relay_token}; "
            "export GIT_TERMINAL_PROMPT=0; "
        )
        env += build_relay_portmap_write(relay_port)
    return env


def build_relay_launch_env(
    codespace_name: str, relay_port: int | None = None
) -> tuple[str, int]:
    """Return ``(prelude_env, relay_port)`` for a detached CodeSpace launch.

    Mints/reuses the per-codespace relay token and resolves the relay port, so a
    Session Host launched detached on the CS inherits working ADO/git auth over
    the relay (the ``-R`` reverse-forward that carries it is stood up by the
    caller's persistent forward). Raises if config is unavailable.

    ``relay_port`` lets an in-daemon caller (agent-bridge) inject the relay's
    *actually-bound* port from ``relay_state.get_live_relay_port`` so the CS env
    + ``-R`` follow the live relay rather than the statically declared config
    port (dotfiles #489/#540 pt3). When ``None`` (e.g. the standalone
    ``agent-codespaces`` path, which cannot see the daemon's process-local live
    port) it first tries the port the daemon **publishes** to its config dir
    (``relay_state``'s cross-daemon rendezvous, discovered here without importing
    ``agent_bridge``), so an ephemeral/dynamic bind is honored on this path too;
    only if that is absent does it fall back to the configured
    ``credentials.relay_port``.
    """
    from .config import load_merged_config
    from .relay_token import token_for

    if relay_port is not None:
        port = int(relay_port)
    else:
        published = _published_live_relay_port()
        if published is not None:
            port = published
        else:
            cfg = load_merged_config()
            port = int(cfg.credentials.relay_port)
    token = token_for(codespace_name)
    return build_relay_env(port, token, use_relay=True), port


def _published_live_relay_port() -> int | None:
    """Best-effort read of the live relay port the agent-bridge daemon publishes.

    Mirrors agent-bridge ``relay_state``'s cross-daemon publish
    (``<primary-config-dir>/relay-port``) **without importing ``agent_bridge``** --
    the standalone agent-codespaces venv does not contain it. Honors
    ``AGENT_BRIDGE_CONFIG_DIR`` (default ``~/.agent-bridge``) and resolves an
    ``/elevated`` sub-daemon dir to its primary parent, so an ephemeral/dynamic
    relay port is discovered even on the standalone path (#540 pt3). Returns
    ``None`` when the file is absent/unparseable.
    """
    base = Path(
        os.environ.get("AGENT_BRIDGE_CONFIG_DIR", "~/.agent-bridge")
    ).expanduser()
    if base.name == "elevated":
        base = base.parent
    try:
        txt = (base / "relay-port").read_text(encoding="utf-8").strip()
        return int(txt) if txt else None
    except (OSError, ValueError):
        return None


# Last-resort relay port when neither a live (published) nor a configured port
# is available -- the retired fixed default, kept only as a backstop (#694).
LEGACY_RELAY_PORT = 9857


def effective_relay_port(config) -> int:
    """Resolve the relay port for a directly-constructed SSH ``-R`` forward + env.

    The relay now binds a **dynamic** port by default (``credentials.relay_port``
    defaults to 0 -> the daemon binds an OS-assigned ephemeral port). The paths
    that build the ``-R`` reverse-forward straight from config -- the interactive
    ``ssh`` connect and ``provision`` -- must therefore follow the daemon's
    **live** port, not the (now 0) static config, exactly like
    :func:`build_relay_launch_env` does for the detached path. Resolution:

    1. the port the agent-bridge daemon **publishes** (``relay_state`` rendezvous,
       read without importing ``agent_bridge``) -- honors an ephemeral/dynamic bind;
    2. else a **positive** configured ``credentials.relay_port`` (an explicit pin);
    3. else :data:`LEGACY_RELAY_PORT` (9857) as a last-resort backstop.
    """
    published = _published_live_relay_port()
    if published:
        return published
    try:
        configured = int(getattr(config.credentials, "relay_port", 0) or 0)
    except (TypeError, ValueError):
        configured = 0
    return configured or LEGACY_RELAY_PORT
