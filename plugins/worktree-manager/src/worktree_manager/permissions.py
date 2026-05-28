"""Copilot CLI permission cloning and merging.

Handles the permissions-config.json lifecycle:
- Clone: copy anchor permissions to a new worktree path
- Merge: merge worktree permissions back to anchor on finalization
- Remove: clean up the worktree entry after merge
"""

from __future__ import annotations

import json
import os
import platform
import tempfile
from pathlib import Path
from typing import Any


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


def _atomic_json_write(path: Path, data: Any) -> None:
    """Write JSON atomically via temp + rename."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, json.dumps(data, indent=2).encode())
        os.close(fd)
        if path.exists():
            path.unlink()
        os.rename(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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


def add_trusted_folder(worktree_path: str) -> bool:
    """Add a worktree path to trusted_folders in config.json.

    Args:
        worktree_path: The worktree path to trust.

    Returns:
        True if added, False if already present or skipped.
    """
    config_file = _config_path()
    if not config_file.exists():
        return False

    try:
        data = json.loads(config_file.read_text())
        folders: list[str] = data.get("trusted_folders", [])
        if worktree_path in folders:
            return False

        folders.append(worktree_path)
        data["trusted_folders"] = folders
        _atomic_json_write(config_file, data)
        return True
    except Exception:
        return False


def remove_trusted_folder(worktree_path: str) -> bool:
    """Remove a worktree path from trusted_folders in config.json.

    Args:
        worktree_path: The worktree path to remove.

    Returns:
        True if removed, False if not found or skipped.
    """
    config_file = _config_path()
    if not config_file.exists():
        return False

    try:
        data = json.loads(config_file.read_text())
        folders: list[str] = data.get("trusted_folders", [])
        if worktree_path not in folders:
            return False

        folders.remove(worktree_path)
        data["trusted_folders"] = folders
        _atomic_json_write(config_file, data)
        return True
    except Exception:
        return False
