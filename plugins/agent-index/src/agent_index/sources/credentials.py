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


def gateway_connector_kwargs(spec: SourceSpec) -> dict[str, object]:
    if spec.auth_gateway is None or not spec.auth_account or spec.type not in ("git", "github"):
        raise SourceCredentialError("source-provider-policy-invalid")
    try:
        from fleet_manager.gateway_worker import token_provider
    except ImportError:
        raise SourceCredentialError("source-provider-dependency-unavailable") from None
    provider = token_provider(
        Path(spec.auth_gateway), source=spec.name, account=spec.auth_account,
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
