"""Explicit experimental remote endpoints, without process/workspace ownership."""

from __future__ import annotations

import ipaddress
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

PROFILE = "acp-http-ws-durable-v1alpha1"
DRAFT_COMMIT = "ccff4e7d2e431880225804a8c136c2ccfcb313d0"
NAMESPACE = "remote-acp/explicit-approvals"
REMOTE_ACP_VERSION = 1


class RemoteAuth(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)
    source: Literal["git-credential", "injected", "none"] = "git-credential"
    identity: str = Field(min_length=1, max_length=256, pattern=r"^\S(?:[^\r\n]*\S)?$")
    resource: str = Field(default="", max_length=2048, pattern=r"^[^\r\n]*$")
    scope: str = Field(default="", max_length=2048, pattern=r"^[^\r\n]*$")

    @model_validator(mode="after")
    def validate_source_scope(self) -> RemoteAuth:
        if self.source != "injected" and (self.resource or self.scope):
            raise ValueError("Source does not support resource/scope configuration")
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
