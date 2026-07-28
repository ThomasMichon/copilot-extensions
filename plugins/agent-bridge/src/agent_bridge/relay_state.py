"""Live credential-relay port registry (daemon-local).

The primary daemon records the port its ``CredentialRelayServer`` actually
bound, so the SSH spawn path can source the reverse-forward (and the
``LC_GIT_CREDENTIAL_RELAY`` env) from the *live* port rather than a statically
declared ``machines.yaml`` value. This lets ``machines.yaml`` stop hardcoding
the relay port per machine (the daemon owns it), and a future ephemeral/dynamic
bind is honored automatically because the *actually-bound* port is what gets
forwarded.

Scope: process-local. An elevated sub-daemon that reuses the primary's relay
(``enable_credential_relay=False``) never sets this, so ``get_live_relay_port``
returns ``None`` there and callers fall back to any declared hook. Publishing
the live port across daemons is the follow-up (approach B).
"""

from __future__ import annotations

_live_relay_port: int | None = None


def set_live_relay_port(port: int | None) -> None:
    """Record (or clear) the port the in-process credential relay is bound to."""
    global _live_relay_port
    _live_relay_port = port


def get_live_relay_port() -> int | None:
    """Return the live relay port for this daemon, or ``None`` if not hosting one."""
    return _live_relay_port
