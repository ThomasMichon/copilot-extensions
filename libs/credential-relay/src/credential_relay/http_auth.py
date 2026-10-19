"""Scoped, non-persistent HTTP injection over existing credential sources."""

from __future__ import annotations

import re
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
    """Bind an existing source to one endpoint/identity without another cache.

    ``scoped`` opts an already-approved source into receiving resource/scope
    fields; the caller is responsible for choosing a source that honors them.
    ``scheme`` is a single HTTP auth token, not a header name or template.
    """

    def __init__(
        self, source: CredentialSource, context: HttpAuthContext, endpoint: str,
        *, timeout: float = 15.0, scheme: str = "Bearer", scoped: bool = False,
    ) -> None:
        if not isinstance(scheme, str) or not re.fullmatch(
            r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+", scheme,
        ):
            raise ValueError("Invalid HTTP credential scheme")
        self._source = source
        self._context = context
        self._timeout = timeout
        self._scheme = scheme
        if not context.identity or context.identity != context.identity.strip():
            raise ValueError("Explicit credential identity is required")
        url = urlsplit(endpoint)
        if (
            context.origin != f"{url.scheme}://{url.netloc.lower()}"
            or (not scoped and (context.resource or context.scope))
        ):
            raise ValueError("Unsupported HTTP credential scope")
        self._fields = {
            "protocol": "https" if url.scheme in {"https", "wss"} else "http",
            "host": url.netloc,
            "path": url.path.lstrip("/"),
            "username": context.identity,
        }
        if scoped:
            self._fields.update(resource=context.resource, scope=context.scope)
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
        scheme = self._scheme + " "
        return format_token_header("Authorization", scheme + "{token}", token)

    async def invalidate(self, context: HttpAuthContext) -> None:
        self._check(context)
        # This adapter has no cache. Never erase the host's stored credential
        # merely because a remote server refused one request.
