"""Source identity records and assembly inventory, without installed runtime operations."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
import os
from pathlib import Path
import re
import stat

SOURCE_FILE = "runtime-source.json"
SOURCE_SCHEMA = "copilot-extensions.runtime-source"
_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_PACKAGE_VERSION = re.compile(
    r"\d+\.\d+\.\d+(?:[.-]dev\d+)?(?:\+[A-Za-z0-9][A-Za-z0-9._-]*)?"
)
_GENERATED = {
    ".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
}


class SourceIdentityError(ValueError):
    """A source deployment does not describe the supplied payload."""



@dataclass(frozen=True)
class SourceIdentity:
    plugin: str
    package_version: str
    source_commit: str
    source_tree: str
    assembly_hash: str
    runtime_version: str

    def document(self) -> dict[str, object]:
        return {"schema": SOURCE_SCHEMA, "version": 1, **asdict(self)}



def runtime_version(package_version: str, commit: str, assembly_hash: str) -> str:
    if not _PACKAGE_VERSION.fullmatch(package_version):
        raise SourceIdentityError("Source deployment requires a supported package version")
    if not _OBJECT_ID.fullmatch(commit) or not _DIGEST.fullmatch(assembly_hash):
        raise SourceIdentityError("Source deployment requires full source and assembly identities")
    separator = "." if "+" in package_version else "+"
    return f"{package_version}{separator}source.{commit}.{assembly_hash[:16]}"



def parse_source_identity(
    data: object, *, package_version: str | None = None, plugin: str | None = None,
) -> SourceIdentity:
    """Parse claimed Source fields, not proof of payload or installed admission."""
    fields = {
        "schema", "version", "plugin", "package_version", "source_commit",
        "source_tree", "assembly_hash", "runtime_version",
    }
    if not isinstance(data, dict) or set(data) != fields:
        raise SourceIdentityError("Source identity has an unsupported field set")
    if data["schema"] != SOURCE_SCHEMA or type(data["version"]) is not int or data["version"] != 1:
        raise SourceIdentityError("Source identity has an unsupported schema or version")
    names = fields - {"schema", "version"}
    if any(not isinstance(data[name], str) or not data[name] for name in names):
        raise SourceIdentityError("Source identity fields must be nonempty strings")
    if (
        (package_version is not None and data["package_version"] != package_version)
        or (plugin is not None and data["plugin"] != plugin)
    ):
        raise SourceIdentityError("Source identity does not match the plugin and package version")
    if not _OBJECT_ID.fullmatch(data["source_tree"]):
        raise SourceIdentityError("Source identity requires a full Git tree identity")
    expected = runtime_version(data["package_version"], data["source_commit"], data["assembly_hash"])
    if data["runtime_version"] != expected:
        raise SourceIdentityError("Source runtime version does not match its immutable inputs")
    return SourceIdentity(
        plugin=data["plugin"],
        package_version=data["package_version"],
        source_commit=data["source_commit"],
        source_tree=data["source_tree"],
        assembly_hash=data["assembly_hash"],
        runtime_version=data["runtime_version"],
    )



def assembly_hash(
    payload: Path, *, native_path: Callable[[Path], Path], fingerprint: Callable[..., str],
    on_progress: Callable[[], None] | None = None,
) -> str:
    files: list[Path] = []

    def scan_error(error: OSError) -> None:
        raise error

    def require_plain(path: Path) -> None:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise SourceIdentityError("Materialized runtime inputs must not be linked")

    require_plain(payload)
    payload = native_path(payload)
    for directory, directories, names in os.walk(payload, onerror=scan_error, followlinks=False):
        project_root = (Path(directory) / "pyproject.toml").is_file()
        directories[:] = [
            name for name in directories
            if name not in _GENERATED and not name.endswith(".egg-info")
            and not (project_root and name in ("build", "dist"))
        ]
        for name in directories:
            require_plain(Path(directory) / name)
        for name in names:
            path = Path(directory) / name
            if path == payload / SOURCE_FILE or name.endswith((".pyc", ".pyo")):
                continue
            require_plain(path)
            # Installation-cell provenance is published after source assembly;
            # its separate receipt validator owns this non-runtime root sidecar.
            if path == payload / "snapshot-provenance.json":
                continue
            files.append(path)
            if on_progress is not None:
                on_progress()
    if not files:
        raise SourceIdentityError("Materialized runtime payload is empty")
    return fingerprint(
        files, **({"on_progress": on_progress} if on_progress is not None else {}),
    )
