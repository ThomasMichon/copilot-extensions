"""Cell-qualified per-project state with stable repository identity."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from plugin_activation import normalize_remote

from . import registry_paths

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


def namespaced() -> bool:
    """Whether this process carries an explicit installation context."""
    return bool(os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip())


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _normalized_repository_remote(project: str) -> str:
    from . import repos

    entry = repos.read_registry().repos.get(project)
    if entry is None:
        raise ValueError(f"repository {project!r} is not registered")
    raw_remote = entry.remote.strip()
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


def _identity(project: str) -> RepositoryIdentity:
    context = registry_paths.installation_context()
    if context is None:
        raise ValueError("repository identity requires namespaced context")
    normalized = _normalized_repository_remote(project)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    repository_id = f"repo-{digest}"
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


def _publish_receipt(temporary: Path, receipt_path: Path) -> bool:
    """Atomically publish complete receipt bytes without replacing an owner."""
    if os.name == "nt":
        import ctypes

        move_file = ctypes.WinDLL(
            "kernel32", use_last_error=True
        ).MoveFileExW
        move_file.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_uint,
        ]
        move_file.restype = ctypes.c_int
        if move_file(str(temporary), str(receipt_path), 0x8):
            return True
        error_code = ctypes.get_last_error()
        if error_code in {80, 183}:
            return False
        raise OSError(
            error_code,
            "MoveFileExW failed to publish repository identity",
            str(receipt_path),
        )
    try:
        os.link(temporary, receipt_path)
        return True
    except FileExistsError:
        return False


def ensure_project_state(project: str) -> Path:
    """Create or validate the cell-local repository identity and state root."""
    if not namespaced():
        raise ValueError("cell-local project state requires namespaced context")
    identity = _identity(project)
    repos_root = identity.repository_root.parent
    repos_root.mkdir(parents=True, exist_ok=True)
    if _is_link_or_reparse(repos_root):
        raise ValueError(f"cell repository root is not a directory: {repos_root}")
    identity.repository_root.mkdir(exist_ok=True)
    if _is_link_or_reparse(identity.repository_root):
        raise ValueError(
            f"repository identity root is not a directory: {identity.repository_root}"
        )
    identity.state_root.mkdir(exist_ok=True)
    if _is_link_or_reparse(identity.state_root):
        raise ValueError(
            f"agent-worktrees project state root is not a directory: "
            f"{identity.state_root}"
        )
    receipt_path = identity.repository_root / "identity.json"
    if not receipt_path.exists():
        descriptor, temporary_name = tempfile.mkstemp(
            dir=identity.repository_root,
            prefix=".identity.json.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(_receipt(identity), indent=2, sort_keys=True)
                    + "\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
            published = _publish_receipt(temporary, receipt_path)
            if published and os.name != "nt":
                directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                directory = os.open(identity.repository_root, directory_flags)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    _validate_receipt(identity, retries=100)
    return identity.state_root


def project_state_root(project: str, legacy_root: Path) -> Path:
    """Return exact legacy state or one validated cell-local project root."""
    if not namespaced():
        return legacy_root
    identity = _identity(project)
    _validate_receipt(identity)
    if not identity.state_root.is_dir() or _is_link_or_reparse(identity.state_root):
        raise ValueError(
            f"agent-worktrees project state root is unavailable: "
            f"{identity.state_root}"
        )
    return identity.state_root
