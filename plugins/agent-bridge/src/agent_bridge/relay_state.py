"""Live credential-relay port registry (daemon-local + cross-daemon publish).

The primary daemon records the port its ``CredentialRelayServer`` actually
bound, so the SSH spawn path can source the reverse-forward (and the
``LC_GIT_CREDENTIAL_RELAY`` env) from the *live* port rather than a statically
declared ``machines.yaml`` value. This lets ``machines.yaml`` stop hardcoding
the relay port per machine (the daemon owns it), and a future ephemeral/dynamic
bind is honored automatically because the *actually-bound* port is what gets
forwarded.

**Cross-daemon publish (approach B).** The in-process value is only known to the
daemon that hosts the relay. A **sibling/elevated sub-daemon** runs with the
relay disabled (``enable_credential_relay=False``) and *reuses the primary's
relay*, so its in-process value is ``None``. To let it discover the live port
(rather than fall back to a — now removed — declared ``machines.yaml`` hook),
the hosting daemon **publishes the port to a file at the primary config dir**;
``get_live_relay_port`` reads it as a fallback. The elevated sub-daemon runs with
``AGENT_BRIDGE_CONFIG_DIR`` = ``<primary>/elevated``, so the path is resolved to
the *primary* dir (its parent) on both sides — a per-box, per-user rendezvous.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("agent-bridge")

_live_relay_port: int | None = None

# Filename under the primary config dir carrying the live relay port.
_RELAY_PORT_FILE = "relay-port"
# The elevated sub-daemon's config dir is ``<primary>/elevated`` (see
# ``elevated._SUBDIR``); resolve the publish path to the primary on both sides.
_ELEVATED_SUBDIR = "elevated"


def _primary_config_dir() -> Path:
    """The primary daemon's config dir (where the relay port is published).

    Resolved so the relay-hosting primary (``~/.agent-bridge``) and an elevated
    sub-daemon (``~/.agent-bridge/elevated``) agree on the same file.
    """
    from .config import config_dir

    d = config_dir()
    return d.parent if d.name == _ELEVATED_SUBDIR else d


def _relay_port_path() -> Path:
    return _primary_config_dir() / _RELAY_PORT_FILE


def set_live_relay_port(port: int | None) -> None:
    """Record (or clear) the port the in-process credential relay is bound to.

    Also publishes it to the primary config dir so sibling/elevated sub-daemons
    that reuse this relay can discover it. Best-effort: a publish failure never
    breaks relay startup (callers still have the in-process value).
    """
    global _live_relay_port
    _live_relay_port = port
    try:
        path = _relay_port_path()
        if port:
            path.write_text(str(port), encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
    except OSError as exc:
        log.debug("Could not publish live relay port to %s: %s", _RELAY_PORT_FILE, exc)


def get_live_relay_port() -> int | None:
    """Return the live relay port.

    Prefers this daemon's in-process value (set when it hosts the relay); falls
    back to the port published by the primary (for a sibling/elevated sub-daemon
    that reuses the primary's relay). Returns ``None`` when neither is available.
    """
    if _live_relay_port is not None:
        return _live_relay_port
    try:
        txt = _relay_port_path().read_text(encoding="utf-8").strip()
        return int(txt) if txt else None
    except (OSError, ValueError):
        return None
