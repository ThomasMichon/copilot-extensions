"""Shared helpers for agent-worktrees' packaged launch-wrapper assets."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PureWindowsPath


REPO = Path(__file__).resolve().parent.parent
MANIFEST_NAME = "launch-wrapper-assets.json"
PACKAGED_MANIFEST_NAME = "packaged-assets.json"
MANIFEST_NAMES = (MANIFEST_NAME, PACKAGED_MANIFEST_NAME)
_SCHEMA = "copilot-extensions.launch-wrapper-assets"


@dataclass(frozen=True)
class LaunchWrapperAssets:
    manifest_path: Path
    canonical_dir_raw: str
    files: tuple[str, ...]
    payload_dir_raw: str = "bin"

    @property
    def plugin_dir(self) -> Path:
        return self.manifest_path.parent

    def canonical_dir(self, *, repo_root: Path = REPO) -> Path:
        return repo_root / self.canonical_dir_raw

    def payload_dir(self, *, plugin_dir: Path | None = None) -> Path:
        return (plugin_dir or self.plugin_dir) / self.payload_dir_raw


def _is_relative_subpath(value: str) -> bool:
    path = Path(value)
    return (
        bool(value)
        and not PureWindowsPath(value).drive
        and not path.is_absolute()
        and path.parts != ()
        and all(part not in ("", ".", "..") for part in value.replace("\\", "/").split("/"))
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


def load_manifest(plugin_dir: Path, *, manifest_name: str = MANIFEST_NAME) -> LaunchWrapperAssets | None:
    manifest_path = plugin_dir / manifest_name
    if not manifest_path.is_file():
        return None
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{manifest_path}: expected an asset manifest object")
    schema = _SCHEMA if manifest_name == MANIFEST_NAME else "copilot-extensions.packaged-assets"
    if data.get("schema") != schema or data.get("version") != 1:
        raise ValueError(f"{manifest_path}: unsupported launch-wrapper-assets schema")
    canonical_dir = data.get("canonicalDir")
    files = data.get("files")
    payload_dir = data.get("payloadDir", "bin")
    if not isinstance(canonical_dir, str) or not _is_relative_subpath(canonical_dir):
        raise ValueError(f"{manifest_path}: canonicalDir must be a non-empty string")
    if not isinstance(payload_dir, str) or not _is_relative_subpath(payload_dir):
        raise ValueError(f"{manifest_path}: payloadDir must be a relative subdirectory")
    if (
        not isinstance(files, list)
        or not files
        or any(not isinstance(name, str) or not _is_plain_basename(name) for name in files)
        or len(set(files)) != len(files)
    ):
        raise ValueError(f"{manifest_path}: files must be a non-empty basename-only string list")
    return LaunchWrapperAssets(
        manifest_path=manifest_path,
        canonical_dir_raw=canonical_dir,
        files=tuple(files),
        payload_dir_raw=payload_dir,
    )
