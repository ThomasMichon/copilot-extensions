"""Read-only placement inspection for isolated recovery qualification.

No discovery, mkdir, backfill, journal append, Git exclusion edit or activation.
The caller supplies independently resolved Git roots and identities.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .recovery_facts import RecoveryError, identity

DIRECTORY = ".agent-worktrees-recovery"
ANCHOR_DIRECTORY = "agent-worktrees-recovery"
MARKER_SCHEMA = "agent-worktrees.recovery-marker"


@dataclass(frozen=True)
class RecoveryLayout:
    marker_directory: Path
    journal_directory: Path


def marker_value(repository_id: str, resource_id: str | None = None) -> dict:
    """Location-free correlation evidence; never a writer-election record."""
    result = {
        "schema": MARKER_SCHEMA, "version": 1,
        "repository_id": identity(repository_id),
        "role": "resource" if resource_id is not None else "journal",
    }
    if resource_id is not None:
        result["resource_id"] = identity(resource_id)
    return result


def _ordinary_chain(path: Path) -> None:
    for item in (*reversed(path.parents), path):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise RecoveryError("source_unavailable") from None
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise RecoveryError("path_indirection")
        if not stat.S_ISDIR(info.st_mode):
            raise RecoveryError("foreign_path_conflict")


def _inspect(directory: Path, expected: dict) -> None:
    _ordinary_chain(directory)
    if not directory.exists():
        return
    marker = directory / "identity.json"
    try:
        info = marker.lstat()
        if not stat.S_ISREG(info.st_mode) or (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise RecoveryError("path_indirection")
        if info.st_size > 4096:
            raise RecoveryError("record_limit")
        # Duplicate known keys must never hide a conflicting identity.
        from .recovery_facts import _constant, _depth, _integer, _number, _object
        with marker.open("rb") as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise RecoveryError("record_limit")
        value = json.loads(
            raw, object_pairs_hook=_object, parse_constant=_constant,
            parse_float=_number, parse_int=_integer,
        )
        _depth(value)
    except FileNotFoundError:
        raise RecoveryError("foreign_path_conflict") from None
    except OSError:
        raise RecoveryError("source_unavailable") from None
    except RecursionError:
        raise RecoveryError("record_limit") from None
    except (UnicodeError, ValueError) as exc:
        if isinstance(exc, RecoveryError):
            raise
        raise RecoveryError("malformed_marker") from None
    if not isinstance(value, dict) or value.get("schema") != expected["schema"]:
        raise RecoveryError("identity_conflict")
    if type(value.get("version")) is not int or value["version"] != expected["version"]:
        raise RecoveryError("unsupported_schema")
    if any(value.get(key) != item for key, item in expected.items() if key != "version"):
        raise RecoveryError("identity_conflict")
    # Unknown fields are retained on disk, but required future semantics cannot
    # be silently treated as a compatible marker.
    if value.get("required_capabilities", []) != []:
        raise RecoveryError("required_capability_unsupported")


def inspect_layout(
    worktree: Path, git_common_dir: Path, *,
    repository_id: str, resource_id: str, marker_ignored: bool,
) -> RecoveryLayout:
    """Preview approved placement, refusing foreign paths without modifying them.

    ``marker_ignored`` is the caller's Git-owner observation, not consent to edit
    ignores. Paths are explicit locators and may change across move/restore.
    """
    for root in (worktree, git_common_dir):
        if not root.is_absolute() or ".." in root.parts:
            raise RecoveryError("invalid_root")
        _ordinary_chain(root)
        if not root.is_dir():
            raise RecoveryError("source_unavailable")
    if marker_ignored is not True:
        raise RecoveryError("marker_not_ignored")
    if os.path.normcase(str(worktree)) == os.path.normcase(str(git_common_dir)):
        raise RecoveryError("invalid_root")
    layout = RecoveryLayout(worktree / DIRECTORY, git_common_dir / ANCHOR_DIRECTORY)
    _inspect(layout.marker_directory, marker_value(repository_id, resource_id))
    _inspect(layout.journal_directory, marker_value(repository_id))
    return layout
