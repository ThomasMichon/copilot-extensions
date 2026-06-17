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

    # Honor the configured relay_port + ado_host from codespaces.yaml. The port
    # must match what the SSH tunnel forwards; the ado_host lets host-less
    # ``ado-auth-helper get-access-token`` (no scope) requests resolve a default
    # ADO host instead of being rejected (#64). Both fall back to the relay
    # defaults; ado_host is never hardcoded to a specific org here -- it comes
    # from the adopting repo's config.
    try:
        from .config import load_merged_config

        creds = load_merged_config().credentials
        builder.set_port(creds.relay_port)
        builder.set_ado_host(creds.ado_host)
    except Exception:  # pragma: no cover - config optional
        log.debug("codespaces relay config unavailable; using relay defaults")
