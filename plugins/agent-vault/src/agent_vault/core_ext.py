"""agent-vault extension: reach a wired *core* over the core-delegation seam.

agent-vault's installed daemon is a light **custody adapter**. When a heavy
**core** is wired -- a remote engine or a container running agent-vault's
own daemon protocol -- this extension lets the CLI reach it as just another client
transport, discovered and dialed by the generic ``core-delegation`` primitive.

It registers a **fallback** transport (consulted only after the built-in
unix-socket / named-pipe / TCP transports), so:

- **No core wired** (no ``AGENT_VAULT_CORE_ENDPOINT`` and no core rendezvous
  file) -> :func:`core_transport` returns ``None`` and nothing changes: the local
  daemon / user-mode path handles the request. This preserves
  ``visions/plugin-services`` §standalone-reachability -- agent-vault never
  *requires* a core.
- **A local daemon is handling the request** -> the built-ins succeed first and
  this transport is never consulted.
- **A core is wired and the local daemon is absent** -> the request is delegated
  to the core over a tokened, newline-framed JSON channel.

Wiring a core is opt-in configuration:

- ``AGENT_VAULT_CORE_ENDPOINT`` -- an explicit ``"<transport>:<address>"`` spec
  (e.g. ``"tcp:127.0.0.1:52731"`` or ``"unix:/path/core.sock"``), or leave it
  unset and let the core advertise itself via a rendezvous file under the core
  runtime dir.
- ``AGENT_VAULT_CORE_TOKEN`` -- an optional bearer token attached to each
  delegated request (token-bind); a core that enforces it validates it, one that
  does not ignores it.

The core runtime dir is deliberately **distinct** from the local daemon's
``~/.agent-vault/run`` so discovery resolves the *core*, not the local daemon
(which the built-in transports already reach).
"""

from __future__ import annotations

import os
from pathlib import Path

from .coredelegation import delegate

#: Explicit endpoint override for a wired core (a ``"<transport>:<address>"`` spec).
CORE_ENDPOINT_ENV = "AGENT_VAULT_CORE_ENDPOINT"
#: Optional bearer token attached to each delegated request.
CORE_TOKEN_ENV = "AGENT_VAULT_CORE_TOKEN"  # noqa: S105 -- an env var name, not a secret
#: Upper bound (seconds) on a single delegated-core round-trip, overridable.
CORE_TIMEOUT_ENV = "AGENT_VAULT_CORE_TIMEOUT"

# The core is an *optional* peer. Several CLI paths call the vault with
# ``timeout=None`` (unbounded) because the local daemon may legitimately block on
# an interactive GUI unlock the operator is watching. A *remote/containerized*
# core has no such local dialog, so an unbounded wait there would let a wired but
# misbehaving core **hang the whole CLI** -- the opposite of the vision's
# ``degrade-gracefully`` behavior (a failing optional peer must degrade a feature,
# never the whole service). So the delegated-core round-trip is always bounded to
# a finite cap, independent of the caller's own (possibly unbounded) timeout.
DEFAULT_CORE_TIMEOUT = 30.0


def core_runtime_dir() -> Path:
    """The runtime dir where a wired core advertises its rendezvous file.

    Distinct from the local daemon's ``~/.agent-vault/run`` so a discovered
    endpoint is the *core*, never a re-dial of the local built-in daemon.
    Overridable with ``AGENT_VAULT_CORE_RUN_DIR`` for tests / non-default layouts.
    """
    override = os.environ.get("AGENT_VAULT_CORE_RUN_DIR")
    if override:
        return Path(override)
    return Path.home() / ".agent-vault" / "core"


def _core_timeout(requested: float | None) -> float:
    """Bound a delegated-core round-trip to a finite cap.

    Returns the smaller of the caller's ``requested`` timeout and the configured
    cap (``AGENT_VAULT_CORE_TIMEOUT``, default :data:`DEFAULT_CORE_TIMEOUT`). An
    unbounded (``None``) or oversized request is clamped to the cap, so a wired
    but hanging core can never block the standalone CLI -- it fails fast and the
    request falls through / surfaces an error instead of hanging.
    """
    try:
        cap = float(os.environ.get(CORE_TIMEOUT_ENV) or DEFAULT_CORE_TIMEOUT)
    except (TypeError, ValueError):
        cap = DEFAULT_CORE_TIMEOUT
    if cap <= 0:
        cap = DEFAULT_CORE_TIMEOUT
    if requested is None:
        return cap
    return min(requested, cap)


def core_transport(request, timeout, ctx):
    """A :data:`~agent_vault.extensions.ClientTransport`: delegate to a wired core.

    Returns the core's response dict, or ``None`` to fall through when no core is
    wired or reachable. The round-trip is always time-bounded (see
    :func:`_core_timeout`) so an optional core never hangs the standalone CLI.
    """
    return delegate(
        "agent-vault-core",
        request,
        timeout=_core_timeout(timeout),
        token=os.environ.get(CORE_TOKEN_ENV) or None,
        override=os.environ.get(CORE_ENDPOINT_ENV) or None,
        runtime_dir=core_runtime_dir(),
        tag=False,  # let the extension registry stamp the ``ext:<name>`` provenance
    )


def register(registry) -> None:
    """Register the core-delegation transport as a built-in fallback."""
    registry.register_transport(core_transport, name="core-delegation")
