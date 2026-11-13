"""Read-only credential consumption; issuance and downscoping remain external."""

from __future__ import annotations

import base64
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit

from .base import ProviderError

MAX_SNAPSHOT_REQUESTS = 64
HTTP_PROFILES = ("legacy", "direct-v1")


class ForgeBinding(Protocol):
    @property
    def provider(self) -> str: ...
    @property
    def api_base(self) -> str: ...
    @property
    def repository(self) -> str: ...
    @property
    def permission(self) -> str: ...
    @property
    def authentication(self) -> str: ...


class ObservationCredential(Protocol):
    @property
    def token(self) -> str: ...
    @property
    def expires_at(self) -> float: ...
    @property
    def forge(self) -> ForgeBinding | None: ...


CredentialSource = Callable[[], ObservationCredential]


@dataclass(frozen=True)
class _ProfiledSource:
    source: CredentialSource = field(repr=False)
    http_profile: str

    def __call__(self) -> ObservationCredential:
        return self.source()


def with_http_profile(source: CredentialSource, profile: str) -> CredentialSource:
    """Request a transport, not credential verification or effect authority."""
    if profile not in HTTP_PROFILES or not callable(source):
        raise ValueError("unsupported native observation HTTP profile")
    return source if profile == "legacy" else _ProfiledSource(source, profile)


class ObservationCredentialError(ProviderError):
    """Abort the entire observation, including otherwise best-effort reads."""


class ObservationCredentials:
    def __init__(
        self, source: CredentialSource, *, provider: str, api_base: str, repository: str,
        max_requests: int | None = None,
    ):
        if max_requests is not None and (
            type(max_requests) is not int or not 1 <= max_requests <= 128
        ):
            raise ValueError("invalid native observation request budget")
        self.source = source
        self.provider, self.api_base, self.repository = provider, api_base, repository
        self.max_requests, self.requests = max_requests, 0
        self.http_profile = getattr(source, "http_profile", "legacy")
        if self.http_profile not in HTTP_PROFILES or (
            self.http_profile != "legacy" and provider not in ("github", "gitea")
        ):
            raise ValueError("unsupported native observation HTTP profile")

    def acquire(self) -> ObservationCredential:
        if self.max_requests is not None and self.requests >= self.max_requests:
            raise ObservationCredentialError("native observation request budget exhausted", transient=True)
        self.requests += 1
        try:
            material = self.source()
        except (OSError, RuntimeError, ValueError):
            # Provider errors may contain secrets; expose only the retryable loss.
            raise ObservationCredentialError(
                "PR observation credential source unavailable", transient=True,
            ) from None
        binding = getattr(material, "forge", None)
        authentication = getattr(binding, "authentication", None)
        if (
            getattr(binding, "provider", None) != self.provider
            or getattr(binding, "api_base", None) != self.api_base
            or getattr(binding, "repository", None) != self.repository
            or getattr(binding, "permission", None) != "observe"
            or authentication not in (
                ("bearer",) if self.provider in ("github", "gitea") else ("basic", "bearer")
            )
        ):
            raise ObservationCredentialError("PR observation credential binding mismatch")
        token, expiry = getattr(material, "token", None), getattr(material, "expires_at", None)
        if (
            not isinstance(token, str) or not token or len(token) > 16384 or not token.isascii()
            or any(character.isspace() or not character.isprintable() for character in token)
            or type(expiry) not in (int, float)
            or type(expiry) is float and not math.isfinite(expiry)
        ):
            raise ObservationCredentialError("PR observation credential material is invalid")
        if expiry <= time.time():
            raise ObservationCredentialError("PR observation credential expired", transient=True)
        return material

    def authorization(self) -> str:
        material = self.acquire()
        binding = material.forge
        if binding is None:
            raise ObservationCredentialError("PR observation credential binding mismatch")
        if binding.authentication == "basic":
            scheme = "Bas" + "ic "
            value = base64.b64encode((":" + material.token).encode()).decode("ascii")
        else:
            scheme = "Bear" + "er "
            value = material.token
        return scheme + value


def github_endpoint(host: str) -> tuple[str, str]:
    """Pin gh's supported HTTPS REST routes; its development host is HTTP-only."""
    parsed = urlsplit("https://" + host)
    hostname = parsed.hostname
    if not hostname:
        raise ObservationCredentialError("GitHub observation API hostname is unavailable")
    if hostname == "github.localhost":
        raise ObservationCredentialError("Scoped GitHub observation requires an HTTPS API endpoint")
    authority = f"[{hostname}]" if ":" in hostname else hostname
    if parsed.port not in (None, 443):
        authority += f":{parsed.port}"
    if hostname == "github.com" or hostname.endswith(".ghe.com"):
        return hostname, f"https://api.{authority}"
    return hostname, f"https://{authority}/api/v3"


def github_request(argv: list[str], api_base: str) -> list[str]:
    """Absolute URLs bypass gh's inherited ``api_host`` routing override."""
    return [*argv[:-1], api_base + argv[-1]] if api_base else argv
