"""Opt-in final-compartment production with verified historical handover."""

from __future__ import annotations

from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
from typing import Any, Callable, Iterator

from agent_procutil import scratch
from agent_procutil._daemon_instance_owner import open_private
from single_instance_lease.operation import OperationLock

from . import resource_producer as producer, tracking, tracking_write
from .artifact_output_policy import member_name
from .artifact_root import AdmittedRoot, _windows_error, admit_root, directory_identity
from .artifact_scratch import _boundaries
from .producer_allocation import MAX_GENERATION
from .resource_delivery import MAX_BYTES, MAX_FILES, valid_summary
from .tracking_claims import parse_claim_ref
from .tracking_producer_write import _ProducerClaim, _matched_claim, _owned_record

SCHEMA = "agent-worktrees.user-output/v1"
NAMESPACE = "worktree-output"
MARKER = ".copilot-output.json"
_NATIVE_ID_MAX = (1 << 128) - 1


def capabilities() -> dict[str, Any]:
    """Advertise this source provider separately from scratch and package versions."""
    return {
        "schema": SCHEMA, "allocator": 1, "verified_delivery": 1,
        "incarnation_bound_reservation": 1,
        "max_files": MAX_FILES, "max_bytes": MAX_BYTES,
        "links": "unsupported", "cloud_acknowledgment": False, "live_rollout": False,
    }


def _arguments(path: Path, owner: str) -> dict[str, Any]:
    return {
        "schema": producer.SCHEMA, "version": producer.VERSION,
        "yaml_path": str(path), "owner_ref": owner,
    }


def _record(path: Path, owner: str, *, mutable: bool = False) -> tracking.WorktreeRecord:
    with tracking._RecordLock(path, require_sidecar=True):
        record, _ = _owned_record(_arguments(path, owner), path, mutable=mutable)
        if record.kind in tracking.MANAGED_KINDS or not record.creation_nonce:
            raise ValueError("user output requires an incarnation-bound owner with retained history")
        return record


def _invoke(path: Path, owner: str, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    result = tracking_write.run_direct("resource_producer", {
        **_arguments(path, owner), "operation": operation, "input": payload,
    }, reason="native user-output producer uses existing direct RMW authority")
    if not result.get("ok"):
        raise ValueError(f"user-output {operation} refused; inspect existing responsibility")
    return dict(result)


def _summary(manifest: object) -> dict[str, Any]:
    if not isinstance(manifest, list) or not 1 <= len(manifest) <= MAX_FILES:
        raise ValueError("user-output manifest exceeds its file bound")
    names = []
    total = 0
    for entry in manifest:
        if not isinstance(entry, dict) or set(entry) != {"name", "bytes", "sha256"}:
            raise ValueError("invalid user-output manifest member")
        name = member_name(entry["name"])
        if name.casefold() == MARKER or name.casefold() in names:
            raise ValueError("duplicate or reserved user-output member")
        names.append(name.casefold())
        if not valid_summary({"files": 1, "bytes": entry["bytes"], "sha256": entry["sha256"]}):
            raise ValueError("invalid user-output manifest integrity")
        total += entry["bytes"]
    result = {
        "files": len(manifest), "bytes": total,
        "sha256": hashlib.sha256(producer.canonical_json(manifest).encode("ascii")).hexdigest(),
    }
    if not valid_summary(result):
        raise ValueError("user-output manifest exceeds its byte bound")
    return result


def _selection(value: object) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "target", "root", "members", "destination", "origin", "root_admitted"}
        or value["schema"] != "agent-worktrees.user-output-destination/v1"
        or not isinstance(value["target"], str) or value["target"] not in {"explicit", "onedrive", "downloads"}
        or value["root_admitted"] is not False
        or not isinstance(value["root"], str) or not Path(value["root"]).is_absolute()
        or not isinstance(value["members"], list) or not 1 <= len(value["members"]) <= 16
        or not isinstance(value["destination"], str) or len(value["destination"]) > 2048
    ):
        raise ValueError("user output needs an inert owner-resolved destination")
    for name in value["members"]:
        member_name(name)
    if value["destination"] != str(Path(value["root"]).joinpath(*value["members"])):
        raise ValueError("user-output destination disagrees with its selected members")
    return value


def _recovery(value: object) -> dict[str, Any]:
    if (
        not isinstance(value, dict) or set(value) != {
            "schema", "root", "root_identity", "members", "content", "nonce",
            "incarnation", "request_hash", "created_at",
        }
        or value["schema"] != SCHEMA or not producer.is_valid_token(value["nonce"])
        or not isinstance(value["incarnation"], str) or not 1 <= len(value["incarnation"]) <= 128
        or not isinstance(value["root"], str) or not Path(value["root"]).is_absolute()
        or "\x00" in value["root"] or ".." in Path(value["root"]).parts
        or not isinstance(value["members"], list) or not 1 <= len(value["members"]) <= 16
        or not valid_summary(value["content"])
        or not valid_summary({"files": 1, "bytes": 0, "sha256": value["request_hash"]})
        or not isinstance(value["created_at"], str) or len(value["created_at"]) > 64
    ):
        raise ValueError("invalid native user-output recovery metadata")
    for member in value["members"]:
        member_name(member)
    identity = value["root_identity"]
    if (
        not isinstance(identity, dict) or set(identity) != {"device", "inode"}
        or any(type(number) is not int or not 0 <= number <= _NATIVE_ID_MAX for number in identity.values())
        or not identity["inode"]
    ):
        raise ValueError("invalid native user-output root identity")
    if (
        len(str(Path(value["root"]).joinpath(*value["members"]))) > 2048
        or datetime.fromisoformat(value["created_at"]).tzinfo is None
    ):
        raise ValueError("invalid native user-output destination or creation time")
    return value


@contextmanager
def _operation(path: Path, operation_id: str) -> Iterator[None]:
    suffix = hashlib.sha256(operation_id.encode("ascii")).hexdigest()[:24]
    lock = OperationLock(path.with_name(path.name + ".output-" + suffix + ".lock"))
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


@contextmanager
def _destination(
    recovery: dict[str, Any], forbidden: tuple[Path, ...], *, create: bool = False,
) -> Iterator[AdmittedRoot]:
    recovery = _recovery(recovery)
    with ExitStack() as stack:
        root = stack.enter_context(admit_root(
            Path(recovery["root"]), forbidden=forbidden, expected=recovery["root_identity"], private=False,
        ))
        for index, name in enumerate(recovery["members"]):
            final = index == len(recovery["members"]) - 1
            if create:
                try:
                    root.create_child(name)
                except FileExistsError:
                    if final:
                        raise ValueError("user-output destination already exists; no overwrite or adoption")
            fd, child = stack.enter_context(root.child(name))
            root = AdmittedRoot(child, fd, directory_identity(os.fstat(fd)), private=False)
        yield root
        root.verify()


def _identity(directory: AdmittedRoot, metadata: dict[str, Any]) -> dict[str, Any]:
    if any(type(number) is not int or not 0 <= number <= _NATIVE_ID_MAX for number in directory.identity.values()):
        raise ValueError("user-output filesystem identity exceeds its native bound")
    return {
        "root": metadata["recovery"]["root_identity"], "directory": directory.identity,
        "incarnation": metadata["recovery"]["incarnation"],
    }


def _require_reference_capacity(owner: str, recovery: dict[str, Any]) -> None:
    metadata = {
        "version": producer.VERSION, "namespace": NAMESPACE,
        "lifetime": producer.USER_OUTPUT_LIFETIME, "token": "x" * 128,
        "creator": owner, "recovery": _recovery(recovery), "phase": "allocating",
        "allocation_epoch": {"generation": MAX_GENERATION, "key_hash": "f" * 64},
    }
    # Serialization stand-ins cover native IDs through 128 bits, not an allocated identity.
    identity = {
        "root": recovery["root_identity"],
        "directory": {"device": _NATIVE_ID_MAX, "inode": _NATIVE_ID_MAX},
        "incarnation": recovery["incarnation"],
    }
    try:
        producer.claim_ref(metadata)
        producer.claim_ref({**metadata, "phase": "bound", "identity": identity})
    except ValueError as exc:
        raise ValueError("user-output allocation lacks lifecycle reference capacity") from exc


def _marker(
    metadata: dict[str, Any], directory: AdmittedRoot, manifest: list[dict[str, Any]], state: str,
) -> dict[str, Any]:
    commitment = hashlib.sha256(producer.canonical_json({
        "token": metadata["token"], "creator": metadata["creator"],
        "incarnation": metadata["recovery"]["incarnation"],
        "allocation_epoch": metadata["allocation_epoch"],
        "root": metadata["recovery"]["root_identity"], "directory": directory.identity,
    }).encode("ascii")).hexdigest()
    return {
        "schema": SCHEMA, "allocation": commitment, "manifest": manifest, "state": state,
    }


def _create_file(directory: AdmittedRoot, name: str, data: bytes) -> None:
    directory.verify()
    fd = (
        open_private(directory.path / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        if os.name == "nt" else
        os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | int(getattr(os, "O_NOFOLLOW")),
                0o600, dir_fd=directory.fd)
    )
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    directory.verify()


def _write_marker(directory: AdmittedRoot, marker: dict[str, Any], *, initial: bool = False) -> None:
    raw = producer.canonical_json(marker).encode("ascii")
    if initial:
        _create_file(directory, MARKER, raw)
        return
    temporary = ".output-" + secrets.token_hex(8)
    _create_file(directory, temporary, raw)
    if os.name == "nt":
        from zdd.routing_persistence import windows_replace
        windows_replace(directory.path / temporary, directory.path / MARKER)
    else:
        os.replace(temporary, MARKER, src_dir_fd=directory.fd, dst_dir_fd=directory.fd)
        os.fsync(directory.fd)
    directory.verify()


@contextmanager
def _read_file(directory: AdmittedRoot, name: str) -> Iterator[int]:
    directory.verify()
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(directory.path / name), 0x80000000, 1, None, 3, 0x08200000, None)
        if handle in (None, ctypes.c_void_p(-1).value):
            raise _windows_error()
        try:
            open_osfhandle: Callable[[int, int], int] = getattr(msvcrt, "open_osfhandle")
            fd = open_osfhandle(handle, os.O_RDONLY | int(getattr(os, "O_BINARY")))
        except BaseException:
            kernel.CloseHandle(handle)
            raise
    else:
        fd = os.open(
            name, os.O_RDONLY | int(getattr(os, "O_NOFOLLOW")) | int(getattr(os, "O_NONBLOCK")),
            dir_fd=directory.fd,
        )
    try:
        before = os.fstat(fd)
        if scratch._is_link(before) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("user output contains a link, alias or non-regular file")
        yield fd
        after = os.fstat(fd)
        observed = (
            (directory.path / name).lstat() if os.name == "nt"
            else os.stat(name, dir_fd=directory.fd, follow_symlinks=False)
        )
        def stamp(info: os.stat_result, *, across_probes: bool = False) -> tuple[int, int, int, int, int]:
            changed = info.st_ctime_ns
            if across_probes and os.name == "nt":
                # Windows fstat/lstat can give ctime different meanings.
                changed = int(getattr(info, "st_birthtime_ns", changed))
            return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, changed
        if (
            stamp(before) != stamp(after)
            or stamp(after, across_probes=True) != stamp(observed, across_probes=True)
            or scratch._is_link(observed)
        ):
            raise ValueError("user-output file changed during verification")
        directory.verify()
    finally:
        os.close(fd)


def _read_marker(directory: AdmittedRoot, metadata: dict[str, Any]) -> dict[str, Any]:
    with _read_file(directory, MARKER) as fd:
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(producer.MAX_INPUT_BYTES + 1)
    if len(raw) > producer.MAX_INPUT_BYTES:
        raise ValueError("user-output marker exceeds its bound")
    marker = json.loads(raw)
    if (
        not isinstance(marker, dict) or not isinstance(marker.get("manifest"), list)
        or marker.get("state") not in {"allocated", "writing", "sealed"}
        or _summary(marker["manifest"]) != metadata["recovery"]["content"]
        or producer.canonical_json(
            _marker(metadata, directory, marker["manifest"], marker["state"]),
        ).encode("ascii") != raw
    ):
        raise ValueError("user-output marker does not match the reserved allocation")
    return marker


def _member_names(directory: AdmittedRoot) -> set[str]:
    found: set[str] = set()
    with os.scandir(directory.path if os.name == "nt" else directory.fd) as entries:
        for entry in entries:
            if len(found) >= MAX_FILES + 1:
                raise ValueError("user-output directory exceeds its file bound")
            found.add(entry.name)
    return found


def _verify_files(directory: AdmittedRoot, marker: dict[str, Any]) -> None:
    expected = {entry["name"]: entry for entry in marker["manifest"]}
    if _member_names(directory) != set(expected) | {MARKER}:
        raise ValueError("user output is incomplete or contains undeclared content")
    for name, entry in expected.items():
        with _read_file(directory, name) as fd:
            if os.fstat(fd).st_size != entry["bytes"]:
                raise ValueError("user-output file size differs from its manifest")
            digest = hashlib.sha256()
            remaining = entry["bytes"] + 1
            while remaining:
                data = os.read(fd, min(65536, remaining))
                if not data:
                    break
                digest.update(data)
                remaining -= len(data)
            if remaining != 1 or digest.hexdigest() != entry["sha256"]:
                raise ValueError("user-output content differs from its manifest")
    if _member_names(directory) != set(expected) | {MARKER}:
        raise ValueError("user-output membership changed during verification")


def allocate(
    path: Path, owner: str, *, selection: dict[str, Any], manifest: list[dict[str, Any]],
    operation_id: str, forbidden: tuple[Path, ...] = (), request_hash: str | None = None,
) -> dict[str, Any]:
    """Reserve and bind exclusive final storage before returning its writable path."""
    selection = _selection(selection)
    content = _summary(manifest)
    if request_hash is None:
        request_hash = hashlib.sha256(producer.canonical_json({
            "destination": selection["destination"], "content": content,
        }).encode("ascii")).hexdigest()
    if not valid_summary({"files": 1, "bytes": 0, "sha256": request_hash}):
        raise ValueError("invalid user-output request identity")
    if not producer.is_valid_token(operation_id):
        raise ValueError("user-output operation requires a stable opaque identifier")
    observed = _record(path, owner, mutable=True)
    with _operation(path, operation_id):
        record = _record(path, owner, mutable=True)
        if record.creation_nonce != observed.creation_nonce:
            raise ValueError("user-output owner incarnation changed before allocation")
        forbidden = (*forbidden, *_boundaries(record))
        if any(
            c.has_producer and isinstance(c.producer, dict)
            and c.producer.get("namespace") == NAMESPACE
            and c.producer.get("recovery", {}).get("nonce") == operation_id
            for c in record.resources
        ):
            raise ValueError("user-output operation already recorded; reconcile its existing token")
        with admit_root(Path(selection["root"]), forbidden=forbidden, private=False) as root:
            recovery = {
                "schema": SCHEMA, "root": str(root.path), "root_identity": root.identity,
                "members": selection["members"], "content": content,
                "nonce": operation_id, "incarnation": record.creation_nonce,
                "request_hash": request_hash,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        _require_reference_capacity(owner, recovery)
        reserved = _invoke(path, owner, "reserve", {
            "kind": "artifact", "namespace": NAMESPACE,
            "lifetime": producer.USER_OUTPUT_LIFETIME, "recovery": recovery,
            "expected_creation_nonce": record.creation_nonce,
        })
        permit = _invoke(path, owner, "begin", {"token": reserved["token"]})
        with _destination(recovery, forbidden, create=True) as directory:
            _write_marker(directory, _marker(permit["producer"], directory, manifest, "allocated"), initial=True)
            bound = _invoke(path, owner, "bind", {
                "token": reserved["token"], "identity": _identity(directory, permit["producer"]),
                "allocation_fence": permit["allocation_fence"],
            })
            _current_binding(path, owner, bound)
            return {**bound, "path": str(directory.path), "operation_id": operation_id}


def _claim(
    path: Path, owner: str, token: str, *, mutable: bool = False,
) -> tuple[tracking.WorktreeRecord, _ProducerClaim]:
    record = _record(path, owner, mutable=mutable)
    claim = _matched_claim(record, token)
    recovery = _recovery(claim.producer["recovery"])
    if (
        claim.producer["lifetime"] != producer.USER_OUTPUT_LIFETIME
        or claim.producer["namespace"] != NAMESPACE or claim.producer["creator"] != owner
        or recovery["incarnation"] != record.creation_nonce
    ):
        raise ValueError("user-output allocation does not belong to this exact owner")
    return record, claim


def _current_binding(path: Path, owner: str, bound: dict[str, Any]) -> None:
    _, current = _claim(path, owner, bound["token"], mutable=True)
    if current.ref != bound["ref"] or current.producer["phase"] != "bound":
        raise ValueError("user-output binding changed; no writable location returned")


def deliver(path: Path, owner: str, token: str) -> dict[str, Any]:
    """Retry the same handover; completed results never access user storage."""
    _claim(path, owner, token)
    return _invoke(path, owner, "deliver", {"token": token})


def existing_operation(path: Path, owner: str, operation_id: str) -> dict[str, Any] | None:
    """Find one original operation in its owner history without user-storage reads."""
    if not producer.is_valid_token(operation_id):
        raise ValueError("user-output operation requires a stable opaque identifier")
    record = _record(path, owner)
    matches = [
        c for c in record.resources if c.has_producer and isinstance(c.producer, dict)
        and c.producer.get("namespace") == NAMESPACE
        and isinstance(c.producer.get("recovery"), dict)
        and c.producer["recovery"].get("nonce") == operation_id
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("user-output operation identity is ambiguous")
    candidate = matches[0].producer
    assert isinstance(candidate, dict)
    _, claim = _claim(path, owner, candidate["token"])
    return producer.wire_metadata(claim.producer)


def recover(path: Path, owner: str, token: str) -> dict[str, Any]:
    """Recover only a never-started exact allocation without creating storage."""
    record, claim = _claim(path, owner, token, mutable=True)
    metadata = claim.producer
    if metadata["phase"] not in {"allocating", "bound"}:
        raise ValueError("user-output recovery requires an existing allocation")
    with _operation(path, metadata["recovery"]["nonce"]):
        with _destination(metadata["recovery"], _boundaries(record)) as directory:
            marker = _read_marker(directory, metadata)
            if marker["state"] != "allocated" or _member_names(directory) != {MARKER}:
                raise ValueError("user-output writes may have started; original responsibility retained")
            identity = _identity(directory, metadata)
    bound = _invoke(path, owner, "bind", {"token": token, "identity": identity})
    with _operation(path, metadata["recovery"]["nonce"]):
        with _destination(metadata["recovery"], _boundaries(record)) as directory:
            marker = _read_marker(directory, bound["producer"])
            if (
                marker["state"] != "allocated" or _member_names(directory) != {MARKER}
                or _identity(directory, metadata) != identity
            ):
                raise ValueError("user-output recovery changed; no writable location returned")
            _current_binding(path, owner, bound)
            return {**bound, "path": str(directory.path), "operation_id": metadata["recovery"]["nonce"]}


def status(path: Path, owner: str, ref: str) -> dict[str, Any]:
    """Verify one registered allocation with bounded real reads, not a success flag."""
    envelope = producer.decode_ref(ref)
    record, claim = _claim(path, owner, envelope["token"])
    metadata = claim.producer
    if claim.ref != ref or metadata["phase"] not in {"allocating", "bound"}:
        raise ValueError("user-output callback subject is not a current allocation")
    with _operation(path, metadata["recovery"]["nonce"]):
        with _destination(metadata["recovery"], _boundaries(record)) as directory:
            marker = _read_marker(directory, metadata)
            identity = _identity(directory, metadata)
            if "identity" in metadata and metadata["identity"] != identity:
                raise ValueError("user-output directory was replaced")
            proof: dict[str, Any] = {
                "version": 1, "subject": ref, "identity": identity,
                "allocation_finished": True, "work_settled": marker["state"] == "sealed",
            }
            if marker["state"] == "sealed":
                _verify_files(directory, marker)
                proof["delivery"] = {
                    "destination": str(directory.path), "delivered_at": datetime.now(timezone.utc).isoformat(),
                    "content": metadata["recovery"]["content"], "result": "verified-local",
                }
            return {"available": True, "exists": True, "producer_verification": proof}


def callback_status(ref: str) -> dict[str, Any]:
    """Resolve the owning ledger through its existing authority, without discovery."""
    from . import config as cfg
    from .execution_spaces import resolve_owner_record_path

    try:
        envelope = producer.decode_ref(ref)
        owner = envelope["creator"]
        parsed = parse_claim_ref(owner)
        if parsed is None or parsed.project is None:
            raise ValueError("user-output owner is not qualified")
        config = cfg.load_project_config(parsed.project)
        path, _, error = resolve_owner_record_path(owner, config)
        if error or path is None:
            raise ValueError("user-output owning record is unavailable")
        return status(path, owner, ref)
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
        return {"available": False, "reason": str(error)}


def produce_files(
    path: Path, owner: str, *, selection: dict[str, Any], files: dict[str, bytes],
    operation_id: str, request_hash: str | None = None,
) -> dict[str, Any]:
    """Produce bounded files directly in their final compartment, then hand over."""
    if not isinstance(files, dict) or not all(isinstance(value, bytes) for value in files.values()):
        raise ValueError("user-output producer requires named byte content")
    manifest = [
        {"name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in sorted(files.items())
    ]
    content = _summary(manifest)
    selection = _selection(selection)
    if not producer.is_valid_token(operation_id):
        raise ValueError("user-output operation requires a stable opaque identifier")
    existing = existing_operation(path, owner, operation_id)
    if existing is not None:
        record, claim = _claim(path, owner, existing["token"])
        metadata = claim.producer
        if any(metadata["recovery"][key] != expected for key, expected in (
            ("root", selection["root"]), ("members", selection["members"]), ("content", content),
        )) or request_hash is not None and request_hash != metadata["recovery"]["request_hash"]:
            raise ValueError("user-output retry changed the original request")
        if metadata["phase"] == "delivered":
            return deliver(path, owner, metadata["token"])
        if metadata["phase"] == "bound":
            with _operation(path, operation_id):
                with _destination(metadata["recovery"], _boundaries(record)) as directory:
                    state = _read_marker(directory, metadata)["state"]
            if state == "sealed":
                return deliver(path, owner, metadata["token"])
        allocated = recover(path, owner, metadata["token"])
    else:
        allocated = allocate(
            path, owner, selection=selection, manifest=manifest, operation_id=operation_id, request_hash=request_hash,
        )
    record, claim = _claim(path, owner, allocated["token"], mutable=True)
    metadata = claim.producer
    with _operation(path, operation_id):
        with _destination(metadata["recovery"], _boundaries(record)) as directory:
            marker = _read_marker(directory, metadata)
            if (
                marker["state"] != "allocated" or _member_names(directory) != {MARKER}
                or _identity(directory, metadata) != metadata["identity"]
            ):
                raise ValueError("user-output producer cannot resume unknown writes")
            _write_marker(directory, {**marker, "state": "writing"})
            for name, data in files.items():
                _create_file(directory, name, data)
            _verify_files(directory, marker)
            _write_marker(directory, {**marker, "state": "sealed"})
    return deliver(path, owner, allocated["token"])
