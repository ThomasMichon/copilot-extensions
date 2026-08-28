"""Credential-relay source injection for local Docker container targets.

agent-bridge discovers this hook (see ``agent_bridge.agent_registry``) and calls
``register_relay`` so agent-containers can contribute the credential sources its
container targets need. Trusted containers reach the host relay through an SSH
``-R`` loopback forward. The Azure token action remains gated behind a
per-container secret because the relay's request-authorization policy is shared
across providers, not because the endpoint is host-network reachable.

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

from .config import RUNTIME_DIR, STATE_DIR
from .private_state import atomic_write_json, enforce_mode, ensure_private_dir

log = logging.getLogger("agent-containers.relay")

# Per-container relay tokens live in a host file so the in-bridge relay validator
# and the (separate-process) ``agent-containers exec`` transport wrapper agree on
# which secrets are valid. Mirrors the lease-file pattern.
_TOKENS_FILE = STATE_DIR / "relay-tokens.json"
_LEGACY_TOKENS_FILE = RUNTIME_DIR / "relay-tokens.json"

# Azure scopes the relay may mint tokens for, when config is unavailable.
# "*" = any scope, gated behind the per-container secret (mirrors
# agent-codespaces). `rush dev-deploy`'s user-delegation-SAS flow and the build
# cache request storage scopes (e.g. https://storage.azure.com/.default), but
# forwarding any scope verbatim keeps the shim a faithful broker.
DEFAULT_AZURE_RESOURCES = ["*"]

# Actions gated behind the per-container secret. The gate is request
# authorization on the shared relay; SSH -R removes the old host-network
# exposure but does not weaken the Azure-token policy.
_GATED_ACTIONS = ["get-azure-token"]

_lock = threading.Lock()


def _read_tokens() -> dict[str, str]:
    path = _prepare_token_file()
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_tokens(data: dict[str, str]) -> None:
    atomic_write_json(_prepare_token_file(), data)


def _token_file() -> Path:
    """Use relocated state, while preserving an existing legacy token store."""
    if _TOKENS_FILE.exists() or _TOKENS_FILE == _LEGACY_TOKENS_FILE:
        return _TOKENS_FILE
    if _LEGACY_TOKENS_FILE.exists():
        return _LEGACY_TOKENS_FILE
    return _TOKENS_FILE


def _prepare_token_file() -> Path:
    path = _token_file()
    ensure_private_dir(path.parent)
    if path.exists():
        enforce_mode(path, 0o600)
    return path


def _validate(token: str) -> bool:
    """Relay token validator: is ``token`` a known per-container secret?"""
    if not token:
        return False
    values = _read_tokens().values()
    return any(secrets.compare_digest(token, t) for t in values)


def relay_profile() -> dict:
    """The declarative container relay profile agent-bridge applies over a
    process boundary (#892 Inc 2).

    Emits the same policy :func:`register_relay` applies in-process -- sources,
    the Azure resource allowlist, the token-gated actions, and the **token-store
    file path** -- as plain JSON so agent-bridge can apply it with a file-backed
    validator instead of importing this module. Derived from the same config so
    the CLI path and the in-process fallback stay in lockstep.
    """
    resources = DEFAULT_AZURE_RESOURCES
    try:
        from .config import load_config

        cfg = load_config()
        resources = getattr(cfg, "relay_azure_resources", None) or DEFAULT_AZURE_RESOURCES
    except Exception:  # pragma: no cover - config optional
        log.debug("containers relay config unavailable; using defaults")

    return {
        "sources": ["git-credential", "gh-auth"],
        "port": None,
        "ado_host": None,
        "azure_resources": list(resources),
        "gated_actions": list(_GATED_ACTIONS),
        "token_store": str(_prepare_token_file()),
    }


def register_relay(builder) -> None:
    """Inject the container credential-relay profile into ``builder``.

    ``builder`` is a :class:`credential_relay.registry.RelayBuilder`. Applies the
    same profile :func:`relay_profile` emits, but with the **in-process** token
    validator -- the degrade-safe fallback agent-bridge uses when the
    ``agent-containers relay-profile`` CLI seam is unavailable (#892 Inc 2).
    """
    from credential_relay.sources.gh_auth import GhAuthSource
    from credential_relay.sources.git_credential import GitCredentialSource

    prof = relay_profile()

    # Generic host-credential sources (deduped against codespaces by name).
    builder.add_source(GitCredentialSource())
    builder.add_source(GhAuthSource())
    # Contribute container Azure resources to the merged allowlist (the builder
    # constructs a single AzLoginSource from the union across providers).
    builder.allow_azure_resources(prof["azure_resources"])

    # Gate Azure token minting behind the per-container shared secret (file-backed
    # so the separate-process exec wrapper and the relay agree).
    builder.require_token(prof["gated_actions"], _validate)
    log.info(
        "Injected container relay profile (az resources=%s, gated=%s)",
        prof["azure_resources"], prof["gated_actions"],
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
