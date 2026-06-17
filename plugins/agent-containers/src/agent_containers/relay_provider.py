"""Credential-relay source injection for local Docker container targets.

agent-bridge discovers this hook (see ``agent_bridge.agent_registry``) and calls
``register_relay`` so agent-containers can contribute the credential sources its
container targets need. Unlike codespaces (auth forwarded over an isolated SSH
tunnel), containers reach the host relay over ``host.docker.internal`` -- network
reachable -- so the Azure token action is gated behind a per-container secret
(see :data:`SESSION_TOKENS` / :func:`token_for`).

The relay itself is owned/run by agent-bridge; this module only injects the
container profile (sources + storage-resource allowlist + token gate).
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
from pathlib import Path

from credential_relay import TokenRegistry

log = logging.getLogger("agent-containers.relay")

# Per-container relay tokens live in a host file so the in-bridge relay validator
# and the (separate-process) ``agent-containers exec`` transport wrapper agree on
# which secrets are valid. Mirrors the lease-file pattern.
_TOKENS_FILE = Path.home() / ".agent-containers" / "relay-tokens.json"

# Azure resources the relay may mint tokens for. Storage is what `rush
# dev-deploy` needs (blob uploads via user-delegation SAS).
DEFAULT_AZURE_RESOURCES = ["https://storage.azure.com/"]

# Actions gated behind the per-container secret. Only the NEW Azure action is
# gated: codespaces never calls get-azure-token, so its (shared) relay path is
# unaffected. ADO get-access-token stays ungated to preserve codespaces; the
# Phase-B Unix-socket transport removes the network exposure entirely.
_GATED_ACTIONS = ["get-azure-token"]

_lock = threading.Lock()


def _read_tokens() -> dict[str, str]:
    try:
        return json.loads(_TOKENS_FILE.read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_tokens(data: dict[str, str]) -> None:
    _TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _TOKENS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(_TOKENS_FILE)


def _validate(token: str) -> bool:
    """Relay token validator: is ``token`` a known per-container secret?"""
    if not token:
        return False
    values = _read_tokens().values()
    return any(secrets.compare_digest(token, t) for t in values)


def register_relay(builder) -> None:
    """Inject the container credential-relay profile into ``builder``.

    ``builder`` is a :class:`credential_relay.registry.RelayBuilder`.
    """
    from credential_relay.sources.gh_auth import GhAuthSource
    from credential_relay.sources.git_credential import GitCredentialSource

    resources = DEFAULT_AZURE_RESOURCES
    try:
        from .config import load_config

        cfg = load_config()
        resources = getattr(cfg, "relay_azure_resources", None) or DEFAULT_AZURE_RESOURCES
    except Exception:  # pragma: no cover - config optional
        log.debug("containers relay config unavailable; using defaults")

    # Generic host-credential sources (deduped against codespaces by name).
    builder.add_source(GitCredentialSource())
    builder.add_source(GhAuthSource())
    # Contribute container Azure resources to the merged allowlist (the builder
    # constructs a single AzLoginSource from the union across providers).
    builder.allow_azure_resources(list(resources))

    # Gate Azure token minting behind the per-container shared secret (file-backed
    # so the separate-process exec wrapper and the relay agree).
    builder.require_token(_GATED_ACTIONS, _validate)
    log.info(
        "Injected container relay profile (az resources=%s, gated=%s)",
        resources, _GATED_ACTIONS,
    )


def token_for(container: str) -> str:
    """Return the per-container relay secret, minting + persisting on first use.

    One stable token per container (reused across dispatches), persisted to
    :data:`_TOKENS_FILE` so both the relay validator and the exec wrapper see it.
    """
    with _lock:
        tokens = _read_tokens()
        tok = tokens.get(container)
        if tok is None:
            tok = TokenRegistry.mint()
            tokens[container] = tok
            _write_tokens(tokens)
            log.info("Minted relay token for container '%s'", container)
        return tok


def revoke(container: str) -> None:
    """Discard a container's relay token (e.g. when the container is removed)."""
    with _lock:
        tokens = _read_tokens()
        if tokens.pop(container, None) is not None:
            _write_tokens(tokens)
            log.info("Revoked relay token for container '%s'", container)
