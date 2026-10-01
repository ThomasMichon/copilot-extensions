"""Shared helpers for agent-worktrees' launch-wrapper asset manifest."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


SCHEMA = "copilot-extensions.launch-wrapper-assets"
VERSION = 1
MANIFEST = "launch-wrapper-assets.json"


@dataclass(frozen=True)
class LaunchWrapperAssets:
    plugin_dir: Path
    canonical_dir_raw: str
    files: tuple[str, ...]

    @property
    def manifest_path(self) -> Path:
        return self.plugin_dir / MANIFEST

    @property
    def payload_dir(self) -> Path:
        return self.plugin_dir / "bin"

    def canonical_dir(self, repo_dir: Path) -> Path:
        return repo_dir / Path(self.canonical_dir_raw)


def _is_relative_subpath(value: str) -> bool:
    path = Path(value)
    return (
        bool(value)
        and not path.is_absolute()
        and path.parts != ()
        and all(part not in ("", ".", "..") for part in path.parts)
    )


def _is_plain_basename(value: str) -> bool:
    path = Path(value)
    return (
        bool(value)
        and value not in {".", ".."}
        and path.name == value
        and "/" not in value
        and "\\" not in value
    )


def load_manifest(plugin_dir: Path) -> LaunchWrapperAssets:
    manifest_path = plugin_dir / MANIFEST
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = data.get("files")
    if (
        data.get("schema") != SCHEMA
        or data.get("version") != VERSION
        or not isinstance(data.get("canonicalDir"), str)
        or not _is_relative_subpath(data["canonicalDir"])
        or not isinstance(files, list)
        or not files
        or any(not isinstance(name, str) or not _is_plain_basename(name) for name in files)
    ):
        raise ValueError(f"malformed launch-wrapper asset manifest: {manifest_path}")
    return LaunchWrapperAssets(
        plugin_dir=plugin_dir,
        canonical_dir_raw=data["canonicalDir"],
        files=tuple(files),
    )


def resolve_source_dir(repo_dir: Path, assets: LaunchWrapperAssets) -> Path:
    if all((assets.payload_dir / name).is_file() for name in assets.files):
        return assets.payload_dir
    return assets.canonical_dir(repo_dir)
