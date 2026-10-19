"""Scoped, non-persistent HTTP injection over existing credential sources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol
from urllib.parse import urlsplit

from .sources import CredentialSource


@dataclass(frozen=True)
class HttpAuthContext:
    origin: str
    identity: str
    generation: str
    resource: str = ""
    scope: str = ""


class HttpAuthInjector(Protocol):
    async def headers(self, context: HttpAuthContext) -> Mapping[str, str]: ...

    async def invalidate(self, context: HttpAuthContext) -> None: ...


def format_token_header(header: str, template: str, token: str) -> dict[str, str]:
    value = template.format(token=token)
    if (
        not header
        or any(c in header for c in "\r\n: ")
        or any(c in value for c in "\r\n")
    ):
        raise ValueError("Invalid credential header")
    return {header: value}


class SourceHttpAuth:
    """Bind an existing source to one endpoint/identity without another cache."""

    def __init__(
        self, source: CredentialSource, context: HttpAuthContext, endpoint: str,
        *, timeout: float = 15.0,
    ) -> None:
        self._source = source
        self._context = context
        self._timeout = timeout
        if not context.identity or context.identity != context.identity.strip():
            raise ValueError("Explicit credential identity is required")
        url = urlsplit(endpoint)
        if (
            context.origin != f"{url.scheme}://{url.netloc.lower()}"
            or context.resource or context.scope
        ):
            raise ValueError("Unsupported HTTP credential scope")
        self._fields = {
            "protocol": "https" if url.scheme in {"https", "wss"} else "http",
            "host": url.netloc,
            "path": url.path.lstrip("/"),
            "username": context.identity,
        }
        if any("\n" in v or "\r" in v for v in self._fields.values()):
            raise ValueError("Invalid credential context")

    def _check(self, context: HttpAuthContext) -> None:
        if context != self._context:
            raise PermissionError("Credential context mismatch")

    async def headers(self, context: HttpAuthContext) -> Mapping[str, str]:
        self._check(context)
        if not self._source.supports("get", self._fields):
            raise PermissionError("Credential source declined")
        raw = await self._source.resolve(
            "get", dict(self._fields), timeout=self._timeout,
        )
        fields = dict(
            line.split("=", 1) for line in (raw or "").splitlines() if "=" in line
        )
        token = fields.get("token") or fields.get("password")
        if fields.get("username", context.identity) != context.identity:
            raise PermissionError("Credential identity mismatch")
        if not token:
            raise PermissionError("Credential source unavailable")
        scheme = "Bear" + "er "
        return format_token_header("Authorization", scheme + "{token}", token)

    async def invalidate(self, context: HttpAuthContext) -> None:
        self._check(context)
        # This adapter has no cache. Never erase the host's stored credential
        # merely because a remote server refused one request.
