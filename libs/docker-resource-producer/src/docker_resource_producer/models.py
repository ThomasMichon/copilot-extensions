"""Small, credential-free identities; names never replace container IDs."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

LABEL_TOKEN = "copilot-extensions.disposable.reservation"
LABEL_CREATOR = "copilot-extensions.disposable.creator"
LABEL_GENERATION = "copilot-extensions.disposable.generation"
_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,128}\Z")
_CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z")
_VOLUME_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,254}\Z")
_CREATED = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?(?:Z|[+-]\d\d:\d\d)\Z")


class VerificationError(RuntimeError):
    """An identity, response or transport cannot establish safe resource state."""


def text(value: object, field: str, *, limit: int = 1024) -> str:
    """Require a bounded nonempty string without control characters."""
    if (
        not isinstance(value, str) or not value or len(value) > limit
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise VerificationError(f"Invalid {field}")
    return value


def generation(value: object) -> int:
    """Validate the positive allocation epoch without accepting booleans."""
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise VerificationError("Invalid allocation generation")
    return value


def daemon_id(value: object) -> str:
    """Docker daemon IDs are opaque but never blank, controls or arbitrary JSON."""
    value = text(value, "daemon identity", limit=256)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_.-]{0,255}", value) is None:
        raise VerificationError("Invalid daemon identity")
    return value


def birth_labels(token: str, creator: str, epoch: int) -> dict[str, str]:
    """Labels that must be supplied to CREATE, never added after allocation."""
    if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
        raise VerificationError("Invalid reservation token")
    return {
        LABEL_TOKEN: token,
        LABEL_CREATOR: text(creator, "creator"),
        LABEL_GENERATION: str(generation(epoch)),
    }


@dataclass(frozen=True)
class Endpoint:
    """Pinned Docker transport and authoritative daemon identity."""
    host: str
    daemon_id: str
    context: str | None = None
    tls: bool = False
    tls_verify: bool = False
    tls_ca: str | None = None
    tls_cert: str | None = None
    tls_key: str | None = None

    def __post_init__(self) -> None:
        host = text(self.host, "Docker endpoint")
        try:
            parsed = urlsplit(host)
            parsed.port
        except ValueError as error:
            raise VerificationError("Malformed Docker endpoint") from error
        if parsed.scheme not in {"unix", "npipe", "tcp", "ssh"} or parsed.password:
            raise VerificationError("Unsupported or credential-bearing Docker endpoint")
        if parsed.query or parsed.fragment or (
            parsed.scheme in {"tcp", "ssh"} and not parsed.hostname
        ) or (parsed.scheme in {"unix", "npipe"} and not parsed.path):
            raise VerificationError("Incomplete Docker endpoint")
        daemon_id(self.daemon_id)
        if self.context is not None:
            text(self.context, "Docker context", limit=256)
        if type(self.tls) is not bool or type(self.tls_verify) is not bool:
            raise VerificationError("Invalid TLS flags")
        if self.tls_verify and not self.tls:
            raise VerificationError("TLS verification requires TLS")
        if self.tls and parsed.scheme != "tcp":
            raise VerificationError("TLS flags require a TCP endpoint")
        if self.tls_verify and not any((self.tls_ca, self.tls_cert, self.tls_key)):
            raise VerificationError("Verified TLS requires pinned CA or client TLS material")
        if bool(self.tls_cert) != bool(self.tls_key):
            raise VerificationError("TLS certificate and key must be paired")
        for field in ("tls_ca", "tls_cert", "tls_key"):
            value = getattr(self, field)
            if value is not None:
                text(value, field)
                if not self.tls or not Path(value).is_absolute():
                    raise VerificationError(f"{field} requires TLS and an absolute path")

    def to_json(self) -> dict[str, Any]:
        """Serialize transport paths, never certificate or key contents."""
        return asdict(self)

    @classmethod
    def from_json(cls, value: object) -> Endpoint:
        """Reject unknown fields instead of silently changing a pinned endpoint."""
        fields = set(cls.__dataclass_fields__)
        if not isinstance(value, dict) or set(value) != fields:
            raise VerificationError("Invalid pinned endpoint fields")
        return cls(**value)


@dataclass(frozen=True)
class Identity:
    """Immutable birth evidence for one container or uniquely named volume."""
    resource_type: str
    resource_id: str
    created: str
    daemon_id: str
    token: str
    creator: str
    generation: int

    def __post_init__(self) -> None:
        if not isinstance(self.resource_type, str) or self.resource_type not in {"container", "volume"}:
            raise VerificationError("Unsupported Docker resource type")
        pattern = _CONTAINER_ID if self.resource_type == "container" else _VOLUME_NAME
        if not isinstance(self.resource_id, str) or pattern.fullmatch(self.resource_id) is None:
            raise VerificationError("Container identity requires a full ID; volume identity requires a unique name")
        if not isinstance(self.created, str) or _CREATED.fullmatch(self.created) is None:
            raise VerificationError("Invalid creation evidence")
        validation_time = re.sub(
            r"\.(\d{1,9})(?=Z|[+-]\d\d:\d\d\Z)",
            lambda match: "." + match.group(1)[:6].ljust(6, "0"),
            self.created,
        )
        try:
            datetime.fromisoformat(validation_time.replace("Z", "+00:00"))
        except ValueError as error:
            raise VerificationError("Invalid creation evidence") from error
        daemon_id(self.daemon_id)
        birth_labels(self.token, self.creator, self.generation)

    def to_json(self) -> dict[str, Any]:
        """Serialize only the small provider identity."""
        return asdict(self)

    @classmethod
    def from_json(cls, value: object) -> Identity:
        """Validate all persisted identity fields before any Docker operation."""
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise VerificationError("Invalid Docker identity fields")
        return cls(**value)


@dataclass(frozen=True)
class DiagnosticObservation:
    """A CLI snapshot, never an authoritative presence or closeout receipt."""

    identity: Identity
    observed_exists: bool

    def __post_init__(self) -> None:
        if not isinstance(self.identity, Identity) or type(self.observed_exists) is not bool:
            raise VerificationError("Invalid diagnostic observation")

    def __bool__(self) -> bool:
        raise VerificationError("Diagnostic observations cannot authorize presence, absence or cleanup")

    def to_json(self) -> dict[str, Any]:
        """Use distinct fields that cannot masquerade as provider verification."""
        return {
            "authority": "diagnostic-only", "observed_exists": self.observed_exists,
            "identity": self.identity.to_json(),
        }
