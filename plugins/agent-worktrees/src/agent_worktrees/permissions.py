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


def _atomic_json_write(path: Path, data: Any, header: str = "") -> None:
    """Write JSON atomically via temp + rename.

    ``header`` is an optional raw text prefix (e.g. the leading ``//`` comment
    block that the Copilot CLI keeps at the top of its managed ``config.json``)
    written verbatim before the JSON body so it survives the rewrite.
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, (header + json.dumps(data, indent=2)).encode("utf-8"))
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


def ensure_extension_permission_approvals(worktree_path: str) -> bool:
    """Pre-approve the facility's own extensions for ``worktree_path`` BEFORE
    Copilot ever spawns there (github/copilot-agent-runtime#22266).

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
        header, data = _read_config_jsonc(config_file)
        folders: list[str] = data.get("trustedFolders", [])
        if worktree_path in folders:
            return False

        folders.append(worktree_path)
        data["trustedFolders"] = folders
        _atomic_json_write(config_file, data, header=header)
        return True
    except Exception:
        return False


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
        folders: list[str] = data.get("trustedFolders", [])
        if worktree_path not in folders:
            return False

        folders.remove(worktree_path)
        data["trustedFolders"] = folders
        _atomic_json_write(config_file, data, header=header)
        return True
    except Exception:
        return False
