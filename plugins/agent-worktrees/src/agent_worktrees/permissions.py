"""Copilot CLI permission cloning and merging.

Handles the permissions-config.json lifecycle:
- Clone: copy anchor permissions to a new worktree path
- Merge: merge worktree permissions back to anchor on finalization
- Remove: clean up the worktree entry after merge

All mutations (including extension approvals and config.json folder trust)
share persistent per-file, user-wide locks. Best-effort acquisition failures
warn and skip; required folder-trust entry failures raise. No helper proceeds
with an unlocked read-modify-write.
"""

from __future__ import annotations

import json
import os
import platform
import tempfile
import warnings
from functools import wraps
from pathlib import Path
from typing import Any, Callable


_PERMISSION_LOCK_TIMEOUT = 2.0


class FolderTrustError(RuntimeError):
    """An admitted checkout could not be made trusted."""


def _serialized(
    path_for_lock: Callable[[], Path], *, merge: bool = False, strict: bool = False,
) -> Callable:
    """Serialize each user-wide file's entire RMW, including first creation.

    Reuse the persistent ``config.lock`` / ``permissions-config.lock`` sidecars
    and portable OS/thread locking from tracking. Never remove a sidecar: an
    atomic JSON replacement must not change the inode carrying the lock.
    Best-effort acquisition failures warn; strict entry failures raise.
    """
    def decorate(mutator: Callable) -> Callable:
        @wraps(mutator)
        def locked(*args: Any, **kwargs: Any) -> Any:
            from .tracking import _RecordLock

            path = path_for_lock()
            if strict:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    raise FolderTrustError(f"Could not prepare Copilot folder trust: {exc}") from exc
            if not strict and not path.parent.exists():
                return [] if merge else False
            lock = _RecordLock(
                path, timeout=_PERMISSION_LOCK_TIMEOUT, require_sidecar=True,
            )
            try:
                lock.__enter__()
                if lock._held is None and not lock._nested_sidecar:
                    raise OSError("no cross-process locking backend")
            except (OSError, TimeoutError) as exc:
                lock.__exit__()
                if strict:
                    raise FolderTrustError(
                        f"Could not lock Copilot folder trust ({type(exc).__name__}): {exc}"
                    ) from exc
                warnings.warn(
                    f"Copilot permission update skipped: could not lock {path.name} "
                    f"({type(exc).__name__}).",
                    RuntimeWarning,
                    stacklevel=2,
                )
                return [] if merge else False
            try:
                return mutator(*args, **kwargs)
            finally:
                lock.__exit__()

        return locked
    return decorate


def _copilot_dir() -> Path:
    """Return the path to the ~/.copilot directory."""
    if platform.system() == "Windows":
        home = os.environ.get("USERPROFILE", str(Path.home()))
    else:
        home = str(Path.home())
    return Path(home) / ".copilot"


def _permissions_path() -> Path:
    """Return the path to Copilot's permissions-config.json."""
    return _copilot_dir() / "permissions-config.json"


def _config_path() -> Path:
    """Return the path to Copilot's config.json."""
    return _copilot_dir() / "config.json"


def _atomic_json_write(path: Path, data: Any, header: str = "") -> None:
    """Write JSON atomically via temp + rename.

    ``header`` is an optional raw text prefix (e.g. the leading ``//`` comment
    block that the Copilot CLI keeps at the top of its managed ``config.json``)
    written verbatim before the JSON body so it survives the rewrite.
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(header + json.dumps(data, indent=2))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_config_jsonc(config_file: Path) -> tuple[str, Any]:
    """Read Copilot's ``config.json``, which is JSONC (a leading ``//`` header).

    The Copilot CLI writes ``config.json`` with a managed leading comment block
    (``// This file is managed automatically.``) that Python's stdlib ``json``
    cannot parse. Split off the leading run of full-line ``//`` comments and
    blank lines as an opaque header, parse the remaining JSON body, and return
    ``(header, data)`` so callers can round-trip the header back out.

    Only full-line comments before the first JSON token are stripped, so ``//``
    inside string values (e.g. URLs) is never touched.
    """
    raw = config_file.read_text(encoding="utf-8")
    lines = raw.splitlines(keepends=True)
    header_lines: list[str] = []
    body_start = 0
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("//") or stripped == "":
            header_lines.append(line)
            body_start = i + 1
        else:
            break
    body = "".join(lines[body_start:])
    return "".join(header_lines), json.loads(body)


@_serialized(lambda: _permissions_path())
def clone_permissions(anchor_path: str, worktree_path: str) -> bool:
    """Clone Copilot permissions from anchor to a new worktree path.

    Args:
        anchor_path: The anchor repo path (source of permissions).
        worktree_path: The new worktree path (destination).

    Returns:
        True if permissions were cloned, False if skipped.
    """
    perm_file = _permissions_path()
    if not perm_file.exists():
        return False

    try:
        data = json.loads(perm_file.read_text())
        locations = data.get("locations", {})

        anchor_perms = locations.get(anchor_path)
        if not anchor_perms:
            return False

        if worktree_path in locations:
            return False  # already exists

        locations[worktree_path] = anchor_perms
        _atomic_json_write(perm_file, data)
        return True
    except Exception:
        return False


@_serialized(lambda: _permissions_path(), merge=True)
def merge_permissions(anchor_path: str, worktree_path: str) -> list[str]:
    """Merge worktree permissions back to anchor and remove worktree entry.

    Any new tool approvals granted in the worktree are added to the
    anchor's approval list. The worktree entry is then removed.

    Args:
        anchor_path: The anchor repo path.
        worktree_path: The worktree path to merge from and remove.

    Returns:
        List of newly merged permission descriptions.
    """
    perm_file = _permissions_path()
    if not perm_file.exists():
        return []

    try:
        data = json.loads(perm_file.read_text())
        locations = data.get("locations", {})

        wt_perms = locations.get(worktree_path)
        anchor_perms = locations.get(anchor_path)

        merged: list[str] = []

        if wt_perms and anchor_perms:
            # Build set of existing anchor approvals
            anchor_approvals = anchor_perms.get("tool_approvals", [])
            anchor_set = {
                json.dumps(a, sort_keys=True) for a in anchor_approvals
            }

            for approval in wt_perms.get("tool_approvals", []):
                key = json.dumps(approval, sort_keys=True)
                if key not in anchor_set:
                    anchor_approvals.append(approval)
                    anchor_set.add(key)
                    merged.append(key)

            anchor_perms["tool_approvals"] = anchor_approvals

        # Remove worktree entry
        if worktree_path in locations:
            del locations[worktree_path]

        _atomic_json_write(perm_file, data)
        return merged
    except Exception:
        return []


# The facility's own marketplace plugins that register an actual JS
# extension connection (installed-plugins/<name>/extensions/<name>/) and
# therefore hit the native runtime's load-time permission gate: any
# connection declaring a skipPermission tool, registering hooks, or handling
# permission requests must be granted "extension-permission-access" before
# its tools are ever published (sdkServerHost.ts's needsLoadTimeGate). Keep
# in sync with the extension directories under plugins/*/extensions/.
_FACILITY_EXTENSION_NAMES: tuple[str, ...] = (
    "plugin:agent-bridge:agent-bridge",
    "plugin:context-handoff:context-handoff",
    "plugin:agent-worktrees:agent-worktrees",
)


@_serialized(lambda: _permissions_path())
def ensure_extension_permission_approvals(worktree_path: str) -> bool:
    """Pre-approve the facility's own extensions for ``worktree_path`` BEFORE
    Copilot ever spawns there (a known Copilot CLI extension-load gate).

    Without this, a fresh location's first extension load blocks on an
    interactive "extension-permission-access" prompt -- and any tool call
    already dispatched to that extension queues or hangs behind it -- with
    no one necessarily present to answer for a headless/unattended launch.
    ``clone_permissions`` above already copies an anchor's approvals to a new
    worktree when the anchor itself has them, but a repo whose anchor was
    never manually approved (or a location clone_permissions didn't reach)
    still needs this direct seed. Mirrors ``add_trusted_folder``'s own
    pre-seed-before-first-launch pattern.

    Args:
        worktree_path: The worktree path to approve.

    Returns:
        True if any new approval was added, False if all were already
        present or the step failed (never raises -- best-effort only, a
        missed seed just falls back to today's interactive prompt).
    """
    perm_file = _permissions_path()
    try:
        if perm_file.exists():
            data = json.loads(perm_file.read_text())
        else:
            data = {}
        if not isinstance(data, dict):
            return False

        locations = data.setdefault("locations", {})
        if not isinstance(locations, dict):
            return False

        location = locations.setdefault(worktree_path, {})
        if not isinstance(location, dict):
            return False

        approvals = location.setdefault("tool_approvals", [])
        if not isinstance(approvals, list):
            return False

        existing = {
            (entry.get("kind"), entry.get("extensionName"))
            for entry in approvals
            if isinstance(entry, dict)
        }

        changed = False
        for extension_name in _FACILITY_EXTENSION_NAMES:
            key = ("extension-permission-access", extension_name)
            if key not in existing:
                approvals.append(
                    {"kind": "extension-permission-access", "extensionName": extension_name}
                )
                changed = True

        if not changed:
            return False

        _atomic_json_write(perm_file, data)
        return True
    except Exception:
        return False


def _add_trusted_folder(config_file: Path, worktree_path: str) -> bool:
    header, data = _read_config_jsonc(config_file) if config_file.exists() else ("", {})
    if not isinstance(data, dict):
        raise ValueError("Copilot config.json must contain an object")
    folders = data.get("trustedFolders", [])
    if not isinstance(folders, list) or not all(isinstance(item, str) for item in folders):
        raise ValueError("Copilot trustedFolders must be a list of paths")
    if worktree_path in folders:
        return False
    folders.append(worktree_path)
    data["trustedFolders"] = folders
    _atomic_json_write(config_file, data, header=header)
    return True


@_serialized(lambda: _config_path(), strict=True)
def _ensure_trusted_folder(worktree_path: str) -> bool:
    try:
        root = Path(worktree_path)
        if not root.is_dir() or root.is_symlink():
            raise ValueError("native checkout disappeared before folder trust could be established")
        return _add_trusted_folder(_config_path(), worktree_path)
    except (OSError, ValueError, TypeError) as exc:
        raise FolderTrustError(f"Could not ensure Copilot folder trust: {exc}") from exc


def ensure_trusted_folder(worktree_path: str) -> bool:
    """Ensure native folder trust after the caller admits this checkout.

    False means already trusted, never failure. This does not clone tool
    permissions or approve extensions.
    """
    try:
        root = Path(worktree_path)
        if (
            not root.is_absolute() or not root.is_dir() or root.is_symlink()
            or root.resolve() == Path(root.anchor)
        ):
            raise ValueError("folder trust requires an existing native checkout directory")
        return _ensure_trusted_folder(str(root.resolve()))
    except (OSError, ValueError, TypeError) as exc:
        raise FolderTrustError(f"Could not ensure Copilot folder trust: {exc}") from exc


@_serialized(lambda: _config_path())
def add_trusted_folder(worktree_path: str) -> bool:
    """Add a worktree path to trustedFolders in config.json.

    Pre-seeding the folder as trusted BEFORE the first launch suppresses
    Copilot's startup folder-trust settle (which otherwise triggers an
    extension reload during env-loading). The key is camelCase
    ``trustedFolders`` to match what the Copilot CLI actually reads; an
    earlier snake_case ``trusted_folders`` was silently ignored.

    Args:
        worktree_path: The worktree path to trust.

    Returns:
        True if added, False if already present or skipped.
    """
    config_file = _config_path()
    if not config_file.exists():
        return False

    try:
        return _add_trusted_folder(config_file, worktree_path)
    except (OSError, ValueError, TypeError) as exc:
        warnings.warn(f"Copilot folder trust update skipped: {exc}", RuntimeWarning, stacklevel=2)
        return False


@_serialized(lambda: _config_path())
def remove_trusted_folder(worktree_path: str) -> bool:
    """Remove a worktree path from trustedFolders in config.json.

    Uses the camelCase ``trustedFolders`` key the Copilot CLI reads (an
    earlier snake_case ``trusted_folders`` was a no-op, so worktree trust
    entries were never cleaned up on finalize).

    Args:
        worktree_path: The worktree path to remove.

    Returns:
        True if removed, False if not found or skipped.
    """
    config_file = _config_path()
    if not config_file.exists():
        return False

    try:
        header, data = _read_config_jsonc(config_file)
        if not isinstance(data, dict):
            raise ValueError("Copilot config.json must contain an object")
        folders = data.get("trustedFolders", [])
        if not isinstance(folders, list) or not all(isinstance(item, str) for item in folders):
            raise ValueError("Copilot trustedFolders must be a list of paths")
        names = {worktree_path, str(Path(worktree_path).resolve())}
        remaining = [folder for folder in folders if folder not in names]
        if remaining == folders:
            return False

        data["trustedFolders"] = remaining
        _atomic_json_write(config_file, data, header=header)
        return True
    except (OSError, ValueError, TypeError) as exc:
        warnings.warn(f"Copilot folder trust removal skipped: {exc}", RuntimeWarning, stacklevel=2)
        return False
