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
SOURCE_PIN_ENV = "COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN"
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
        r"[+.]source\.(?:[0-9a-f]{40}|[0-9a-f]{64})\.[0-9a-f]{16}"
        r"(?:\.profile\.[0-9a-f]{16})?$", "", ordinary,
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
        selected_pin = os.environ.get(SOURCE_PIN_ENV)
        if selected_pin is not None and json.dumps(
            manifest_fields(identity), sort_keys=True,
        ) != selected_pin:
            raise SourceIdentityError("Source identity differs from the update transaction selection")
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


def fingerprint_source(paths, *, _normalize_windows_extended_path) -> str:
    """Return a stable sha256 fingerprint over a frozen set of source paths.

    Covers the FULL attributable runtime install input, not just one
    manifest file -- an adopter must pass every path whose content changing
    should be treated as "this version's payload changed" (e.g. a plugin's
    whole ``src/`` tree plus its ``pyproject.toml``, not ``pyproject.toml``
    alone -- a prior audit on this effort found exactly that narrower scope
    on one adopter, #5472). Hashes each regular file's path (relative to
    whichever declared root most specifically contains it, labeled by that
    root's own path relative to the common ancestor of every declared root
    -- never the root's bare basename, which is not unique across declared
    roots, and never its absolute filesystem path, which would make the
    digest depend on WHERE the checkout happens to live) and content, sorted
    by label for determinism; a directory is walked recursively. Relocating
    an entire checkout to a different absolute path -- a fresh clone, a
    different OS, a renamed parent directory -- does not change the
    fingerprint of logically-identical content, so long as the declared
    roots' positions relative to EACH OTHER are unchanged.

    Each ``(label, content)`` pair is framed with an explicit 8-byte
    big-endian length prefix before hashing, so two different payloads can
    never serialize to the same byte stream (a bare separator byte, even a
    NUL, does not unambiguously frame arbitrary file content -- a label or
    file ending in that same byte could make two distinct inputs collide).

    Root matching is independent of the ORDER ``paths`` is passed in: roots
    are tried longest-path-first (the most specific containing root always
    wins over a shallower, overlapping one), and the same file reached
    through more than one overlapping root is only ever hashed once
    (deduplicated by its resolved absolute path). Swapping the order of
    overlapping roots cannot change the result. Note this is strictly about
    ORDER: declaring an additional, redundant child root that is already
    covered by a passed parent root is a DIFFERENT, more specific
    declaration (that child's own files are now labeled relative to it
    instead of the parent) and is expected to change the result -- this
    function does not treat "this subtree was separately named" as
    equivalent to "this subtree was already covered".

    Fails closed: a missing declared root, any unreadable file, or any
    directory that cannot be scanned/stat'd during the walk (e.g. a
    permission-denied subdirectory) raises immediately rather than being
    silently skipped -- a caller relying on this fingerprint for an
    admission decision must never see a best-effort digest over whatever
    happened to remain accessible stand in for "the full attributable input
    was validated". A caller that genuinely needs to fingerprint a
    partially-staged tree must check for that itself before calling this.

    Symlinks are never silently resolved away: a discovered symlink (to a
    file OR a directory) is hashed as ITS OWN entry -- its label, plus a
    record of what it points to -- never collapsed with whatever it
    happens to point at. Resolving a path and deduplicating by the
    resolved path would let a symlink alias (``alias.py -> real.py``, both
    under a declared root) vanish from the digest entirely once both paths
    resolved the same way. A NESTED symlinked DIRECTORY (one discovered
    mid-walk, not itself a declared root) is deliberately never
    followed/walked into (cycle-unsafe, and ambiguous which identity --
    the link or its target -- would own the nested names); instead the
    link itself is hashed as a single entry recording where it points, so
    re-pointing or adding/removing it still changes the digest even though
    its nested contents, if any, are not separately walked.

    A NESTED symlink's target must itself fall inside one of the DECLARED
    roots (so its actual bytes are already covered by that root's own,
    separate walk) -- this raises otherwise, exactly like a symlinked
    DECLARED ROOT does (below): hashing only a symlink's identity/pointer
    is only safe when the content it points at is attributable some other
    way, never when it is an undeclared external target whose bytes could
    change invisibly to this digest. A DANGLING target (resolves to
    nothing at all) is the one exception -- allowed through as identity-
    only, since there is no actual content it could be hiding.

    A symlink target is recorded as-is when relative (already stable under
    whole-tree relocation, since it is interpreted relative to the
    symlink's own location, which moves together with everything else).
    An ABSOLUTE target is made relocation-invariant the same way a file
    label is -- relative to the common ancestor of every declared root --
    but ONLY when it actually falls inside one of the declared roots
    themselves (never merely under their common ancestor, which can
    include undeclared sibling paths); an absolute target outside every
    declared root is rejected per the previous paragraph, so this
    normalization step only ever runs for a target already known to be
    covered.

    A DECLARED ROOT that is itself a symlink is treated differently from a
    nested one, and deliberately raises instead of silently succeeding: a
    root is the caller's own attributable content declaration, so reducing
    it to mere link-target identity -- never hashing what is actually read
    through that path -- would recreate exactly the content-drift blind
    spot this function exists to close (#5472: fingerprinting too little
    of the real install input). The caller must declare the real resolved
    content path (or the symlink's target) instead of a symlinked root.

    Each entry's kind ("file" vs "symlink") is itself framed as its own
    length-prefixed record alongside the label and content/target, so a
    regular file can never hash identically to a symlink merely because
    its raw bytes happen to equal some symlink's encoded target text.
    """
    import hashlib
    import os as _os

    def _lstat_mode(p: Path, context: str) -> int:
        # An explicit lstat (never following the final symlink component),
        # with any OSError other than "does not exist" propagating. This is
        # deliberately NOT Path.is_symlink()/is_dir()/is_file(): those
        # catch OSError internally (including PermissionError) and return
        # False, which would silently misclassify -- and potentially drop
        # -- an entry this function could not actually stat, rather than
        # failing closed like every other check in this function.
        try:
            return _os.lstat(p).st_mode
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise OSError(f"fingerprint_source: could not stat {p} ({context})") from exc

    def _declared_root_path(p) -> Path:
        # Resolve the PARENT chain (handles relative paths, intermediate
        # symlinks, '..' components) to get a canonical absolute path, but
        # preserve the final component exactly as declared -- never follow
        # it -- so a declared root that is itself a symlink is classified
        # with its own lstat identity below, instead of being silently
        # resolved through to whatever it points at.
        raw = Path(p)
        if not raw.is_absolute():
            raw = Path.cwd() / raw
        return raw.parent.resolve() / raw.name

    roots = [_declared_root_path(p) for p in paths]
    root_modes: dict[Path, int] = {}
    for root_path in roots:
        try:
            root_modes[root_path] = _lstat_mode(root_path, "declared root")
        except FileNotFoundError:
            raise FileNotFoundError(
                f"fingerprint_source: declared input path does not exist: {root_path}"
            )
    # Longest-path-first: the most specific containing root always wins,
    # regardless of the order `paths` was passed in.
    ordered_roots = sorted(roots, key=lambda r: len(r.parts), reverse=True)

    # The common ancestor of every declared root is itself a deterministic
    # function of the ROOT SET (never of the order `paths` was passed in,
    # and never of where that set happens to live on disk): relocating the
    # whole checkout moves the ancestor too, but every root's path RELATIVE
    # to it is unchanged. Label roots by that relative path instead of their
    # absolute one, so the fingerprint is both order- and location-
    # independent while still disambiguating two roots that merely share a
    # basename (e.g. "src" under two different parents).
    common_root = Path(_os.path.commonpath([str(r) for r in roots]))

    def _root_label(root_path: Path) -> str:
        if root_path == common_root:
            return "."
        return root_path.relative_to(common_root).as_posix()

    def _raise(exc: OSError) -> None:
        raise OSError(f"fingerprint_source: could not scan a directory: {exc}") from exc

    def _require_target_within_declared_roots(candidate: Path) -> None:
        # A NESTED symlink's content is otherwise invisible to this
        # fingerprint (only its own identity/target-pointer is hashed --
        # see docstring); that is only safe when its target's actual
        # bytes are ALREADY covered by one of the declared roots (walked
        # separately, in its own right). A target outside every declared
        # root -- an undeclared external file or directory -- could have
        # its content changed with NO effect on the digest, recreating
        # exactly the content-drift blind spot this function exists to
        # close. Fail closed by rejecting it outright; the caller must
        # either declare the target directly as its own root, or avoid
        # this undeclared external symlink. A dangling target (resolves
        # to nothing at all) is allowed through as identity-only: there is
        # no actual content it could be hiding.
        try:
            target_resolved = candidate.resolve(strict=True)
        except FileNotFoundError:
            return  # dangling -- nothing to hide, allow identity-only.
        except OSError as exc:
            # A genuine lookup failure (e.g. permission denied) is NOT
            # the same as "dangling" -- target content may exist and is
            # simply unverifiable here, which must never silently permit
            # an identity-only hash. Fail closed like every other stat
            # failure in this function.
            raise OSError(
                f"fingerprint_source: could not resolve symlink target "
                f"for {candidate}: {exc}"
            ) from exc
        if any(
            target_resolved == r or target_resolved.is_relative_to(r)
            for r in roots
        ):
            return
        raise ValueError(
            f"fingerprint_source: symlink {candidate} points outside "
            f"every declared root (to {target_resolved}) -- its content "
            f"is not otherwise covered by this fingerprint; declare the "
            f"target directly as an additional root, or avoid this "
            f"undeclared external symlink"
        )

    # Each discovered entry is kept under its OWN (never resolved) path, so
    # a symlink is never conflated with whatever it points to. Dict value
    # is "file" for a regular file, or "symlink" for anything that is a
    # symlink (to a file OR a directory). Keying by the un-resolved,
    # as-walked path is also what makes the existing overlapping-root
    # dedup work without an extra resolve() call: the same physical file
    # reached via two overlapping roots is always discovered at the exact
    # same path both times (`root_path` is already resolved up front), so
    # the dict naturally collapses it to one entry.
    entries: dict[Path, str] = {}
    for root_path in roots:
        mode = root_modes[root_path]
        if stat.S_ISLNK(mode):
            # Unlike a NESTED symlink discovered mid-walk (hashed as its
            # own identity -- see docstring), a DECLARED ROOT that is
            # itself a symlink is rejected outright, never silently
            # reduced to link-target text: a root is the caller's own
            # attributable content declaration, so quietly fingerprinting
            # only "where this points" -- while never hashing what is
            # actually read through that path -- would recreate exactly
            # the content-drift blind spot this function exists to close
            # (#5472). The caller must declare the real resolved path (or
            # the symlink's target) instead.
            raise ValueError(
                f"fingerprint_source: declared root is a symlink, not a "
                f"resolved content path: {root_path} -> "
                f"{_os.readlink(root_path)!r} -- declare the real target "
                f"path so its actual content is covered"
            )
        elif stat.S_ISDIR(mode):
            # os.walk with onerror=_raise (NOT Path.rglob/is_file, which
            # silently swallow scandir/stat PermissionError on the
            # supported Python versions) so an unreadable subdirectory
            # fails closed instead of being quietly omitted from the
            # fingerprint. followlinks=False is explicit: a symlinked
            # directory is never descended into (see docstring).
            for dirpath, dirnames, filenames in _os.walk(
                root_path, onerror=_raise, followlinks=False
            ):
                for dname in dirnames:
                    candidate = Path(dirpath) / dname
                    cmode = _lstat_mode(candidate, "nested directory entry")
                    if stat.S_ISLNK(cmode):
                        _require_target_within_declared_roots(candidate)
                        entries[candidate] = "symlink"
                    elif not stat.S_ISDIR(cmode):
                        # os.walk placed this name in dirnames, but an
                        # explicit lstat here disagrees it is a plain
                        # directory (e.g. a device/FIFO/socket, or a
                        # TOCTOU race) -- fail closed rather than
                        # silently traverse or skip it.
                        raise ValueError(
                            f"fingerprint_source: unsupported filesystem "
                            f"object type at {candidate} (not a regular "
                            f"file, directory, or symlink)"
                        )
                for name in filenames:
                    candidate = Path(dirpath) / name
                    cmode = _lstat_mode(candidate, "nested file entry")
                    if stat.S_ISLNK(cmode):
                        _require_target_within_declared_roots(candidate)
                        entries[candidate] = "symlink"
                    elif stat.S_ISREG(cmode):
                        entries[candidate] = "file"
                    else:
                        # A FIFO/device/socket/etc. discovered mid-walk
                        # must never be silently omitted from the
                        # fingerprint (a FIFO could also block indefinitely
                        # if ever read) -- only a regular file, directory,
                        # or symlink is a supported attributable input.
                        raise ValueError(
                            f"fingerprint_source: unsupported filesystem "
                            f"object type at {candidate} (not a regular "
                            f"file, directory, or symlink)"
                        )
        elif stat.S_ISREG(mode):
            entries[root_path] = "file"
        else:
            # A declared root that is a FIFO/device/socket/etc. is not a
            # supported attributable input -- fail closed instead of
            # silently reading it as an ordinary file (a FIFO could block
            # indefinitely).
            raise ValueError(
                f"fingerprint_source: unsupported filesystem object type "
                f"at declared root {root_path} (not a regular file, "
                f"directory, or symlink)"
            )

    def _label(f: Path) -> str:
        # The root's path relative to the common ancestor of every declared
        # root (see `_root_label`), never the root's bare basename (not
        # unique across declared roots) and never its absolute path
        # (would make the digest depend on checkout location).
        for root_path in (f,) if f in root_modes else ordered_roots:
            try:
                rel = f.relative_to(root_path)
            except ValueError:
                continue
            return _root_label(root_path) + "/" + rel.as_posix()
        return f.as_posix()

    def _frame(data: bytes) -> bytes:
        return len(data).to_bytes(8, "big") + data

    def _read_data(f: Path) -> bytes:
        kind = entries[f]
        if kind == "symlink":
            try:
                target = _os.readlink(f)
            except OSError as exc:
                raise OSError(
                    f"fingerprint_source: could not read symlink {f}: {exc}"
                ) from exc
            target = _normalize_windows_extended_path(target)
            if _os.path.isabs(target):
                # An absolute target is only made relocation-invariant
                # when it actually falls INSIDE one of the DECLARED roots
                # themselves (never merely under their common ancestor,
                # which can include undeclared sibling paths the caller
                # never opted into relocating together with the rest --
                # e.g. roots `project/src` and `project/pyproject.toml`
                # share ancestor `project/`, but `project/shared.py` was
                # never declared and is not guaranteed to move with the
                # declared roots). Relocating the WHOLE declared set then
                # moves the target's effective position the same way it
                # moves everything else, so the relative form stays
                # stable. A target outside every declared root (a fixed
                # external location, or on a different drive on Windows)
                # is NOT part of what gets relocated -- its raw absolute
                # text is already the stable identity in that case.
                try:
                    target_resolved = Path(target).resolve()
                except OSError:
                    target_resolved = None
                if target_resolved is not None and any(
                    target_resolved == r or target_resolved.is_relative_to(r)
                    for r in roots
                ):
                    target = _root_label(target_resolved)
            data = Path(target).as_posix().encode("utf-8")
        else:
            try:
                data = f.read_bytes()
            except OSError as exc:
                raise OSError(f"fingerprint_source: could not read {f}: {exc}") from exc
        return data

    digest = hashlib.sha256()

    def _update(f: Path, data: bytes) -> None:
        digest.update(_frame(_label(f).encode("utf-8")))
        # The entry's kind is framed as ITS OWN length-prefixed record,
        # never concatenated into `data` as a string prefix: a regular
        # file whose raw bytes happen to equal some symlink's encoded
        # "target text" must never hash identically to that symlink (or
        # vice versa) just because the two kind tags were distinguished
        # only by unframed leading bytes.
        digest.update(_frame(entries[f].encode("utf-8")))
        digest.update(_frame(data))

    ordered = sorted(entries, key=_label)
    if len(ordered) < 2:
        for f in ordered:
            _update(f, _read_data(f))
    else:
        from collections import deque
        from concurrent.futures import ThreadPoolExecutor

        batch_size = 4
        with ThreadPoolExecutor(
            max_workers=batch_size, thread_name_prefix="runtime-fingerprint",
        ) as pool:
            for offset in range(0, len(ordered), batch_size):
                pending = deque(
                    (f, pool.submit(_read_data, f))
                    for f in ordered[offset:offset + batch_size]
                )
                while pending:
                    f, future = pending.popleft()
                    _update(f, future.result())
                    del future
    return digest.hexdigest()
