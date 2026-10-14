"""Resolve this payload's projection engine, materialized at promotion."""

from __future__ import annotations

import json
from pathlib import Path, PureWindowsPath

ENTRY_POINT = "manage-instruction-projections.py"


def _subdirectory(root: Path, value: object) -> Path:
    if (
        not isinstance(value, str) or not value
        or PureWindowsPath(value).drive or Path(value).is_absolute()
        or any(part in ("", ".", "..") for part in value.replace("\\", "/").split("/"))
    ):
        raise ValueError("invalid projection engine directory")
    path = root / value
    path.resolve().relative_to(root.resolve())
    return path


def projection_cli_script(payload: Path | None = None) -> Path:
    if payload is None:
        from . import installer

        payload = installer._payload_root()
    manifest = json.loads((payload / "packaged-assets.json").read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != "copilot-extensions.packaged-assets"
        or type(manifest.get("version")) is not int or manifest["version"] != 1
    ):
        raise ValueError("invalid packaged projection engine manifest")
    names = manifest.get("files")
    if (
        not isinstance(names, list) or ENTRY_POINT not in names
        or any(
            not isinstance(name, str) or name in ("", ".", "..")
            or "/" in name or "\\" in name
            for name in names
        )
    ):
        raise ValueError("invalid packaged projection engine file list")
    scripts = _subdirectory(payload, manifest.get("payloadDir"))
    if not scripts.is_dir():
        repo = payload.parent.parent
        if payload.parent.name != "plugins" or not (repo / "tools" / "materialize_main.py").is_file():
            raise FileNotFoundError("this agent-worktrees payload has no packaged projection engine")
        scripts = _subdirectory(repo, manifest.get("canonicalDir"))
    for name in names:
        path = scripts / name
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(f"packaged projection engine is incomplete: {name}")
    return scripts / ENTRY_POINT
