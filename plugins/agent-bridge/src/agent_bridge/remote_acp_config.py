"""Explicit experimental remote endpoints, without process/workspace ownership."""

from __future__ import annotations

import ipaddress
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator
from credential_relay.http_sources import HTTP_SOURCE_REFERENCE

PROFILE = "acp-http-ws-durable-v1alpha1"
DRAFT_COMMIT = "ccff4e7d2e431880225804a8c136c2ccfcb313d0"
NAMESPACE = "remote-acp/explicit-approvals"
REMOTE_ACP_VERSION = 1
TOKEN_SCHEME_PATTERN = r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$"
TARGET_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"


def canonical_endpoint(endpoint: str) -> str:
    """Normalize only addressing syntax, never broaden endpoint/path policy."""
    try:
        url = urlsplit(endpoint)
        port = url.port
        host = (url.hostname or "").encode("idna").decode("ascii").lower()
        if (
            url.scheme not in {"https", "http", "wss", "ws"} or not host
            or url.username is not None or url.password is not None
            or url.query or url.fragment or not url.path
            or "\\" in endpoint or any(ord(c) <= 32 or ord(c) == 127 for c in endpoint)
            or port == 0
        ):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r"[a-z0-9.-]+", host):
                raise ValueError from None
        else:
            host = f"[{address.compressed}]" if address.version == 6 else str(address)
        scheme = {"wss": "https", "ws": "http"}.get(url.scheme, url.scheme)
        authority = host if port in (None, 443 if scheme == "https" else 80) else f"{host}:{port}"
        return f"{scheme}://{authority}{url.path}"
    except (ValueError, UnicodeError):
        raise ValueError("Invalid remote endpoint") from None


def is_remote_address(name: str) -> bool:
    return name.startswith("remote-acp:") or "://" in name


class RemoteAuth(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)
    source: Literal["git-credential", "injected", "none"] = "git-credential"
    identity: str = Field(min_length=1, max_length=256, pattern=r"^\S(?:[^\r\n]*\S)?$")
    resource: str = Field(default="", max_length=2048, pattern=r"^[^\r\n]*$")
    scope: str = Field(default="", max_length=2048, pattern=r"^[^\r\n]*$")
    scheme: str = Field(default="Bearer", max_length=64, pattern=TOKEN_SCHEME_PATTERN)
    credential_source: str | None = Field(default=None, max_length=256, pattern=HTTP_SOURCE_REFERENCE)

    @model_validator(mode="after")
    def validate_source_scope(self) -> RemoteAuth:
        if self.source != "injected" and (self.resource or self.scope):
            raise ValueError("Source does not support resource/scope configuration")
        if self.source != "injected" and self.credential_source is not None:
            raise ValueError("Credential source reference requires injected authentication")
        return self


class RemoteAcpTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)
    endpoint: str = Field(max_length=2048)
    profile: Literal["acp-http-ws-durable-v1alpha1"] = PROFILE
    draft_commit: Literal["ccff4e7d2e431880225804a8c136c2ccfcb313d0"] = DRAFT_COMMIT
    transport: Literal["streamable-http", "websocket"]
    application_approvals: Literal["required"] = "required"
    permissions: Literal["manual"] = "manual"
    auth: RemoteAuth
    timeout_seconds: float = Field(default=30, ge=1, le=120)
    idle_seconds: float = Field(default=120, ge=1, le=3600)
    rpc_timeout_seconds: float = Field(default=900, ge=1, le=86400)
    max_frame_bytes: int = Field(default=1024 * 1024, ge=1024, le=8 * 1024 * 1024)
    max_queue_messages: int = Field(default=64, ge=1, le=256)
    allow_loopback_test: bool = False

    @model_validator(mode="after")
    def validate_endpoint(self) -> RemoteAcpTarget:
        canonical_endpoint(self.endpoint)
        url = urlsplit(self.endpoint)
        try:
            port = url.port
        except ValueError:
            raise ValueError("Invalid remote endpoint") from None
        if (
            url.scheme not in {"https", "http"} or not url.hostname
            or url.username is not None or url.password is not None
            or url.query or url.fragment or not url.path
            or any(c.isspace() for c in self.endpoint)
            or port == 0
        ):
            raise ValueError("Invalid remote endpoint")
        loopback = url.hostname.lower() == "localhost"
        try:
            loopback = loopback or ipaddress.ip_address(url.hostname).is_loopback
        except ValueError:
            pass
        if url.scheme != "https" and not (loopback and self.allow_loopback_test):
            raise ValueError("Remote endpoint requires verified TLS")
        if self.auth.source == "none" and not (loopback and self.allow_loopback_test):
            raise ValueError("Remote endpoint requires scoped authentication")
        return self

    @property
    def origin(self) -> str:
        url = urlsplit(self.endpoint)
        return f"{url.scheme}://{url.netloc.lower()}"


class RemoteAuthRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)
    source: Literal["git-credential", "injected"] = "git-credential"
    scheme: str = Field(default="Bearer", max_length=64, pattern=TOKEN_SCHEME_PATTERN)


class RemoteAcpRequirements(BaseModel):
    """Inert plugin policy: no endpoint, identity, credential or local command."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)
    transport: Literal["streamable-http", "websocket"]
    profile: Literal["acp-http-ws-durable-v1alpha1"] = PROFILE
    draft_commit: Literal["ccff4e7d2e431880225804a8c136c2ccfcb313d0"] = DRAFT_COMMIT
    application_approvals: Literal["required"] = "required"
    permissions: Literal["manual"] = "manual"
    auth: RemoteAuthRequirements = Field(default_factory=RemoteAuthRequirements)
    description: str = Field(default="", max_length=512)


class RemoteAcpBinding(BaseModel):
    """Adopter-owned instance binding; a plugin cannot approve itself."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)
    plugin: str = Field(pattern=r"^[^@/\\\s]+@[^@/\\\s]+$")
    endpoint: str = Field(max_length=2048)
    identity: str = Field(min_length=1, max_length=256, pattern=r"^\S(?:[^\r\n]*\S)?$")
    credential_source: str | None = Field(default=None, max_length=256, pattern=HTTP_SOURCE_REFERENCE)
    resource: str = Field(default="", max_length=2048, pattern=r"^[^\r\n]*$")
    scope: str = Field(default="", max_length=2048, pattern=r"^[^\r\n]*$")
    project: str | None = None


class RemoteAuthApproval(BaseModel):
    """Exact non-secret local permission to use a registered HTTP source."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)
    origin: str = Field(max_length=2048)
    identity: str = Field(min_length=1, max_length=256, pattern=r"^\S(?:[^\r\n]*\S)?$")
    resource: str = Field(default="", max_length=2048, pattern=r"^[^\r\n]*$")
    scope: str = Field(default="", max_length=2048, pattern=r"^[^\r\n]*$")
    scheme: str = Field(default="Bearer", max_length=64, pattern=TOKEN_SCHEME_PATTERN)


def capability_metadata() -> dict:
    return {NAMESPACE: {
        "profile": PROFILE,
        "applicationApprovals": {
            "version": 1, "explicitAnswers": True,
        },
    }}


def validate_capabilities(capabilities: object) -> None:
    meta = getattr(capabilities, "field_meta", None) or {}
    selected = meta.get(NAMESPACE, {}) if isinstance(meta, dict) else {}
    approval = selected.get("applicationApprovals", {}) if isinstance(selected, dict) else {}
    if (
        not isinstance(selected, dict) or not isinstance(approval, dict)
        or selected.get("profile") != PROFILE
        or type(approval.get("version")) is not int or approval.get("version") != 1
        or approval.get("required") is not True
    ):
        raise PermissionError("Remote ACP profile/approval capability mismatch")
