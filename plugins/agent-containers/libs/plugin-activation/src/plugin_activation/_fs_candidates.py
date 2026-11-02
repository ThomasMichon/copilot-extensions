"""Filesystem checks shared by installed- and marketplace-root resolution."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from dropin_registry import ScanAuthority
from plugin_resolve import PLUGIN_MANIFEST_RELS

from ._config_io import _load_json_object

_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        or getattr(info, "st_reparse_tag", 0)
    )


def _regular_directory(path: Path) -> tuple[Path | None, str | None]:
    try:
        info = path.lstat()
        canonical = path.resolve(strict=True)
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, str(exc)
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or _is_reparse(info)
    ):
        return None, "path must be a non-reparse directory"
    return canonical, None


def _manifest_name(root: Path) -> tuple[str | None, str | None, bool]:
    for rel in PLUGIN_MANIFEST_RELS:
        path = root.joinpath(*rel)
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            return None, str(exc), True
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or _is_reparse(info)
        ):
            return None, "plugin manifest must be a regular non-reparse file", False
        authority, data, findings = _load_json_object(path)
        if authority is ScanAuthority.INDETERMINATE:
            indeterminate = findings[0].reason == "entry-indeterminate"
            return None, findings[0].detail or findings[0].reason, indeterminate
        name = data.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip(), None, False
        return None, "plugin manifest requires a non-empty name", False
    return None, "plugin manifest is missing", False


def _valid_source_part(value: str) -> bool:
    return bool(
        value
        and value not in {".", ".."}
        and "/" not in value
        and "\\" not in value
        and "@" not in value
    )
