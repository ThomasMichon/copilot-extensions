"""Created-object publication proofs and native-domain recovery (stdlib only)."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any, Callable, Iterator

SCHEMA = "copilot-extensions.publication-proof"
CONTEXT_SCHEMA = "copilot-extensions.publication-context"
MAX_BYTES = 2 * 1024 * 1024
_leases: Any = None


class PublicationProofError(ValueError):
    """Required evidence is absent, invalid, or does not authorize this object."""


def absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values:
        if key in result:
            raise PublicationProofError(f"Duplicate proof field: {key}")
        result[key] = value
    return result


def _windows_owned(fd: int) -> bool:
    return lease_component().windows_owned_file(fd)


def _plain_parents(path: Path) -> None:
    for parent in (path.parent, *path.parent.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise PublicationProofError(f"Proof path has an unsafe parent: {parent}")


@dataclass(frozen=True)
class PhysicalState:
    device: int
    inode: int
    mode: int
    size: int
    modified: int
    digest: str

    @classmethod
    def parse(cls, value: Any) -> PhysicalState:
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise PublicationProofError("Invalid physical proof fields")
        if any(type(value[key]) is not int or value[key] < 0 for key in (
            "device", "inode", "mode", "size", "modified",
        )) or not isinstance(value["digest"], str) or not re.fullmatch("[0-9a-f]{64}", value["digest"]):
            raise PublicationProofError("Invalid physical proof values")
        if not stat.S_ISREG(value["mode"]):
            raise PublicationProofError("Proof does not describe an ordinary file")
        return cls(**value)


def state_fd(fd: int, *, owned: bool = True) -> PhysicalState:
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise PublicationProofError("Proof requires a single-link ordinary file")
    if owned and not (_windows_owned(fd) if os.name == "nt" else before.st_uid == os.getuid()):
        raise PublicationProofError("Proof object is not owned by this principal")
    position = os.lseek(fd, 0, os.SEEK_CUR)
    previous_mode = None
    if os.name == "nt":
        import msvcrt

        previous_mode = msvcrt.setmode(fd, os.O_BINARY)
    digest = hashlib.sha256()
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        while block := os.read(fd, 65536):
            digest.update(block)
    finally:
        os.lseek(fd, position, os.SEEK_SET)
        if previous_mode is not None:
            msvcrt.setmode(fd, previous_mode)
    after = os.fstat(fd)
    fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_nlink")
    if any(getattr(before, key) != getattr(after, key) for key in fields):
        raise PublicationProofError("Created object changed during proof capture")
    return PhysicalState(after.st_dev, after.st_ino, after.st_mode,
                         after.st_size, after.st_mtime_ns, digest.hexdigest())


def state_path(path: Path, *, private: bool = False, owned: bool = True) -> PhysicalState | None:
    _plain_parents(path)
    try:
        named = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(named.st_mode) or getattr(named, "st_file_attributes", 0) & 0x400:
        raise PublicationProofError(f"Not an ordinary proof object: {path}")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        result = state_fd(fd, owned=owned)
        if private and os.name != "nt" and result.mode & 0o022:
            raise PublicationProofError(f"Proof file permits untrusted writes: {path}")
        final = path.lstat()
        if (named.st_dev, named.st_ino) != (result.device, result.inode) or (
            final.st_dev, final.st_ino
        ) != (result.device, result.inode):
            raise PublicationProofError(f"Named proof object changed: {path}")
        return result
    finally:
        os.close(fd)


def group(root: Path, target: Path) -> str:
    if target.name.lower() in ("payload-dir", "stamped-version", "payload-origin"):
        return "redirects"
    if target.name.lower() in ("current-version", "last-known-good"):
        return "selectors"
    return "artifact." + hashlib.sha256(str(target).lower().encode()).hexdigest()


def lease_component() -> Any:
    global _leases
    if _leases is None:
        spec = importlib.util.spec_from_file_location(
            __name__ + "_leases", Path(__file__).with_name("versioned_runtime_leases.py"),
        )
        if spec is None or spec.loader is None:
            raise PublicationProofError("Publication lease component is unavailable")
        _leases = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_leases)
    return _leases


@contextmanager
def leases(root: Path, groups: list[str], *,
           open_lock: Callable[[Path, str], int] | None = None) -> Iterator[None]:
    with ExitStack() as stack:
        for key in sorted(set(groups)):
            stack.enter_context(lease_component()._kernel_lease(
                root, key, kind="publication", shared=False, open_lock=open_lock,
            ))
        yield


@contextmanager
def target_leases(root: Path, targets: list[Path], *,
                  open_lock: Callable[[Path, str], int] | None = None) -> Iterator[None]:
    bindings = {(os.path.normcase(str(absolute(path).parent)), group(root, absolute(path))) for path in targets}
    with ExitStack() as stack:
        for parent, key in sorted(bindings):
            stack.enter_context(leases(Path(parent), [key], open_lock=open_lock))
        yield


def _publish_receipt(source: Path, target: Path) -> None:
    if os.name == "nt":
        os.rename(source, target)
        return
    import ctypes

    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux"):
        rename = getattr(library, "renameat2", None)
        if rename is None:
            raise OSError(errno.ENOTSUP, "Atomic no-replace receipt publication is unavailable")
        rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        arguments = (-100, os.fsencode(source), -100, os.fsencode(target), 1)
    elif sys.platform == "darwin":
        rename = getattr(library, "renamex_np", None)
        if rename is None:
            raise OSError(errno.ENOTSUP, "Atomic no-replace receipt publication is unavailable")
        rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        arguments = (os.fsencode(source), os.fsencode(target), 0x00000004)
    else:
        raise OSError(errno.ENOTSUP, "Atomic no-replace receipt publication is unavailable")
    rename.restype = ctypes.c_int
    if rename(*arguments) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(target))


class Receipt:
    """Append and fsync created-stage records before their corresponding rename."""

    def __init__(self, path: Path, root: Path, targets: list[Path], producer: str) -> None:
        self.path, self.root = absolute(path), absolute(root)
        self.targets = [absolute(target) for target in targets]
        _plain_parents(self.path)
        fd, name = tempfile.mkstemp(prefix=".receipt-", dir=self.path.parent)
        temporary = Path(name)
        created = os.fstat(fd)
        self.stream = os.fdopen(fd, "r+b", buffering=0)
        self.sequence = 0
        self.finished = False
        try:
            self._append({
                "schema": SCHEMA, "version": 1, "kind": "header", "root": str(self.root),
                "targets": [str(target) for target in self.targets], "producer": producer,
                "file_identity": [created.st_dev, created.st_ino],
            })
            expected = state_fd(self.stream.fileno())
            self.stream.close()
            _publish_receipt(temporary, self.path)
            if os.name != "nt":
                directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            fd = os.open(self.path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) |
                         getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
            self.stream = os.fdopen(fd, "r+b", buffering=0)
            if state_fd(self.stream.fileno()) != expected or state_path(
                self.path, private=True,
            ) != expected:
                raise PublicationProofError("Receipt changed before producer append custody")
            self.stream.seek(0, os.SEEK_END)
        except BaseException:
            self.stream.close()
            raise

    def _append(self, value: dict[str, Any]) -> None:
        data = (json.dumps(value, sort_keys=True) + "\n").encode("utf-8")
        if self.stream.tell() + len(data) > MAX_BYTES or self.stream.write(data) != len(data):
            raise PublicationProofError("Short ownership receipt write")
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def created(self, target: Path, fd: int, *, content: bytes, phase: str = "forward") -> PhysicalState:
        target = absolute(target)
        if target not in self.targets or phase not in ("forward", "recovery"):
            raise PublicationProofError("Created-stage proof is outside its target binding")
        value = state_fd(fd)
        if value.digest != hashlib.sha256(content).hexdigest() or value.size != len(content):
            raise PublicationProofError("Created stage does not contain its producer-declared bytes")
        self.sequence += 1
        self._append({"kind": "created", "sequence": self.sequence,
                      "target": str(target), "phase": phase, "state": asdict(value)})
        return value

    def finish(self, outcome: str) -> None:
        if self.finished or self.stream.closed:
            return
        try:
            self._append({"kind": "finish", "sequence": self.sequence, "outcome": outcome})
            self.finished = True
        finally:
            self.stream.close()

    def noop(self, target: Path, state: PhysicalState) -> None:
        target = absolute(target)
        if target not in self.targets:
            raise PublicationProofError("No-op is outside its target binding")
        self.sequence += 1
        self._append({"kind": "noop", "sequence": self.sequence,
                      "target": str(target), "state": asdict(state)})


def _read_json(path: Path, *, lines: bool = False, recovery_only: bool = False) -> Any:
    before = state_path(path, private=True)
    if before is None or before.size > MAX_BYTES:
        raise PublicationProofError(f"Required proof is missing or oversized: {path}")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if state_fd(fd) != before:
            raise PublicationProofError(f"Proof binding changed before read: {path}")
        blocks = []
        while block := os.read(fd, min(65536, MAX_BYTES + 1 - sum(map(len, blocks)))):
            blocks.append(block)
            if sum(map(len, blocks)) > MAX_BYTES:
                raise PublicationProofError("Proof exceeded its size bound while read")
        data = b"".join(blocks)
        if state_fd(fd) != before:
            raise PublicationProofError(f"Proof changed while read: {path}")
    finally:
        os.close(fd)
    if state_path(path, private=True) != before:
        raise PublicationProofError(f"Proof changed while read: {path}")
    if not data.endswith(b"\n"):
        if not (lines and recovery_only):
            raise PublicationProofError(f"Proof is incomplete: {path}")
        data = data[:data.rfind(b"\n") + 1]
    try:
        if lines:
            return [json.loads(line, object_pairs_hook=_pairs) for line in data.splitlines()]
        return json.loads(data, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError) as error:
        raise PublicationProofError(f"Invalid proof data: {path}") from error


def read_receipt(
    path: Path, root: Path, allowed: set[Path], *, recovery_only: bool = False,
) -> dict[Path, list[PhysicalState]]:
    """Read created-object proofs; a recovery-only prefix never certifies publication."""
    records = _read_json(path, lines=True, recovery_only=recovery_only)
    if not records or len(records) > 1024 or (not recovery_only and len(records) < 2):
        raise PublicationProofError("Required proof has no complete producer outcome")
    header = records[0]
    if not isinstance(header, dict) or set(header) != {
        "schema", "version", "kind", "root", "targets", "producer", "file_identity",
    } or header["schema"] != SCHEMA or type(header["version"]) is not int or header["version"] != 1 or header["kind"] != "header":
        raise PublicationProofError("Invalid proof header")
    if header["root"] != str(root) or header["producer"] not in (
        "redirects", "activate", "session-stamp", "outer-recovery", "files",
    ):
        raise PublicationProofError("Proof installation binding does not match")
    actual = state_path(path, private=True)
    identity = header["file_identity"]
    if not isinstance(identity, list) or len(identity) != 2 or any(type(value) is not int for value in identity) or (
        actual is None or identity != [actual.device, actual.inode]
    ):
        raise PublicationProofError("Receipt is not its producer-created file")
    targets = header["targets"]
    if (not isinstance(targets, list) or not targets or any(not isinstance(value, str) for value in targets)
            or len(targets) != len(set(targets))
            or any(Path(value) not in allowed or str(absolute(Path(value))) != value for value in targets)):
        raise PublicationProofError("Proof target binding does not match")
    finish = records[-1] if isinstance(records[-1], dict) and records[-1].get("kind") == "finish" else None
    if finish is None and not recovery_only:
        raise PublicationProofError("Required proof is incomplete")
    result = {Path(value): [] for value in targets}
    for sequence, record in enumerate(records[1:-1] if finish is not None else records[1:], 1):
        if isinstance(record, dict) and record.get("kind") == "noop":
            if header["producer"] != "files" or set(record) != {"kind", "sequence", "target", "state"} or (
                type(record["sequence"]) is not int or record["sequence"] != sequence
                or not isinstance(record["target"], str) or Path(record["target"]) not in result
            ):
                raise PublicationProofError("Invalid no-op publication record")
            PhysicalState.parse(record["state"])
            continue
        if not isinstance(record, dict) or set(record) != {"kind", "sequence", "target", "phase", "state"} or (
            record["kind"] != "created" or type(record["sequence"]) is not int
            or record["sequence"] != sequence or record["phase"] not in ("forward", "recovery")
            or not isinstance(record["target"], str) or Path(record["target"]) not in result
        ):
            raise PublicationProofError("Invalid created-stage proof record")
        result[Path(record["target"])].append(PhysicalState.parse(record["state"]))
    if finish is not None and (set(finish) != {"kind", "sequence", "outcome"} or (
        finish["kind"] != "finish" or type(finish["sequence"]) is not int
        or finish["sequence"] != len(records) - 2 or finish["outcome"] not in ("success", "error")
    )):
        raise PublicationProofError("Required proof is incomplete")
    return result


def create_context(path: Path, root: Path, bindings: list[list[str]]) -> None:
    entries: list[dict[str, Any]] = []
    targets: set[Path] = set()
    for target, backup, digest in bindings:
        destination = absolute(Path(target))
        if destination in targets:
            raise PublicationProofError("Duplicate context target")
        targets.add(destination)
        saved = absolute(Path(backup)) if backup else None
        value = state_path(saved) if saved else None
        if saved and (value is None or value.digest != digest):
            raise PublicationProofError("Outer backup does not match its declared bytes")
        entries.append({"target": str(destination), "backup": str(saved) if saved else None,
                        "state": asdict(value) if value else None})
    _plain_parents(path)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    created = os.fstat(fd)
    document = {"schema": CONTEXT_SCHEMA, "version": 1, "root": str(absolute(root)), "entries": entries,
                "file_identity": [created.st_dev, created.st_ino]}
    with os.fdopen(fd, "wb") as stream:
        stream.write((json.dumps(document, sort_keys=True) + "\n").encode())
        stream.flush()
        os.fsync(stream.fileno())


def read_context(path: Path) -> tuple[Path, dict[Path, tuple[Path | None, PhysicalState | None]]]:
    document = _read_json(path)
    if not isinstance(document, dict) or set(document) != {"schema", "version", "root", "entries", "file_identity"} or (
        document["schema"] != CONTEXT_SCHEMA or type(document["version"]) is not int or document["version"] != 1
        or not isinstance(document["root"], str) or not isinstance(document["entries"], list)
    ):
        raise PublicationProofError("Invalid publication context")
    actual = state_path(path, private=True)
    if actual is None or document["file_identity"] != [actual.device, actual.inode]:
        raise PublicationProofError("Context is not its caller-created file")
    root = absolute(Path(document["root"]))
    if str(root) != document["root"]:
        raise PublicationProofError("Aliased publication root")
    result = {}
    for entry in document["entries"]:
        if not isinstance(entry, dict) or set(entry) != {"target", "backup", "state"} or not isinstance(entry["target"], str):
            raise PublicationProofError("Invalid context target")
        target = absolute(Path(entry["target"]))
        if str(target) != entry["target"] or target in result:
            raise PublicationProofError("Aliased context target")
        if entry["backup"] is not None and not isinstance(entry["backup"], str):
            raise PublicationProofError("Invalid context backup path")
        backup = absolute(Path(entry["backup"])) if isinstance(entry["backup"], str) else None
        value = PhysicalState.parse(entry["state"]) if entry["state"] is not None else None
        if (backup is None) != (value is None) or (backup and backup.parent != absolute(path.parent)):
            raise PublicationProofError("Invalid context backup")
        result[target] = (backup, value)
    return root, result


def _proofs(
    receipts: list[Path], root: Path, entries: dict, expected: list[list[str]], *,
    recovery_only: bool = False,
) -> dict[Path, list[PhysicalState]]:
    result: dict[Path, list[PhysicalState]] = {}
    digests = {target: ({before.digest} if before else set()) for target, (_backup, before) in entries.items()}
    for target, digest in expected:
        path = absolute(Path(target))
        if path not in entries or not re.fullmatch("[0-9a-f]{64}", digest):
            raise PublicationProofError("Invalid declared publication bytes")
        digests[path].add(digest)
    if not receipts or len(receipts) != len(set(receipts)):
        raise PublicationProofError("Required producer receipts are missing or duplicated")
    for path in receipts:
        for target, values in read_receipt(path, root, set(entries), recovery_only=recovery_only).items():
            if any(value.digest not in digests[target] for value in values):
                raise PublicationProofError(f"Producer bytes were not declared by its caller: {target}")
            result.setdefault(target, []).extend(values)
    return result


def check_owned(context: Path, receipts: list[Path], required: list[Path], expected: list[list[str]]) -> None:
    root, entries = read_context(context)
    required = [absolute(path) for path in required]
    if any(path not in entries for path in required):
        raise PublicationProofError("Recovery target is outside its context")
    with target_leases(root, required):
        proofs = _proofs(receipts, root, entries, expected)
        for path in required:
            current = state_path(path)
            if current is None or current not in proofs.get(path, ()):
                raise PublicationProofError(f"Not a producer-created publication object: {path}")


def cleanup_proofs(context: Path, receipts: list[Path], expected: list[list[str]]) -> None:
    root, entries = read_context(context)
    _proofs(receipts, root, entries, expected)
    states = {path: state_path(path, private=True) for path in [*receipts, context]}
    for path, before in states.items():
        try:
            if state_path(path, private=True) != before:
                raise PublicationProofError(f"Proof changed before cleanup: {path}")
            path.unlink()
        except (OSError, PublicationProofError) as error:
            print(f"Publication proof cleanup warning; retained {path}: {error}", file=sys.stderr)


def _stage(root: Path, target: Path, data: bytes, mode: int, receipt: Receipt, *, phase: str = "recovery") -> tuple[Path, PhysicalState]:
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.owned-", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w+b") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            temporary.chmod(stat.S_IMODE(mode))
            proof = receipt.created(target, stream.fileno(), content=data, phase=phase)
        return temporary, proof
    except BaseException:
        # A stage that could not be durably proved is retained, never guessed away.
        raise


def recover_owned(context: Path, receipts: list[Path], required: list[Path], output: Path, expected: list[list[str]]) -> None:
    root, entries = read_context(context)
    required = [absolute(path) for path in required]
    if not required or any(path not in entries for path in required):
        raise PublicationProofError("Invalid native recovery target set")
    with target_leases(root, required):
        result = Receipt(output, root, required, "outer-recovery")
        try:
            _recover_locked(root, entries, _proofs(receipts, root, entries, expected), required, result)
        except BaseException:
            result.finish("error")
            raise


def _recover_locked(root: Path, entries: dict, proofs: dict, required: list[Path], result: Receipt) -> None:
    try:
        for path in required:
            if state_path(path) not in proofs.get(path, ()):
                raise PublicationProofError(f"Foreign publication object: {path}")
            backup, before = entries[path]
            if backup and state_path(backup) != before:
                raise PublicationProofError(f"Recovery backup changed: {backup}")
        for path in required:
            current = state_path(path)
            if current not in proofs.get(path, ()):
                raise PublicationProofError(f"Publication changed before recovery: {path}")
            backup, before = entries[path]
            if backup is None:
                path.unlink()
                if state_path(path) is not None:
                    raise PublicationProofError(f"Owned removal failed: {path}")
            else:
                if state_path(backup) != before:
                    raise PublicationProofError(f"Recovery backup changed: {backup}")
                data = backup.read_bytes()
                temporary, created = _stage(root, path, data, before.mode, result)
                if state_path(backup) != before or state_path(path) != current:
                    raise PublicationProofError("Recovery bindings changed during staging")
                os.replace(temporary, path)
                if state_path(path) != created:
                    raise PublicationProofError(f"Recovery result is not its created stage: {path}")
        result.finish("success")
    except BaseException:
        result.finish("error")
        raise


def recover_uncontested(
    context: Path, receipts: list[Path], output: Path, expected: list[list[str]],
    baseline: dict[Path, PhysicalState | None], *, recovery_only: bool = False,
) -> list[Path]:
    """Recover proven outputs only; foreign objects never authorize copyback.

    Explicit recovery may consume durable receipt prefixes after producer death.
    Such prefixes authorize reversal only, never successful publication.
    """
    if type(recovery_only) is not bool:
        raise PublicationProofError("Invalid recovery-only control")
    root, entries = read_context(context)
    if set(baseline) != set(entries):
        raise PublicationProofError("Selective recovery baseline differs from its context")
    with target_leases(root, list(entries)):
        proofs = _proofs(receipts, root, entries, expected, recovery_only=recovery_only)
        return _recover_uncontested_locked(root, entries, proofs, output, baseline)


def _recover_uncontested_locked(root: Path, entries: dict, proofs: dict, output: Path, baseline: dict) -> list[Path]:
    owned, foreign = [], []
    for path, (backup, before) in entries.items():
        if backup and state_path(backup) != before:
            raise PublicationProofError(f"Recovery backup changed: {backup}")
        try:
            current = state_path(path)
        except (OSError, PublicationProofError):
            foreign.append(path)
            continue
        if current == baseline[path]:
            continue
        if current in proofs.get(path, ()):
            owned.append(path)
        else:
            foreign.append(path)
    if owned:
        _recover_locked(root, entries, proofs, owned, Receipt(output, root, owned, "outer-recovery"))
    return foreign


@dataclass(frozen=True)
class CopyBinding:
    source: Path
    target: Path
    digest: str
    executable: bool = False
    normalized_noop: bool = False


class CopyPublication:
    """Created-FD copy custody, retained until the owner's actual settlement."""

    def __init__(
        self, root: Path, workspace: Path, bindings: list[CopyBinding],
        validate_inputs: Callable[[], None] | None = None,
        *, before_effects: Callable[[], None] | None = None,
    ) -> None:
        self.root, self.workspace = absolute(root), absolute(workspace)
        self.bindings = [
            CopyBinding(absolute(item.source), absolute(item.target), item.digest,
                        item.executable, item.normalized_noop) for item in bindings
        ]
        targets = [os.path.normcase(str(item.target)) for item in self.bindings]
        if not targets or len(set(targets)) != len(targets):
            raise PublicationProofError("Copy targets are empty or duplicated")
        for item in self.bindings:
            if (not re.fullmatch("[0-9a-f]{64}", item.digest)
                    or type(item.executable) is not bool or type(item.normalized_noop) is not bool
                    or os.path.normcase(str(item.source)) in targets):
                raise PublicationProofError("Invalid declared source/target copy binding")
        self.validate_inputs = validate_inputs or (lambda: None)
        self.before_effects = before_effects or (lambda: None)
        self.context = self.workspace / "copy-context.json"
        self.receipts = [self.workspace / "copy-forward.jsonl"]
        self.expected = [[str(item.target), item.digest] for item in self.bindings]
        self.baseline: dict[Path, PhysicalState | None] = {}
        self.backups: dict[Path, PhysicalState] = {}
        self.recovery_entries: dict[Path, tuple[Path | None, PhysicalState | None]] = {}
        self.published = False
        self.outputs: dict[Path, PhysicalState] = {}
        self._custody: ExitStack | None = None

    def _hold(self):
        from contextlib import nullcontext

        if self._custody is not None:
            raise PublicationProofError("Copy publication custody is already held")
        self._custody = ExitStack()
        self._custody.enter_context(target_leases(self.root, [item.target for item in self.bindings]))
        return nullcontext()

    def _release(self) -> None:
        if self._custody is not None:
            self._custody.close()
            self._custody = None

    def verify(self) -> None:
        if self._custody is None:
            raise PublicationProofError("Copy publication custody is not held")
        for path, expected in self.outputs.items():
            if state_path(path) != expected:
                raise PublicationProofError(f"Copy target changed before settlement: {path}")

    @staticmethod
    def _bytes(path: Path, expected: PhysicalState, *, owned: bool = True) -> bytes:
        value = path.read_bytes()
        if (state_path(path, owned=owned) != expected or hashlib.sha256(value).hexdigest() != expected.digest):
            raise PublicationProofError(f"Copy input changed while reading: {path}")
        return value

    def publish(self) -> CopyPublication:
        receipt = None
        try:
            with self._hold():
                self.validate_inputs()
                sources: dict[Path, tuple[PhysicalState, bytes]] = {}
                # Validate every target before the first backup or target write.
                for item in self.bindings:
                    source = state_path(item.source, owned=False)
                    if source is None or source.digest != item.digest:
                        raise PublicationProofError(f"Declared copy source changed: {item.source}")
                    sources[item.source] = (source, self._bytes(item.source, source, owned=False))
                    self.baseline[item.target] = state_path(item.target)
                bindings = []
                for index, item in enumerate(self.bindings):
                    before = self.baseline[item.target]
                    backup = self.workspace / f"copy-before.{index}"
                    if before is not None:
                        data = self._bytes(item.target, before)
                        fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
                        with os.fdopen(fd, "w+b") as stream:
                            stream.write(data)
                            stream.flush()
                            os.fsync(stream.fileno())
                            backup.chmod(stat.S_IMODE(before.mode))
                            self.backups[backup] = state_fd(stream.fileno())
                    bindings.append([str(item.target), str(backup) if before else "",
                                     before.digest if before else ""])
                    self.recovery_entries[item.target] = (
                        (backup, self.backups[backup]) if before else (None, None)
                    )
                create_context(self.context, self.root, bindings)
                receipt = Receipt(self.receipts[0], self.root, list(self.baseline), "files")
                self.before_effects()
                for item in self.bindings:
                    source, data = sources[item.source]
                    if state_path(item.source, owned=False) != source or state_path(item.target) != self.baseline[item.target]:
                        raise PublicationProofError(f"Copy bindings changed before publication: {item.target}")
                    before = self.baseline[item.target]
                    if item.normalized_noop and before is not None:
                        old = self._bytes(item.target, before)
                        normalize = lambda value: value.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
                        if normalize(old) == normalize(data):
                            receipt.noop(item.target, before)
                            self.outputs[item.target] = before
                            continue
                    mode = stat.S_IMODE(source.mode) | (0o111 if item.executable else 0)
                    stage, created = _stage(self.root, item.target, data, mode, receipt, phase="forward")
                    self.outputs[item.target] = created
                    if state_path(item.source, owned=False) != source or state_path(item.target) != before:
                        raise PublicationProofError(f"Copy bindings changed during staging: {item.target}")
                    os.replace(stage, item.target)
                    if state_path(item.target) != created:
                        raise PublicationProofError(f"Copy lost its created object: {item.target}")
                for source, (before, _data) in sources.items():
                    if state_path(source, owned=False) != before:
                        raise PublicationProofError(f"Copy source changed during publication: {source}")
                self.validate_inputs()
                self.verify()
                receipt.finish("success")
                self.published = True
            return self
        except BaseException as failure:
            if receipt is not None:
                receipt.finish("error")
                try:
                    self.rollback()
                except (OSError, PublicationProofError) as recovery:
                    raise PublicationProofError(
                        f"Copy failed ({failure}); recovery incomplete ({recovery}); retained {self.workspace}"
                    ) from failure
            else:
                self._release()
            raise

    def rollback(self) -> None:
        if self._custody is None:
            raise PublicationProofError("Copy recovery requires retained publication custody")
        output = self.workspace / f"copy-recovery.{len(self.receipts)}.jsonl"
        try:
            root, entries = self._context()
            proofs = _proofs(self.receipts, root, entries, self.expected)
            foreign = _recover_uncontested_locked(root, entries, proofs, output, self.baseline)
        finally:
            if output.exists():
                self.receipts.append(output)
            self._release()
        if foreign:
            raise PublicationProofError(
                "Foreign copy targets preserved; recovery evidence retained: "
                + ", ".join(map(str, foreign)) + f"; {self.workspace}"
            )

    def _context(self) -> tuple[Path, dict[Path, tuple[Path | None, PhysicalState | None]]]:
        root, entries = read_context(self.context)
        if root != self.root or entries != self.recovery_entries:
            raise PublicationProofError("Copy recovery context differs from its captured bindings")
        return root, entries

    def retain(self) -> None:
        """Settle a successful copy while retaining its durable recovery proofs."""
        if not self.published:
            raise PublicationProofError("Only a successful publication can retain its proofs")
        self.verify()
        for path, before in self.backups.items():
            if state_path(path) != before:
                raise PublicationProofError(f"Backup changed before retained settlement: {path}")
        root, entries = self._context()
        proofs = _proofs(self.receipts, root, entries, self.expected)
        for path, created in self.outputs.items():
            if created != self.baseline[path] and created not in proofs.get(path, ()):
                raise PublicationProofError(f"Captured copy output is absent from its proofs: {path}")
        self._release()

    def keep(self) -> None:
        """Metadata cleanup is not a new mutable-source postcommit gate."""
        try:
            self.verify()
            self._context()
            for path, before in self.backups.items():
                if state_path(path) != before:
                    raise PublicationProofError(f"Backup changed before cleanup: {path}")
            for path, before in self.backups.items():
                if state_path(path) != before:
                    raise PublicationProofError(f"Backup changed before cleanup: {path}")
                path.unlink()
            cleanup_proofs(self.context, self.receipts, self.expected)
        except (OSError, PublicationProofError) as error:
            print(f"Copy evidence retained after settlement: {self.workspace}: {error}", file=sys.stderr)
        finally:
            self._release()


def publish_selectors(
    root: Path, version: str, receipt_path: Path | None, last_good: Any,
    *, before_effects: Callable[[], None] | None = None,
) -> None:
    """Publish while activate/release_dev already holds the existing selector lease."""
    targets = [root / "current-version", root / "last-known-good"]
    receipt = Receipt(receipt_path, root, targets, "activate") if receipt_path else None
    try:
        if before_effects is not None:
            before_effects()
        for index, target in enumerate(targets):
            if index and receipt is None:
                last_good(root, version)
                continue
            fd, name = tempfile.mkstemp(prefix=f".{target.name}.activate-", dir=root)
            temporary = Path(name)
            with os.fdopen(fd, "w+b") as stream:
                stream.write((version + "\n").encode())
                stream.flush()
                os.fsync(stream.fileno())
                created = receipt.created(target, stream.fileno(), content=(version + "\n").encode()) if receipt else None
            os.replace(temporary, target)
            if created is not None and state_path(target) != created:
                raise PublicationProofError(f"Selector lost its created object: {target}")
        if receipt:
            receipt.finish("success")
    except BaseException:
        if receipt:
            receipt.finish("error")
        raise
