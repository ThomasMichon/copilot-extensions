"""Source-qualified runtime identity, separate from the package version."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from functools import lru_cache
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
from types import ModuleType

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


def package_version(slot_name: str) -> str:
    """Separate generated source/repair identities from the package version."""
    ordinary = re.sub(r"[+.]repair\.[0-9a-f]{32}$", "", slot_name)
    return re.sub(
        r"[+.]source\.(?:[0-9a-f]{40}|[0-9a-f]{64})\.[0-9a-f]{16}$", "", ordinary,
    )


def version_key(version: str):
    """Order package releases and profiles before generated identities."""
    supported = re.fullmatch(
        r"(\d+)\.(\d+)\.(\d+)(?:[.-]dev(\d+))?(?:\+([A-Za-z0-9][A-Za-z0-9._-]*))?",
        package_version(version),
    )
    if supported:
        major, minor, patch, dev, profile = supported.groups()
        return (
            0, int(major), int(minor), int(patch),
            1 if dev is None else 0, int(dev or 0), (profile or "").casefold(),
            version.casefold(),
        )
    tokens = re.split(r"(\d+)", version.casefold())
    return (1, tuple((1, int(token)) if token.isdigit() else (0, token) for token in tokens))


def requires_exact_selection(versions: list[str]) -> bool:
    """Refuse unordered source recovery, including implicit plain-slot downgrade."""
    if not any(re.search(r"[+.]source\.", version) for version in versions):
        return False
    print(
        "Source-qualified runtime recovery requires an exact current-version or "
        "last-known-good selector; refusing unordered source fallback",
        file=sys.stderr,
    )
    return True


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SourceIdentityError(f"Duplicate source identity field: {key}")
        result[key] = value
    return result


@lru_cache(maxsize=1)
def _runtime() -> ModuleType:
    path = Path(__file__).with_name("versioned_runtime.py")
    spec = importlib.util.spec_from_file_location("source_identity_versioned_runtime", path)
    if spec is None or spec.loader is None:
        raise SourceIdentityError("Canonical runtime fingerprint helper is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def assembly_hash(payload: Path) -> str:
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
    if not files:
        raise SourceIdentityError("Materialized runtime payload is empty")
    return _runtime().fingerprint_source(files)


def read_source_identity(
    payload: Path, package_version: str, *, plugin: str | None = None,
) -> SourceIdentity | None:
    path = payload / SOURCE_FILE
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or (
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    ):
        raise SourceIdentityError("Source identity must be an ordinary file")
    data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
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
    manifest = json.loads(
        (payload / "plugin.json").read_text(encoding="utf-8"),
        object_pairs_hook=_unique_object,
    )
    if not isinstance(manifest, dict) or (
        manifest.get("name") != data["plugin"]
        or manifest.get("version") != package_version
        or data["package_version"] != package_version
        or (plugin is not None and data["plugin"] != plugin)
    ):
        raise SourceIdentityError("Source identity does not match the plugin and package version")
    if not _OBJECT_ID.fullmatch(data["source_tree"]):
        raise SourceIdentityError("Source identity requires a full Git tree identity")
    expected = runtime_version(package_version, data["source_commit"], data["assembly_hash"])
    if data["runtime_version"] != expected:
        raise SourceIdentityError("Source runtime version does not match its immutable inputs")
    if assembly_hash(payload) != data["assembly_hash"]:
        raise SourceIdentityError("Materialized payload bytes do not match the source identity")
    return SourceIdentity(
        plugin=data["plugin"],
        package_version=data["package_version"],
        source_commit=data["source_commit"],
        source_tree=data["source_tree"],
        assembly_hash=data["assembly_hash"],
        runtime_version=data["runtime_version"],
    )


def manifest_fields(identity: SourceIdentity | None) -> dict[str, object]:
    if identity is None:
        return {}
    return {
        "runtime_version": identity.runtime_version,
        "runtime_source": identity.document(),
    }


def publish_source_snapshot(payload: Path, root: Path, identity: SourceIdentity) -> Path:
    """Serialize complete source publication without directory-nesting moves."""
    payload = Path(os.path.abspath(payload))
    root = Path(os.path.abspath(root.expanduser()))
    if root.resolve().is_relative_to(payload.resolve()):
        raise SourceIdentityError("Source snapshot root must be outside the source payload")

    def verify(path: Path) -> None:
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise SourceIdentityError("Source snapshot inputs must be ordinary directories")
        if read_source_identity(path, identity.package_version, plugin=identity.plugin) != identity:
            raise SourceIdentityError("Source snapshot identity changed after runtime selection")

    verify(payload)
    snapshots = root / "snapshots"
    snapshot = snapshots / identity.runtime_version
    snapshots.mkdir(parents=True, exist_ok=True)
    with _runtime().construction_lease(snapshots, identity.runtime_version):
        verify(payload)
        if snapshot.exists() or snapshot.is_symlink():
            verify(snapshot)
            verify(payload)
            return snapshot
        with tempfile.TemporaryDirectory(prefix=".source-", dir=snapshots) as temporary:
            candidate = Path(temporary) / "payload"
            shutil.copytree(payload, candidate, symlinks=True)
            verify(candidate)
            verify(payload)
            candidate.rename(snapshot)
        return snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--package-version", required=True)
    parser.add_argument("--plugin")
    parser.add_argument("--expected-runtime-version")
    parser.add_argument("--expected-source-identity")
    parser.add_argument("--snapshot-root", type=Path)
    parser.add_argument("--format", choices=("version", "json", "shell-fields"), default="version")
    args = parser.parse_args(argv)
    try:
        if not _PACKAGE_VERSION.fullmatch(args.package_version):
            raise SourceIdentityError("Source deployment requires a supported package version")
        identity = read_source_identity(args.payload, args.package_version, plugin=args.plugin)
        selected_version = identity.runtime_version if identity else args.package_version
        if args.expected_runtime_version is not None and args.expected_runtime_version != selected_version:
            raise SourceIdentityError("Source identity changed after runtime selection")
        if (
            args.expected_source_identity is not None
            and json.dumps(manifest_fields(identity), sort_keys=True) != args.expected_source_identity
        ):
            raise SourceIdentityError("Source identity changed after runtime selection")
        if args.snapshot_root is not None:
            if identity is None or args.format != "version":
                raise SourceIdentityError("Source snapshot publication requires a source receipt and path output")
            print(publish_source_snapshot(args.payload, args.snapshot_root, identity))
            return 0
        if args.format == "version":
            print(selected_version)
        else:
            fields = manifest_fields(identity)
            if args.format == "shell-fields":
                print(json.dumps(fields, sort_keys=True)[1:-1])
            else:
                print(json.dumps(fields, sort_keys=True))
    except (OSError, ValueError) as error:
        print(f"source-runtime: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
