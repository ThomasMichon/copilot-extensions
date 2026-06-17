"""Credential-relay source injection for GitHub Codespaces targets.

agent-bridge discovers this hook (see ``agent_bridge.agent_registry``) and calls
``register_relay`` to let agent-codespaces contribute the credential sources its
codespace targets need. The relay itself is owned/run by agent-bridge; this
module only injects the codespace-specific profile.

For codespaces, auth is forwarded over the SSH ``-R`` tunnel, so the only source
needed is :class:`~credential_relay.sources.git_credential.GitCredentialSource`
(proxies to the host Git Credential Manager for ADO + GitHub git/npm/nuget).
"""

from __future__ import annotations

import logging

log = logging.getLogger("agent-codespaces.relay")


def register_relay(builder) -> None:
    """Inject the codespace credential-relay profile into ``builder``.

    ``builder`` is a :class:`credential_relay.registry.RelayBuilder`.
    """
    from credential_relay.sources.git_credential import GitCredentialSource

    from .relay_token import validate as _validate_codespace_token

    builder.add_source(GitCredentialSource())

    # Faithfully shim the official ``azure-auth-helper get-access-token "<scope>"``
    # broker: allow minting an AAD token for ANY scope from the host az identity
    # (the official helper is a generic managed-identity broker). Gated behind a
    # per-codespace token (see relay_token) -- the shared relay also serves
    # network-reachable container targets, so get-azure-token must stay gated;
    # the SSH-tunnel-isolated codespace presents its own token.
    builder.allow_azure_resources(["*"])
    builder.require_token(["get-azure-token"], _validate_codespace_token)

    # Honor the configured relay_port from codespaces.yaml so the server binds
    # the same port the SSH tunnel forwards (falls back to the server default).
    try:
        from .config import load_merged_config

        builder.set_port(load_merged_config().credentials.relay_port)
    except Exception:  # pragma: no cover - config optional
        log.debug("codespaces relay_port unavailable; using relay default")
