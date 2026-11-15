"""Generated project identity reader; source: agent_worktrees.project_state."""

from __future__ import annotations

import hashlib
import json
import stat
import time

from dataclasses import dataclass
from pathlib import Path

from urllib.parse import urlsplit, urlunsplit

from remote_identity import normalize_remote

IDENTITY_SCHEMA = "copilot-extensions.repository-identity"

IDENTITY_VERSION = 1

@dataclass(frozen=True)
class RepositoryIdentity:
    """Stable identity and state location for one registered repository."""

    project: str
    repository_id: str
    normalized_remote: str
    repository_root: Path
    state_root: Path
    marketplace_id: str

def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )

def _normalize_repository_remote(project: str, remote: str) -> str:
    if not isinstance(remote, str):
        raise ValueError(f"repository {project!r} has no usable remote")
    raw_remote = remote.strip()
    try:
        parsed = urlsplit(raw_remote)
        default_port = {
            "http": 80,
            "https": 443,
            "ssh": 22,
            "git": 9418,
        }.get(parsed.scheme.casefold())
        if default_port is not None and parsed.port == default_port:
            host = parsed.hostname or ""
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            if parsed.username:
                host = f"{parsed.username}@{host}"
            raw_remote = urlunsplit(
                (parsed.scheme, host, parsed.path, parsed.query, parsed.fragment)
            )
    except ValueError:
        pass
    normalized = normalize_remote(raw_remote)
    if not normalized:
        raise ValueError(
            f"repository {project!r} has no stable remote identity"
        )
    if normalized.startswith("file-relative:"):
        raise ValueError(
            f"repository {project!r} remote is not an absolute stable identity"
        )
    if normalized.startswith("network:github.com/"):
        normalized = normalized.casefold()
    return normalized

def _repository_id(normalized: str) -> str:
    return "repo-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]

def project_identity(project: str, remote: str, context: dict) -> RepositoryIdentity:
    if context is None:
        raise ValueError("repository identity requires namespaced context")
    normalized = _normalize_repository_remote(project, remote)
    repository_id = _repository_id(normalized)
    try:
        cell_root = Path(context["cellRoot"])
        marketplace_id = str(context["marketplaceId"])
    except (KeyError, TypeError) as error:
        raise ValueError(
            "installation context omitted repository identity roots"
        ) from error
    if not cell_root.is_absolute() or not marketplace_id:
        raise ValueError("installation context has invalid repository identity")
    repository_root = cell_root / "repos" / repository_id
    return RepositoryIdentity(
        project=project,
        repository_id=repository_id,
        normalized_remote=normalized,
        repository_root=repository_root,
        state_root=repository_root / "agent-worktrees",
        marketplace_id=marketplace_id,
    )

def _receipt(identity: RepositoryIdentity) -> dict:
    return {
        "schema": IDENTITY_SCHEMA,
        "version": IDENTITY_VERSION,
        "repositoryId": identity.repository_id,
        "normalizedRemote": identity.normalized_remote,
        "marketplaceId": identity.marketplace_id,
        "displayName": identity.project,
    }

def _validate_receipt(
    identity: RepositoryIdentity,
    *,
    retries: int = 0,
) -> None:
    path = identity.repository_root / "identity.json"
    if _is_link_or_reparse(path):
        raise ValueError(f"repository identity receipt is not a regular file: {path}")
    value = None
    error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            error = None
            break
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            error = exc
            if attempt < retries:
                time.sleep(0.01)
    if error is not None:
        raise ValueError(f"repository identity receipt is unreadable: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"repository identity receipt is invalid: {path}")
    expected = _receipt(identity)
    for key in (
        "schema",
        "version",
        "repositoryId",
        "normalizedRemote",
        "marketplaceId",
    ):
        if value.get(key) != expected[key]:
            raise ValueError(
                f"repository identity receipt mismatch for {identity.project!r}: {key}"
            )
