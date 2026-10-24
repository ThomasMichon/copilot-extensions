"""Serialized, recoverable publication for the existing contract capture owners."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import os
from pathlib import Path, PurePosixPath
import re
import stat

from agent_bridge_contract_paths import safe
import agent_bridge_contract_registry as codec

JOURNAL = "plugins/agent-bridge/contract/.capture-publication"
LOCK = JOURNAL + ".lock"


@contextmanager
def _lock(root: Path) -> Iterator[None]:
    path = safe(root, LOCK)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        info = os.fstat(fd)
        if info.st_nlink != 1 or (info.st_dev, info.st_ino) != (
            path.stat().st_dev, path.stat().st_ino,
        ):
            raise ValueError("contract publication lock identity changed")
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def _store(root: Path, relative: str, data: bytes) -> Path:
    path = safe(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if codec.read_file(root, relative) != data:
            raise ValueError(f"capture recovery artifact changed: {relative}")
        return path
    temporary = safe(root, relative + ".pending")
    with temporary.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if path.exists():
        if codec.read_file(root, relative) != data:
            raise ValueError(f"capture recovery artifact changed: {relative}")
        temporary.unlink()
    else:
        os.replace(temporary, path)
    return path


def _recover_chunk_aliases(root: Path, prefix: str, outputs: dict[str, str]) -> None:
    """Retire only exact journal-owned pairs left between link and unlink."""
    for number, (relative, sha256) in enumerate(outputs.items()):
        if not relative.startswith(codec.PROVENANCE):
            continue
        name = PurePosixPath(relative)
        if not re.fullmatch(r"[0-9a-f]{64}\.json", name.name):
            raise ValueError(f"invalid immutable capture output: {relative}")
        target = safe(root, name.parent.as_posix(), directory=True) / name.name
        staged = safe(root, prefix, directory=True) / f"new-{number}.json"
        if not target.exists() or not staged.exists():
            safe(root, relative)
            safe(root, f"{prefix}/new-{number}.json")
            continue
        left, right = target.lstat(), staged.lstat()
        if left.st_nlink == right.st_nlink == 1:
            safe(root, relative)
            safe(root, f"{prefix}/new-{number}.json")
            continue
        if (
            not stat.S_ISREG(left.st_mode) or not stat.S_ISREG(right.st_mode)
            or any(getattr(info, "st_file_attributes", 0) & getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400,
            ) for info in (left, right))
            or left.st_nlink != 2 or right.st_nlink != 2
            or (left.st_dev, left.st_ino) != (right.st_dev, right.st_ino)
            or left.st_size > codec.MAX_ARTIFACT_BYTES
            or name.stem != sha256 or codec.digest(target.read_bytes()) != sha256
        ):
            raise ValueError(f"unowned capture output alias: {relative}")
        staged.unlink()
        safe(root, relative)


def recover_pending_links(root: Path, source_commit: str) -> None:
    """On explicit writes only, remove proven staging aliases before plan reads."""
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ValueError("capture recovery requires an exact source commit")
    with _lock(root):
        journal = safe(root, JOURNAL, directory=True)
        if not journal.exists():
            return
        children = list(journal.iterdir())
        if len(children) != 1 or not re.fullmatch(r"[0-9a-f]{64}", children[0].name):
            raise ValueError("another contract publication requires explicit recovery")
        prefix = JOURNAL + "/" + children[0].name
        prior = _actual(root, prefix + "/manifest.json")
        if prior is None:
            return
        manifest = codec.decode(prior)
        if (
            set(manifest) != {"schema", "schema_version", "source_commit", "outputs", "inputs"}
            or manifest["schema"] != "agent-bridge.contract-publication"
            or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
            or manifest["source_commit"] != source_commit
            or not isinstance(manifest["outputs"], dict)
            or any(not isinstance(path, str) or not isinstance(sha256, str)
                   or not re.fullmatch(r"[0-9a-f]{64}", sha256)
                   for path, sha256 in manifest["outputs"].items())
        ):
            raise ValueError("capture recovery plan does not match the original source/bytes")
        identity = {key: manifest[key] for key in ("source_commit", "outputs")}
        if codec.digest(codec.encode(identity)) != children[0].name:
            raise ValueError("capture recovery journal identity changed")
        _recover_chunk_aliases(root, prefix, manifest["outputs"])


def _actual(root: Path, relative: str) -> bytes | None:
    try:
        return codec.read_file(root, relative)
    except FileNotFoundError:
        return None


def _verify(
    root: Path, manifest: dict, outputs: dict[str, bytes], *, target: str | None = None,
) -> None:
    for item in manifest["inputs"]:
        if target is not None and item["path"] != target:
            continue
        actual = _actual(root, item["path"])
        actual_hash = codec.digest(actual) if actual is not None else None
        allowed = {item["sha256"]}
        if item["path"] in outputs:
            allowed.add(codec.digest(outputs[item["path"]]))
        if actual_hash not in allowed:
            raise ValueError(f"capture publication input changed: {item['path']}")


def publish(
    root: Path, source_commit: str, outputs: dict[str, bytes], *,
    validate: Callable[[], None],
    input_hashes: dict[str, str | None] | None = None,
) -> None:
    """Publish a caller-validated plan; resume only the identical source/byte plan.

    A stable OS lock is released by process exit, never by deleting its path.
    The on-disk journal retains old/new bytes across interruption. It is not
    evidence authority: every resumption still invokes the capture's validator.
    """
    if codec.REGISTRY not in outputs or list(outputs)[-1] != codec.REGISTRY:
        raise ValueError("contract publication must publish the registry last")
    if any(len(data) > codec.MAX_ARTIFACT_BYTES for data in outputs.values()):
        raise ValueError("capture output exceeds the unchanged 1 MiB guard")
    identity = {
        "source_commit": source_commit,
        "outputs": {path: codec.digest(data) for path, data in outputs.items()},
    }
    transaction = codec.digest(codec.encode(identity))
    prefix = JOURNAL + "/" + transaction
    manifest_path = prefix + "/manifest.json"
    with _lock(root):
        journal = safe(root, JOURNAL + "/owner.json").parent
        if journal.exists() and any(child.name != transaction for child in journal.iterdir()):
            raise ValueError("another contract publication requires explicit recovery")
        prior = _actual(root, manifest_path)
        if prior is not None:
            manifest = codec.decode(prior)
            if (
                set(manifest) != {"schema", "schema_version", "source_commit", "outputs", "inputs"}
                or manifest["schema"] != "agent-bridge.contract-publication"
                or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
                or any(manifest[key] != value for key, value in identity.items())
            ):
                raise ValueError("capture recovery plan does not match the original source/bytes")
            _recover_chunk_aliases(root, prefix, identity["outputs"])
        proposed = codec.load(root, read=lambda path: outputs[path] if path in outputs
                              else codec.read_file(root, path))
        chunks = set(proposed.inputs) - {codec.REGISTRY}
        validate()
        if prior is None:
            metadata = codec.load(root)
            inputs = dict(metadata.inputs)
            inputs.update({path: _actual(root, path) for path in outputs})
            hashes = {
                path: codec.digest(data) if data is not None else None
                for path, data in inputs.items()
            }
            hashes.update(input_hashes or {})
            manifest = {
                "schema": "agent-bridge.contract-publication", "schema_version": 1,
                **identity,
                "inputs": [
                    {"path": path, "sha256": sha256}
                    for path, sha256 in sorted(hashes.items())
                ],
            }
            for data in inputs.values():
                if data is not None:
                    _store(root, f"{prefix}/old-{codec.digest(data)}.json", data)
        if not isinstance(manifest["inputs"], list) or any(
            not isinstance(item, dict) or set(item) != {"path", "sha256"}
            or not isinstance(item["path"], str)
            or (item["sha256"] is not None and (
                not isinstance(item["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
            )) for item in manifest["inputs"]
        ):
            raise ValueError("invalid capture recovery input inventory")
        for number, (path, data) in enumerate(outputs.items()):
            _store(root, f"{prefix}/new-{number}.json", data)
            target = safe(root, path)
            if path in chunks and target.exists() and target.read_bytes() != data:
                raise ValueError(f"immutable capture output collision: {path}")
        _store(root, manifest_path, codec.encode(manifest))
        _verify(root, manifest, outputs)
        validate()
        for number, (path, data) in enumerate(outputs.items()):
            if path == codec.REGISTRY:
                _verify(root, manifest, outputs)
                validate()
                for preceding, expected in outputs.items():
                    if preceding != codec.REGISTRY and _actual(root, preceding) != expected:
                        raise ValueError(f"capture published output changed: {preceding}")
            else:
                _verify(root, manifest, outputs, target=path)
            temporary = safe(root, f"{prefix}/new-{number}.json")
            if temporary.read_bytes() != data:
                raise ValueError(f"capture staged output changed: {path}")
            target = safe(root, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            if path in chunks:
                if target.exists():
                    if target.read_bytes() != data:
                        raise ValueError(f"immutable capture output collision: {path}")
                else:
                    # No-replace publication of immutable content-addressed bytes.
                    os.link(temporary, target)
                    temporary.unlink()
            else:
                os.replace(temporary, target)
        if codec.load(root).materialized != proposed.materialized:
            raise ValueError("published contract differs from the validated complete history")
        # Only this exact successful transaction is retired. Failures retain it.
        directory = safe(root, manifest_path).parent
        for item in directory.iterdir():
            safe(root, item.relative_to(root).as_posix()).unlink()
        directory.rmdir()
        journal.rmdir()
