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
import time
from contextlib import contextmanager
from typing import Any

from credential_relay.token_store import read_token_store, retry_token_store_io
from dropin_registry import atomic_write_text
from single_instance_lease import OperationLock, OperationLockedError

from .config import RUNTIME_DIR

log = logging.getLogger("agent-codespaces.relay")

# Per-codespace relay tokens live in a host file shared between the in-bridge
# relay validator and the separate ``agent-codespaces ssh`` process.
_TOKENS_FILE = RUNTIME_DIR / "relay-tokens.json"

_lock = threading.Lock()


def _read_tokens() -> dict[str, Any]:
    return read_token_store(_TOKENS_FILE)


def _write_tokens(data: dict[str, Any]) -> None:
    retry_token_store_io(lambda: atomic_write_text(_TOKENS_FILE, json.dumps(data)))


@contextmanager
def _transaction():
    with _lock:
        _TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
        lock = OperationLock(_TOKENS_FILE.with_suffix(".json.lock").resolve())
        deadline = time.monotonic() + 5.0
        while True:
            try:
                retry_token_store_io(lock.acquire)
                break
            except OperationLockedError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("relay token store is busy") from None
                time.sleep(min(0.02, remaining))
        try:
            yield
        finally:
            lock.release()


def _validation_tokens() -> dict[str, Any]:
    try:
        return _read_tokens()
    except (OSError, ValueError, UnicodeError):
        return {}


def _token_value(entry: Any) -> str:
    """Read the secret from a structured entry or a legacy ``codespace: token``."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return str(entry.get("token", ""))
    return ""


def validate(token: str) -> bool:
    """Relay token validator: is ``token`` a known per-codespace secret?"""
    if not token:
        return False
    return any(
        secrets.compare_digest(token, _token_value(entry))
        for entry in _validation_tokens().values()
    )


def authorize_azure(token: str, action: str, fields: dict[str, str]) -> bool:
    """Authorize a ``get-azure-token`` request against the token's scope policy.

    The request-scoped :meth:`RelayBuilder.authorize_token` gate calls this with
    the presented ``token``, the ``action``, and the request ``fields``. The
    token is authorized only if it is a known per-codespace secret AND the
    requested scope/resource is in the ``allowed_resources`` recorded for that
    codespace when the token was minted (see :func:`token_for`). Legacy
    string-only entries carry no allowlist and are therefore denied for scoped
    minting -- they must be re-minted (structured) to gain Azure-token access.

    Raises :class:`credential_relay.server.ScopeDenied` -- rather than simply
    returning ``False`` -- when the token IS a recognized per-codespace secret
    but the specific resource/scope is not in its allowlist (#4367): the relay
    gate turns this into an explicit, wire-visible denial for a caller who
    already holds a valid token, while an unrecognized/garbage token still
    returns a plain ``False`` and stays fully silent over the wire.
    """
    if action != "get-azure-token" or not token:
        return False
    from credential_relay.server import ScopeDenied

    requested = fields.get("scope") or fields.get("resource") or ""
    normalized = requested.removesuffix("/.default").rstrip("/")
    for entry in _validation_tokens().values():
        if not secrets.compare_digest(token, _token_value(entry)):
            continue
        if not isinstance(entry, dict):
            raise ScopeDenied  # legacy string-only entry: no allowlist at all
        allowed = {
            str(value).removesuffix("/.default").rstrip("/")
            for value in entry.get("allowed_resources", [])
        }
        if "*" in allowed or normalized in allowed:
            return True
        raise ScopeDenied
    return False


def token_for(
    codespace: str, *, repository: str | None = None,
    allowed_resources: list[str] | None = None,
) -> str:
    """Return the per-codespace relay secret, minting + persisting on first use.

    One stable token per codespace (reused across connections), persisted to
    :data:`_TOKENS_FILE` so both the relay validator and the SSH transport see
    it. ``repository`` and ``allowed_resources`` record the per-token scope
    policy :func:`authorize_azure` enforces; they are refreshed on each call so
    a re-mint with updated config re-scopes an existing token.
    """
    with _transaction():
        tokens = _read_tokens()
        raw_entry = tokens.get(codespace)
        entry = raw_entry if isinstance(raw_entry, dict) else {}
        tok = _token_value(raw_entry)
        if not tok:
            tok = secrets.token_hex(32)
            log.info("Minted relay token for codespace '%s'", codespace)
        tokens[codespace] = {
            "token": tok,
            "repository": repository or entry.get("repository"),
            "allowed_resources": list(
                allowed_resources or entry.get("allowed_resources", [])
            ),
        }
        _write_tokens(tokens)
        return tok


def revoke(codespace: str) -> None:
    """Discard a codespace's relay token (e.g. when it is deleted)."""
    with _transaction():
        tokens = _read_tokens()
        if tokens.pop(codespace, None) is not None:
            _write_tokens(tokens)
            log.info("Revoked relay token for codespace '%s'", codespace)
