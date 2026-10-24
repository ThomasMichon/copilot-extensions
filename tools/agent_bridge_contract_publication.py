"""Serialized, recoverable publication for the existing contract capture owners."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import os
from pathlib import Path
import re

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
    try:
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if codec.read_file(root, relative) != data:
            raise ValueError(f"capture recovery artifact changed: {relative}") from None
    return path


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
    proposed = codec.load(root, read=lambda path: outputs[path] if path in outputs
                          else codec.read_file(root, path))
    chunks = set(proposed.inputs) - {codec.REGISTRY}
    identity = {
        "source_commit": source_commit,
        "outputs": {path: codec.digest(data) for path, data in outputs.items()},
    }
    transaction = codec.digest(codec.encode(identity))
    prefix = JOURNAL + "/" + transaction
    manifest_path = prefix + "/manifest.json"
    with _lock(root):
        validate()
        journal = safe(root, JOURNAL + "/owner.json").parent
        if journal.exists() and any(child.name != transaction for child in journal.iterdir()):
            raise ValueError("another contract publication requires explicit recovery")
        prior = _actual(root, manifest_path)
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
        else:
            manifest = codec.decode(prior)
            if (
                set(manifest) != {"schema", "schema_version", "source_commit", "outputs", "inputs"}
                or manifest["schema"] != "agent-bridge.contract-publication"
                or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
                or any(manifest[key] != value for key, value in identity.items())
            ):
                raise ValueError("capture recovery plan does not match the original source/bytes")
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
