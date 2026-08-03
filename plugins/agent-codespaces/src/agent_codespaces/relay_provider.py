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


def register_relay(builder) -> None:
    """Inject the codespace credential-relay profile into ``builder``.

    ``builder`` is a :class:`credential_relay.registry.RelayBuilder`.
    """
    from credential_relay.sources.git_credential import GitCredentialSource

    from .relay_token import validate as _validate_codespace_token

    builder.add_source(GitCredentialSource())

    azure_resources = set(DEFAULT_AZURE_RESOURCES)

    # Honor the configured relay_port + ado_host from codespaces.yaml. The port
    # must match what the SSH tunnel forwards; the ado_host lets host-less
    # ``ado-auth-helper get-access-token`` (no scope) requests resolve a default
    # ADO host instead of being rejected (#64). Additional Azure resources may be
    # opted in explicitly through credentials.sources.az-login.allowed_resources;
    # the default remains the narrow ADO REST audience, never an internal org.
    try:
        from .config import load_merged_config

        creds = load_merged_config().credentials
        builder.set_port(creds.relay_port)
        builder.set_ado_host(creds.ado_host)
        az_cfg = creds.sources.get("az-login")
        if az_cfg and az_cfg.enabled:
            azure_resources.update(az_cfg.allowed_resources)
    except Exception:  # pragma: no cover - config optional
        log.debug("codespaces relay config unavailable; using relay defaults")

    # Raw Azure/Entra bearer minting stays policy-gated: by default the
    # CodeSpace can ask only for the public ADO REST resource, plus any resources
    # explicitly configured by the adopting repo. The shared relay also serves
    # network-reachable targets, so get-azure-token must stay behind a
    # per-codespace token even though CodeSpaces reach it through an SSH tunnel.
    builder.allow_azure_resources(sorted(azure_resources))
    builder.require_token(["get-azure-token"], _validate_codespace_token)
