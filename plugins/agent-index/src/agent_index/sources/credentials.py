"""Explicit renewable source credentials; no ambient or cached fallback."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from agent_index.indexing.engine import SourceSpec

TokenProvider = Callable[[], str]


class SourceCredentialError(RuntimeError):
    """Static errors safe to expose through indexing task diagnostics."""


def validate_auth_selection(auth: dict) -> None:
    selected = [key for key in ("gateway", "relay") if key in auth]
    if len(selected) > 1 or any(
        not isinstance(auth[key], str) or not auth[key].strip()
        or not Path(auth[key]).is_absolute()
        for key in selected
    ):
        raise SourceCredentialError("source-provider-policy-invalid")


def _relay_destination(spec: SourceSpec) -> str:
    if spec.type == "github":
        return "github.com"
    if spec.type != "git" or not spec.remote_url:
        raise SourceCredentialError("source-materialization-policy-required")
    https_origin(spec.remote_url)
    target = urlsplit(spec.remote_url)
    if target.port not in (None, 443):
        raise SourceCredentialError("source-provider-destination-invalid")
    return target.hostname


def http_source_credentials(value: dict) -> dict:
    """Fixed source-HTTP seam; caller capabilities never come from local files."""
    keys = {"source", "account", "recipient", "host", "action", "capability"}
    if not isinstance(value, dict) or set(value) != keys:
        raise SourceCredentialError("source-provider-policy-invalid")
    capability = value["capability"]
    if not isinstance(capability, str) or not 32 <= len(capability) <= 2048:
        raise SourceCredentialError("source-capability-required")
    from agent_index.indexing.engine import configured_source_specs
    from agent_index.source_account_relay import exchange_for

    matches = [
        spec for spec in configured_source_specs()
        if spec.name == value["source"] and spec.auth_account == value["account"]
        and spec.auth_relay is not None and spec.auth_gateway is None
    ]
    if len(matches) != 1:
        raise SourceCredentialError("source-provider-policy-invalid")
    fields = {key: item for key, item in value.items() if key != "capability"}
    try:
        token = exchange_for(
            Path(matches[0].auth_relay), fields, capability, host=_relay_destination(matches[0]),
        )
    except (OSError, ValueError, TypeError, RuntimeError):
        raise SourceCredentialError("source-credential-unavailable") from None
    return {"token": token, "account": value["account"], "host": value["host"]}


def gateway_connector_kwargs(spec: SourceSpec) -> dict[str, object]:
    if (
        (spec.auth_gateway is None) == (spec.auth_relay is None)
        or not spec.auth_account or spec.type not in ("git", "github")
    ):
        raise SourceCredentialError("source-provider-policy-invalid")
    try:
        if spec.auth_relay is not None:
            from agent_index.source_account_relay import token_provider
        else:
            from fleet_manager.gateway_worker import token_provider
    except ImportError:
        raise SourceCredentialError("source-provider-dependency-unavailable") from None
    kwargs = {}
    if spec.auth_relay is not None:
        kwargs["host"] = _relay_destination(spec)
    provider = token_provider(
        Path(spec.auth_relay if spec.auth_relay is not None else spec.auth_gateway),
        source=spec.name, account=spec.auth_account,
        **kwargs,
    )
    if spec.type == "github":
        return {"token_provider": provider}
    if not spec.repo_path or not spec.remote_url:
        raise SourceCredentialError("source-materialization-policy-required")
    from agent_index.sources.git_repo import GitRepoConnector

    connector = GitRepoConnector.materialize(
        repo_path=Path(spec.repo_path), remote_url=spec.remote_url,
        token_provider=provider, ref=spec.ref,
    )
    if connector.source_name != spec.name:
        raise SourceCredentialError("source-materialization-identity-mismatch")
    return {
        "repo_path": str(connector.repo_path), "token_provider": provider,
        **({"ref": spec.ref} if spec.ref else {}),
    }


def acquire_token(provider: TokenProvider) -> str:
    # Keep provider diagnostics out of the task exception's traceback and context.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="index-source-credential") as executor:
        result = executor.submit(copy_context().run, provider)
        if isinstance(result.exception(), Exception):
            raise SourceCredentialError("source-credential-unavailable") from None
        value = result.result()
    if (
        not isinstance(value, str) or not value or len(value) > 16384
        or any(ord(char) < 33 or ord(char) == 127 for char in value)
    ):
        raise SourceCredentialError("source-credential-invalid")
    return value


def authorization(provider: TokenProvider) -> str:
    scheme = "Bear" + "er "
    return scheme + acquire_token(provider)


def https_origin(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme != "https" or not parts.hostname
        or parts.username is not None or parts.password is not None
        or parts.query or parts.fragment or any(ord(char) < 33 for char in url)
    ):
        raise SourceCredentialError("source-credential-https-required")
    try:
        parts.port
    except ValueError:
        raise SourceCredentialError("source-credential-https-required") from None
    return f"https://{parts.netloc}"
