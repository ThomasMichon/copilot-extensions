"""Strict, opt-in repository observation identity and finite source contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

PROTOCOL = "agent-pull-requests.repository-cache/v1"
STORAGE_EXTENSION = "repository_cache_version:1"
MAX_TARGETS = 16
MAX_PULLS = 200
MAX_PAGES = 4
MAX_EVENTS = 4096
MAX_REGISTRATIONS = 128
MAX_CACHE_BYTES = 16 * 1024 * 1024


class RepositoryBackpressure(ValueError):
    """Unsettled state cannot be evicted to admit another projection."""


@dataclass(frozen=True)
class RepositoryIdentity:
    namespace: str
    provider: str
    endpoint: str
    account: str
    repo: str

    def __post_init__(self) -> None:
        if self.provider not in ("github", "gitea", "azure-devops"):
            raise ValueError("repository cache provider unsupported")
        if any(not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value,
        ) for value in (self.namespace, self.account)):
            raise ValueError("invalid repository namespace or account")
        if not isinstance(self.repo, str) or (
            self.repo.count("/") != 1 or len(self.repo) > 256
            or any(not part or part in (".", "..") for part in self.repo.split("/"))
            or any(ord(char) < 32 or ord(char) == 127 for char in self.repo)
        ) or self.provider != "azure-devops" and not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repo,
        ):
            raise ValueError("invalid exact repository")
        address = urlsplit(self.endpoint)
        if (
            address.scheme != "https" or not address.hostname
            or address.username is not None or address.password is not None
            or address.query or address.fragment or self.endpoint.endswith("/")
            or any(part in (".", "..") for part in address.path.split("/"))
        ):
            raise ValueError("repository endpoint requires a canonical HTTPS origin/base")
        address.port

    def document(self) -> dict[str, str]:
        return {
            "namespace": self.namespace, "provider": self.provider, "endpoint": self.endpoint,
            "account": self.account, "repo": self.repo,
        }

    @property
    def key(self) -> str:
        return hashlib.sha256(json.dumps(
            self.document(), sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()

    @classmethod
    def parse(cls, value: object) -> RepositoryIdentity:
        if not isinstance(value, dict) or set(value) != {
            "namespace", "provider", "endpoint", "account", "repo",
        }:
            raise ValueError("invalid full repository identity")
        return cls(**value)


def capability() -> dict:
    """Source seam declaration, not a running host capability advertisement."""
    return {
        "protocol": PROTOCOL, "storage_extension": STORAGE_EXTENSION,
        "native_inventory": "agent-worktrees.pr-inventory/v1",
        "native_snapshot": "agent-worktrees.pr-snapshot/v1",
        "effects": "none",
    }
