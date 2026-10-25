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
from typing import Any

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


class RedirectPublicationError(RuntimeError):
    """Redirect publication or its recovery could not finish safely."""


def add_runtime_cli_commands(subparsers: argparse._SubParsersAction) -> None:
    redirects = subparsers.add_parser(
        "publish-redirects", help="atomically publish source redirects with failure recovery",
    )
    redirects.add_argument("--payload-dir", required=True, type=Path)
    redirects.add_argument("--version")
    redirects.add_argument("--payload-origin")
    redirects.add_argument("--expected-source-identity")
    redirects.add_argument("--ownership-receipt", type=Path)
    subparsers.choices["activate"].add_argument("--ownership-receipt", type=Path)
    context = subparsers.add_parser("publication-context")
    context.add_argument("--output", required=True, type=Path)
    context.add_argument("--binding", action="append", nargs=3, default=[])
    for command in ("publication-check", "publication-restore", "publication-cleanup"):
        parser = subparsers.add_parser(command)
        parser.add_argument("--context", required=True, type=Path)
        parser.add_argument("--receipt", action="append", required=True, type=Path)
        parser.add_argument("--path", action="append", default=[], type=Path)
        parser.add_argument("--expected", action="append", nargs=2, default=[])
        if command == "publication-restore":
            parser.add_argument("--output-receipt", required=True, type=Path)
    fingerprint = subparsers.add_parser(
        "fingerprint", help="print a stable sha256 fingerprint over the given "
        "source paths (files and/or directories)",
    )
    fingerprint.add_argument("paths", nargs="+", help="source file(s)/directory(ies)")


def run_runtime_cli_command(args: argparse.Namespace) -> None:
    if args.cmd.startswith("publication-"):
        publication = _runtime()._runtime_component("versioned_runtime_publication")
        if args.cmd == "publication-context":
            publication.create_context(args.output, args.root, args.binding)
        elif args.cmd == "publication-check":
            publication.check_owned(args.context, args.receipt, args.path, args.expected)
        elif args.cmd == "publication-cleanup":
            publication.cleanup_proofs(args.context, args.receipt, args.expected)
        else:
            publication.recover_owned(args.context, args.receipt, args.path, args.output_receipt, args.expected)
        return
    if args.cmd == "publish-redirects":
        published = publish_runtime_redirects(
            args.root, args.payload_dir, version=args.version, payload_origin=args.payload_origin,
            expected_source_identity=args.expected_source_identity,
            ownership_receipt=args.ownership_receipt,
        )
        if args.json:
            print(json.dumps({"published": published}))
    else:
        raise ValueError(f"Unknown source runtime command: {args.cmd}")


@dataclass(frozen=True)
class _RedirectState:
    content: bytes
    mode: int
    device: int
    inode: int
    modified: int


def _redirect_state(path: Path) -> _RedirectState | None:
    try:
        named = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(named.st_mode) or (
        getattr(named, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    ):
        raise RedirectPublicationError(f"Redirect must be an ordinary file: {path}")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        content = stream.read()
        after = os.fstat(stream.fileno())
    def identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
        return value.st_dev, value.st_ino, value.st_mode, value.st_mtime_ns, value.st_size

    if identity(named) != identity(opened) or identity(opened) != identity(after) or identity(after) != identity(path.lstat()):
        raise RedirectPublicationError(f"Redirect changed while being read: {path}")
    return _RedirectState(content, after.st_mode, after.st_dev, after.st_ino, after.st_mtime_ns)


def _stage_redirect(
    root: Path, name: str, content: bytes, mode: int,
    owned: dict[Path, tuple[int, int]], receipt: Any = None, target: Path | None = None,
) -> Path:
    fd, temporary = tempfile.mkstemp(prefix=f".{name}.redirect-", dir=root)
    path = Path(temporary)
    opened = os.fstat(fd)
    owned[path] = opened.st_dev, opened.st_ino
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
        path.chmod(mode)
        if receipt is not None:
            receipt.created(target, stream.fileno(), content=content)
    return path


def _cleanup_redirect_stage(path: Path, identity: tuple[int, int]) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (info.st_dev, info.st_ino) != identity:
        raise RedirectPublicationError(f"Redirect temporary file changed ownership: {path}")
    path.chmod(stat.S_IMODE(info.st_mode) | stat.S_IWUSR)
    path.unlink()


def _owned_redirect_state(
    path: Path, owned: dict[Path, tuple[int, int]], content: bytes,
) -> _RedirectState:
    current = _redirect_state(path)
    if (current is None or (current.device, current.inode) != owned.get(path)
            or current.content != content or path.lstat().st_nlink != 1):
        raise RedirectPublicationError(f"Redirect staging lost ownership or content: {path}")
    return current


def _verify_redirect_source(payload: Path, version: str | None, expected: str | None) -> None:
    metadata = payload / SOURCE_FILE
    if expected is None:
        if metadata.exists() or metadata.is_symlink() or re.search(r"[+.]source\.", version or ""):
            raise SourceIdentityError("Source redirect publication requires the selected full source identity")
        return
    selected = json.loads(expected, object_pairs_hook=_unique_object)
    receipt = selected.get("runtime_source") if isinstance(selected, dict) else None
    package_version = receipt.get("package_version") if isinstance(receipt, dict) else None
    if not isinstance(package_version, str):
        raise SourceIdentityError("Source redirect publication requires a complete selected source identity")
    identity = read_source_identity(payload, package_version)
    if (identity is None or json.dumps(manifest_fields(identity), sort_keys=True) != expected
            or (version is not None and version != identity.runtime_version)):
        raise SourceIdentityError("Source identity changed after runtime selection; redirects were not committed")


def publish_runtime_redirects(
    root: Path, payload_dir: Path, version: str | None = None,
    payload_origin: str | None = None, expected_source_identity: str | None = None,
    ownership_receipt: Path | None = None,
) -> dict[str, str]:
    """Stage all requested redirects, then replace or restore them under one lease.

    The installer retains its outer lifecycle lock and source-admission duties.
    This narrower lease serializes redirect writers, including otherwise-unlocked
    source bootstrap paths. It is distinct from runtime selector publication.
    """
    payload_dir = payload_dir.resolve(strict=True)
    if not payload_dir.is_dir():
        raise ValueError("Redirect payload must be an existing directory")
    values = {"payload-dir": str(payload_dir)}
    if version is not None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", version) or version.endswith("."):
            raise ValueError("Redirect version must be a single ordinary slot name")
        values["stamped-version"] = version
    if payload_origin is not None:
        values["payload-origin"] = payload_origin
    if any(not value or any(char in value for char in "\x00\r\n") for value in values.values()):
        raise ValueError("Redirect values must be non-empty single lines")
    root = root.resolve()
    if root == Path(root.anchor):
        raise ValueError("Redirect publication requires an installation root, not a filesystem root")
    with _runtime()._kernel_lease(root, "redirects", kind="publication", shared=False):
        publication = _runtime()._runtime_component("versioned_runtime_publication") if ownership_receipt else None
        receipt = publication.Receipt(ownership_receipt, root, [root / name for name in values], "redirects") if publication else None
        outcome = "error"
        try:
            _verify_redirect_source(payload_dir, version, expected_source_identity)
            previous = {root / name: _redirect_state(root / name) for name in values}
        except BaseException:
            if receipt is not None:
                receipt.finish("error")
            raise
        owned: dict[Path, tuple[int, int]] = {}
        backups: dict[Path, Path] = {}
        stages: dict[Path, Path] = {}
        attempted: dict[Path, _RedirectState] = {}
        retained: set[Path] = set()
        try:
            for path, old in previous.items():
                if old is not None:
                    backups[path] = _stage_redirect(root, path.name, old.content, 0o600, owned)
                mode = old.mode & 0o755 if old is not None else 0o600
                arguments = (root, path.name, (values[path.name] + "\n").encode("utf-8"), mode, owned)
                stages[path] = (_stage_redirect(*arguments, receipt=receipt, target=path)
                                if receipt else _stage_redirect(*arguments))
            for path, temporary in stages.items():
                _verify_redirect_source(payload_dir, version, expected_source_identity)
                if _redirect_state(path) != previous[path]:
                    retained.update(backups.values())
                    raise RedirectPublicationError(f"Redirect changed before publication: {path}")
                staged = _owned_redirect_state(
                    temporary, owned, (values[path.name] + "\n").encode("utf-8"),
                )
                attempted[path] = staged
                os.replace(temporary, path)
            _verify_redirect_source(payload_dir, version, expected_source_identity)
            for path, expected in attempted.items():
                if _redirect_state(path) != expected:
                    retained.update(backups.values())
                    raise RedirectPublicationError(f"Redirect changed during publication: {path}")
            outcome = "success"
        except BaseException as failure:
            if isinstance(failure, RedirectPublicationError):
                retained.update(backups.values())
            recovery_errors: list[str] = []
            for path, expected in reversed(list(attempted.items())):
                try:
                    current = _redirect_state(path)
                    old = previous[path]
                    if current == old:
                        continue
                    if current != expected:
                        raise RedirectPublicationError(f"Cannot restore a concurrently changed redirect: {path}")
                    if old is not None:
                        backup = backups[path]
                        _owned_redirect_state(backup, owned, old.content)
                        backup.chmod(stat.S_IMODE(old.mode))
                        if receipt is not None:
                            fd = os.open(backup, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                            try:
                                created = publication.state_fd(fd)
                                if (created.device, created.inode) != owned[backup]:
                                    raise RedirectPublicationError("Recovery stage lost its created identity")
                                receipt.created(path, fd, content=old.content, phase="recovery")
                            finally:
                                os.close(fd)
                        os.replace(backup, path)
                        restored = _redirect_state(path)
                        if restored is None or restored.content != old.content or restored.mode != old.mode:
                            raise RedirectPublicationError(f"Redirect recovery verification failed: {path}")
                    else:
                        path.unlink()
                        if _redirect_state(path) is not None:
                            raise RedirectPublicationError(f"Redirect removal verification failed: {path}")
                except (OSError, RedirectPublicationError) as recovery:
                    recovery_errors.append(str(recovery))
            if recovery_errors or retained:
                retained.update(backups.values())
                paths = ", ".join(str(path) for path in sorted(retained) if path.exists())
                detail = "; ".join(recovery_errors) or str(failure)
                raise RedirectPublicationError(
                    f"Redirect publication failed; recovery files retained: {paths}; {detail}"
                ) from failure
            raise
        finally:
            for path, identity in owned.items():
                if path in retained:
                    continue
                try:
                    _cleanup_redirect_stage(path, identity)
                except (OSError, RedirectPublicationError) as cleanup:
                    print(f"Redirect temporary cleanup failed; retained {path}: {cleanup}", file=sys.stderr)
            if receipt is not None:
                receipt.finish(outcome)
    return values


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
    """Load the single explicit primitive resource shipped beside this component."""
    candidates: list[Path] = []
    for name in ("versioned_runtime.py", "_versioned_runtime.py"):
        path = Path(__file__).with_name(name)
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(info.st_mode) or (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise SourceIdentityError("Runtime primitive must be an ordinary co-located file")
        candidates.append(path)
    if len(candidates) != 1:
        raise SourceIdentityError("Runtime resources require exactly one named or packaged primitive")
    path = candidates[0]
    spec = importlib.util.spec_from_file_location("source_identity_versioned_runtime", path)
    if spec is None or spec.loader is None:
        raise SourceIdentityError("Co-located runtime primitive is unavailable")
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


def payload_package_version(payload: Path, plugin: str) -> str:
    """Read the selected consumer's attributed package identity."""
    if plugin == "worktree-manager":
        import tomllib

        data = tomllib.loads((payload / "pyproject.toml").read_text(encoding="utf-8"))
        manifest = data.get("project")
        if (
            not isinstance(manifest, dict) or not isinstance(manifest.get("name"), str)
            or re.sub(r"[-_.]+", "-", manifest["name"]).lower()
            != "copilot-extensions-worktree-manager"
        ):
            raise SourceIdentityError("Materialized Manager project identity does not match")
    else:
        manifest = json.loads(
            (payload / "plugin.json").read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
        )
        if not isinstance(manifest, dict) or manifest.get("name") != plugin:
            raise SourceIdentityError("Materialized plugin identity does not match the selection")
    version = manifest.get("version")
    if not isinstance(version, str) or not version:
        raise SourceIdentityError("Materialized runtime has no package version")
    return version


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
    if (
        payload_package_version(payload, data["plugin"]) != package_version
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
