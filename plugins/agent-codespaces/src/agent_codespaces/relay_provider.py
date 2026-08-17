"""Credential-relay source injection for GitHub Codespaces targets.

agent-bridge discovers this hook (see ``agent_bridge.agent_registry``) and calls
``register_relay`` to let agent-codespaces contribute the credential sources its
codespace targets need. The relay itself is owned/run by agent-bridge; this
module only injects the codespace-specific profile.

For codespaces, auth is forwarded over the SSH ``-R`` tunnel. The profile
serves the git-credential shape through
:class:`~credential_relay.sources.git_credential.GitCredentialSource`, and the
ADO REST bearer shape through the gated Azure CLI source.
"""

from __future__ import annotations

import logging

log = logging.getLogger("agent-codespaces.relay")

# Well-known Azure DevOps resource/app ID. This is the default raw ADO REST
# bearer audience the CodeSpace helper may request from the host az identity.
ADO_REST_RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"
# Azure Storage is used by dev-deploy/blob upload flows; keep it pre-allowed so
# narrowing the historical wildcard broker does not regress those explicit-scope
# requests.
AZURE_STORAGE_RESOURCE = "https://storage.azure.com/"
DEFAULT_AZURE_RESOURCES = [ADO_REST_RESOURCE, AZURE_STORAGE_RESOURCE]


def relay_profile() -> dict:
    """The declarative codespace relay profile agent-bridge applies over a
    process boundary (#892 Inc 2).

    Emits the same policy `register_relay` applies in-process -- sources, relay
    port, ADO host, the Azure resource allowlist, the token-gated actions, and
    the **token-store file path** -- as plain JSON so agent-bridge can apply it
    with a file-backed validator instead of importing this module. Derived from
    the same merged config, so the CLI path and the in-process fallback stay in
    lockstep. Config-unavailable degrades to the relay defaults (port/ado_host
    ``None`` -> the builder's ``set_*`` are no-ops, exactly as today).
    """
    from .relay_token import _TOKENS_FILE

    azure_resources = set(DEFAULT_AZURE_RESOURCES)
    port: int | None = None
    ado_host: str | None = None
    try:
        from .config import load_merged_config

        creds = load_merged_config(include_cwd=False).credentials
        port = creds.relay_port
        ado_host = creds.ado_host
        az_cfg = creds.sources.get("az-login")
        if az_cfg and az_cfg.enabled:
            azure_resources.update(az_cfg.allowed_resources)
    except Exception:  # pragma: no cover - config optional
        log.debug("codespaces relay config unavailable; using relay defaults")

    return {
        "sources": ["git-credential"],
        "port": port,
        "ado_host": ado_host,
        "azure_resources": sorted(azure_resources),
        "gated_actions": ["get-azure-token"],
        "token_store": str(_TOKENS_FILE),
        # Signal the scoped-authorizer contract: the token store records a
        # per-token ``allowed_resources`` allowlist, so agent-bridge gates
        # get-azure-token with a request-scoped FileTokenAuthorizer rather than a
        # bare FileTokenValidator. A bridge that predates the flag ignores it and
        # keeps the validator path (back-compatible).
        "scoped_azure": True,
    }


def register_relay(builder) -> None:
    """Inject the codespace credential-relay profile into ``builder``.

    ``builder`` is a :class:`credential_relay.registry.RelayBuilder`. Applies the
    same profile :func:`relay_profile` emits, but with the **in-process**
    request-scoped token authorizer -- this is the degrade-safe fallback
    agent-bridge uses when the ``agent-codespaces relay-profile`` CLI seam is
    unavailable (#892 Inc 2). ``set_port(None)`` / ``set_ado_host(None)`` are
    internally no-ops, so a config-unavailable profile behaves exactly as before.
    """
    from credential_relay.sources.git_credential import GitCredentialSource

    from .relay_token import authorize_azure

    prof = relay_profile()
    builder.add_source(GitCredentialSource())
    builder.set_port(prof["port"])
    builder.set_ado_host(prof["ado_host"])
    # Raw Azure/Entra bearer minting stays policy-gated: a CodeSpace token may
    # mint only the Azure resources recorded in its per-token allowlist (the ADO
    # REST resource by default, plus any resources the adopting repo's config
    # adds). The shared relay also serves network-reachable targets, so
    # get-azure-token stays behind a per-codespace token even though CodeSpaces
    # reach it through an SSH tunnel. ``allow_azure_resources`` enables the
    # Azure source; the authorizer -- not the static allowlist -- enforces scope.
    builder.allow_azure_resources(prof["azure_resources"])
    builder.authorize_token(prof["gated_actions"], authorize_azure)
