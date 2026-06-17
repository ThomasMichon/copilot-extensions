"""Per-codespace relay tokens for gating ``get-azure-token`` on the shared relay.

The credential relay (run by agent-bridge) is shared by all providers. The
agent-containers provider gates ``get-azure-token`` behind a per-container secret
because containers reach the relay over a network-reachable address. CodeSpaces
reach it over an SSH ``-R`` tunnel (isolated), but the gate is global once any
provider enables it -- so the codespace path must present its own token too.

This module mints one stable token per codespace, persisted to a host file so
the in-bridge relay validator and the (separate-process) ``agent-codespaces
ssh`` transport agree on which secrets are valid. Mirrors
``agent_containers.relay_provider``'s token store.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading

from .config import RUNTIME_DIR

log = logging.getLogger("agent-codespaces.relay")

# Per-codespace relay tokens live in a host file shared between the in-bridge
# relay validator and the separate ``agent-codespaces ssh`` process.
_TOKENS_FILE = RUNTIME_DIR / "relay-tokens.json"

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


def validate(token: str) -> bool:
    """Relay token validator: is ``token`` a known per-codespace secret?"""
    if not token:
        return False
    values = _read_tokens().values()
    return any(secrets.compare_digest(token, t) for t in values)


def token_for(codespace: str) -> str:
    """Return the per-codespace relay secret, minting + persisting on first use.

    One stable token per codespace (reused across connections), persisted to
    :data:`_TOKENS_FILE` so both the relay validator and the SSH transport see
    it.
    """
    with _lock:
        tokens = _read_tokens()
        tok = tokens.get(codespace)
        if tok is None:
            tok = secrets.token_hex(32)
            tokens[codespace] = tok
            _write_tokens(tokens)
            log.info("Minted relay token for codespace '%s'", codespace)
        return tok


def revoke(codespace: str) -> None:
    """Discard a codespace's relay token (e.g. when it is deleted)."""
    with _lock:
        tokens = _read_tokens()
        if tokens.pop(codespace, None) is not None:
            _write_tokens(tokens)
            log.info("Revoked relay token for codespace '%s'", codespace)
