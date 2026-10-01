"""Shared helpers for agent-worktrees' packaged launch-wrapper assets."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
MANIFEST_NAME = "launch-wrapper-assets.json"
_SCHEMA = "copilot-extensions.launch-wrapper-assets"


@dataclass(frozen=True)
class LaunchWrapperAssets:
    manifest_path: Path
    canonical_dir_raw: str
    files: tuple[str, ...]

    @property
    def plugin_dir(self) -> Path:
        return self.manifest_path.parent

    def canonical_dir(self, *, repo_root: Path = REPO) -> Path:
        return repo_root / self.canonical_dir_raw

    def payload_dir(self, *, plugin_dir: Path | None = None) -> Path:
        return (plugin_dir or self.plugin_dir) / "bin"


def load_manifest(plugin_dir: Path) -> LaunchWrapperAssets | None:
    manifest_path = plugin_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        return None
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if data.get("schema") != _SCHEMA or data.get("version") != 1:
        raise ValueError(f"{manifest_path}: unsupported launch-wrapper-assets schema")
    canonical_dir = data.get("canonicalDir")
    files = data.get("files")
    if not isinstance(canonical_dir, str) or not canonical_dir:
        raise ValueError(f"{manifest_path}: canonicalDir must be a non-empty string")
    if (
        not isinstance(files, list)
        or not files
        or any(not isinstance(name, str) or not name for name in files)
    ):
        raise ValueError(f"{manifest_path}: files must be a non-empty string list")
    return LaunchWrapperAssets(
        manifest_path=manifest_path,
        canonical_dir_raw=canonical_dir,
        files=tuple(files),
    )
