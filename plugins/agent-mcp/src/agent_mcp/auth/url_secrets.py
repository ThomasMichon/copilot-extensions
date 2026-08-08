"""Resolve ``${name}`` secret placeholders inside ``server.url``.

The auth injectors put a secret into a *credential position* (an HTTP header or a
child env var). Some upstreams instead carry the secret **in the URL itself** --
e.g. an add-on that gates access on a secret URL path (``/private_<token>``)
rather than a bearer header. Without this, that secret cannot live in a committed
config, forcing a machine-local override to hardcode the full secret URL.

This module closes that gap using the *same* injector machinery: a ``${name}``
token in ``server.url`` is backed by a ``server.url_secrets[name]`` source (an
:class:`~agent_mcp.config.AuthSpec` reusing the auth kinds -- typically
``command`` + ``parse: raw`` for ``vault get "<entry>" password``). Resolution is
**lazy, at spawn/connect time** (never at config load / ``validate`` / ``status``),
so the secret is fetched only when the bridge actually connects -- never committed
and never placed in the session environment, exactly like the auth-injected
secrets.
"""

from __future__ import annotations

import logging

from ..config import BridgeConfig, url_placeholder_names
from .injectors import _build_one

log = logging.getLogger("agent-mcp.url-secrets")


async def resolve_url(cfg: BridgeConfig) -> str:
    """Return ``cfg.server.url`` with every ``${name}`` placeholder resolved.

    Builds an injector for each referenced ``server.url_secrets[name]`` source
    (reusing the auth-injector kinds), acquires its raw secret, and substitutes
    it into the URL. Returns the URL unchanged when there are no placeholders /
    no sources. Raises :class:`RuntimeError` if a referenced secret cannot be
    resolved (e.g. the vault is locked), so the bridge fails loudly instead of
    connecting to a half-formed URL.

    Config validation (:func:`agent_mcp.config.validate_config`) guarantees every
    placeholder has a matching source before this runs.
    """
    url = cfg.server.url or ""
    specs = cfg.server.url_secrets
    names = url_placeholder_names(url)
    if not specs or not names:
        return url

    resolved = url
    for name in names:
        spec = specs.get(name)
        if spec is None:  # defensive: validation should have caught this
            raise RuntimeError(
                f"agent-mcp: URL secret '${{{name}}}' has no source in "
                f"server.url_secrets (bridge '{cfg.name}')"
            )
        injector = _build_one(spec, cfg)
        secret = await injector.acquire_secret()
        if not secret:
            raise RuntimeError(
                f"agent-mcp: could not resolve URL secret '${{{name}}}' "
                f"(source kind '{spec.kind}') for bridge '{cfg.name}' -- is the "
                f"credential source available (e.g. vault unlocked)?"
            )
        resolved = resolved.replace("${" + name + "}", secret)

    log.debug("resolved %d URL secret placeholder(s) for bridge '%s'",
              len(names), cfg.name)
    return resolved
