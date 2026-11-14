"""Native, opt-in disposable scratch provider over the existing producer ledger."""

from __future__ import annotations

from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import time
from typing import Iterator

from agent_procutil import scratch
from agent_procutil._daemon_instance_owner import open_private
from single_instance_lease.operation import OperationLock, OperationLockedError

from . import resource_producer as producer, tracking, tracking_write
from .artifact_root import AdmittedRoot, admit_root, directory_identity
from .artifact_storage import DEFAULT_DIAGNOSTIC_BYTES, DEFAULT_DIAGNOSTIC_FILES, validate_values
from .tracking_producer_write import _matched_claim, _owned_record

NAMESPACE = "worktree-scratch"
SCHEMA = "agent-worktrees.scratch-allocation/v1"
_MARKER = scratch.BREADCRUMB
_DIAGNOSTIC = "runner-diagnostic.txt"
_RETENTION = {"class": "disposable-scratch", "cleanup": "explicit-owner-pass"}
_LOCK_WAIT_SECONDS = 10.0


def capabilities() -> dict:
    return {
        "schema": SCHEMA, "allocator": 1, "artifact_retention": 1,
        "cleanup": "explicit-owner-pass", "live_rollout": False,
    }


def _arguments(path: Path, owner: str) -> dict:
    return {
        "schema": producer.SCHEMA, "version": producer.VERSION,
        "yaml_path": str(path), "owner_ref": owner,
    }


def _record(path: Path, owner: str, *, mutable: bool = False):
    with tracking._RecordLock(path, require_sidecar=True):
        record, _ = _owned_record(_arguments(path, owner), path, mutable=mutable)
        if record.kind in tracking.MANAGED_KINDS:
            raise ValueError("native scratch requires a session owner with a retained ledger")
        if not record.creation_nonce:
            raise ValueError("scratch allocation requires a worktree incarnation")
        return record


def _boundaries(record) -> tuple[Path, ...]:
    from . import config as cfg, sessions
    from .execution_spaces import project_record_config

    config = project_record_config(record)
    roots = [
        Path(record.worktree_path), Path(cfg.project_dir(record.repo)), sessions._session_state_dir(),
    ]
    for repo in config.repos.values():
        roots.extend(Path(value) for value in (repo.anchor, repo.worktree_root) if value)
    if "COPILOT_HOME" in os.environ:
        roots.append(Path(os.environ["COPILOT_HOME"]))
    metadata = [entry for root in roots for entry in protected_git_paths(root)]
    roots.extend(metadata)
    return tuple(roots)


def protected_git_paths(checkout: Path) -> tuple[Path, ...]:
    """Resolve only Git's exact pointer files at an owner-selected checkout."""
    marker = checkout / ".git"
    if marker.is_dir():
        return (marker.resolve(),)
    if not marker.exists():
        return ()
    def pointer(path: Path, prefix: str = "") -> Path:
        with path.open("rb") as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError("Git metadata pointer exceeds its bound")
        text = raw.decode("utf-8").strip()
        if not text.startswith(prefix) or not text[len(prefix):].strip():
            raise ValueError("invalid Git metadata pointer")
        selected = Path(text[len(prefix):].strip())
        return (path.parent / selected).resolve() if not selected.is_absolute() else selected.resolve()
    directory = pointer(marker, "gitdir:")
    common = directory / "commondir"
    return (directory, pointer(common)) if common.exists() else (directory,)


def _invoke(path: Path, owner: str, operation: str, payload: dict) -> dict:
    result = tracking_write.run_direct("resource_producer", {
        **_arguments(path, owner), "operation": operation, "input": payload,
    }, reason="native scratch producer uses the existing direct RMW authority")
    if not result.get("ok"):
        raise ValueError(f"scratch {operation} refused; inspect existing responsibility")
    return result


@contextmanager
def _root(recovery: dict, forbidden: tuple[Path, ...]) -> Iterator[AdmittedRoot]:
    if (
        set(recovery) != {
            "schema", "root", "root_identity", "nonce", "incarnation", "failure_evidence", "created_at",
        }
        or recovery.get("schema") != SCHEMA
        or not producer.is_valid_token(recovery.get("nonce"))
    ):
        raise ValueError("invalid native scratch recovery metadata")
    if not isinstance(recovery["created_at"], str) or datetime.fromisoformat(recovery["created_at"]).tzinfo is None:
        raise ValueError("scratch creation time must be timezone qualified")
    validate_values({"failure_evidence": recovery["failure_evidence"]}, machine_paths=False)
    with admit_root(
        Path(recovery["root"]), forbidden=forbidden, expected=recovery["root_identity"],
    ) as root:
        # One fixed control file, not a second per-allocation inventory. Its
        # kernel lock serializes physical operations, never the running work.
        lock = OperationLock(root.path / ".artifact-root.lock")
        deadline = time.monotonic() + _LOCK_WAIT_SECONDS
        while True:
            try:
                lock.acquire()
                break
            except OperationLockedError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(0.05, remaining))
        try:
            root.verify()
            yield root
        finally:
            lock.release()


def _name(recovery: dict) -> str:
    return "a-" + hashlib.sha256(recovery["nonce"].encode("ascii")).hexdigest()[:12]


def _write_marker(fd: int, path: Path, record: dict, *, initial: bool = False) -> None:
    raw = producer.canonical_json(record).encode("ascii")
    temporary = ".marker-" + secrets.token_hex(8)
    if os.name == "nt":
        out = open_private(path / temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    else:
        out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(out, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if initial:
            # Exclusive publication also refuses an unexplained legacy marker.
            if os.name == "nt":
                os.rename(path / temporary, path / _MARKER)
            else:
                os.link(temporary, _MARKER, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                os.unlink(temporary, dir_fd=fd)
        elif os.name == "nt":
            from zdd.routing_persistence import windows_replace
            windows_replace(path / temporary, path / _MARKER)
        else:
            os.replace(temporary, _MARKER, src_dir_fd=fd, dst_dir_fd=fd)
        if os.name != "nt":
            os.fsync(fd)
    finally:
        if os.name == "nt":
            (path / temporary).unlink(missing_ok=True)
        else:
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass


def _read_marker(fd: int, path: Path, metadata: dict) -> dict:
    raw = scratch._saved_breadcrumb(fd, str(path))
    if raw is None or len(raw) > producer.MAX_INPUT_BYTES:
        raise ValueError("scratch allocation marker is missing or unreadable")
    value = json.loads(raw)
    if not isinstance(value, dict) or producer.canonical_json(value).encode("ascii") != raw:
        raise ValueError("scratch allocation marker is not canonical")
    expected = {
        "schema": SCHEMA, "creator": metadata["creator"], "token": metadata["token"],
        "recovery": metadata["recovery"], "allocation_epoch": metadata["allocation_epoch"],
        "directory": directory_identity(os.fstat(fd)),
        "purpose": "contained test-runner sandbox",
        "retention": "disposable; retained evidence requires an explicit owner cleanup pass",
    }
    if (
        set(value) != set(expected) | {"state", "diagnostics"}
        or value["state"] not in {"allocated", "active", "settled", "reclaiming"}
        or producer.canonical_json({
            **expected, "state": value["state"], "diagnostics": value["diagnostics"],
        }).encode("ascii") != raw
    ):
        raise ValueError("scratch allocation marker identity does not match")
    if value["state"] in {"settled", "reclaiming"}:
        _verify_evidence(fd, path, value)
    elif value["diagnostics"] is not None:
        raise ValueError("unfinished scratch cannot assert bounded evidence")
    return value


def _verify_evidence(fd: int, path: Path, marker: dict) -> None:
    evidence = marker["diagnostics"]
    if (
        not isinstance(evidence, dict) or set(evidence) != {"files", "bytes", "truncated"}
        or type(evidence["files"]) is not int or evidence["files"] not in (0, 1)
        or type(evidence["bytes"]) is not int or evidence["bytes"] < 0
        or type(evidence["truncated"]) is not bool
        or evidence["bytes"] > marker["recovery"]["failure_evidence"]["max_bytes"]
    ):
        raise ValueError("invalid scratch diagnostic receipt")
    observed = {"files": 0, "bytes": 0}
    with os.scandir(path if os.name == "nt" else fd) as entries:
        for entry in entries:
            if entry.name == _MARKER:
                continue
            info = (path / entry.name).lstat() if os.name == "nt" else entry.stat(follow_symlinks=False)
            if (
                entry.name != _DIAGNOSTIC or scratch._is_link(info)
                or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            ):
                raise ValueError("retained scratch contains undeclared output")
            observed["files"] += 1
            observed["bytes"] += info.st_size
    removed_diagnostic = marker["state"] == "reclaiming" and observed == {"files": 0, "bytes": 0}
    if not removed_diagnostic and observed != {key: evidence[key] for key in ("files", "bytes")}:
        raise ValueError("scratch diagnostic contents changed")


def _identity(root: AdmittedRoot, fd: int, metadata: dict) -> dict:
    return {
        "root": root.identity, "directory": directory_identity(os.fstat(fd)),
        "nonce": metadata["recovery"]["nonce"], "incarnation": metadata["recovery"]["incarnation"],
    }


def _claim(path: Path, owner: str, token: str):
    record = _record(path, owner)
    claim = _matched_claim(record, token)
    metadata = claim.producer
    if (
        metadata["namespace"] != NAMESPACE or metadata["lifetime"] != producer.ARTIFACT_LIFETIME
        or metadata["creator"] != owner
        or metadata["recovery"].get("incarnation") != record.creation_nonce
    ):
        raise ValueError("native scratch owner or incarnation changed")
    return record, claim


def allocate(
    path: Path, owner: str, *, root_path: Path, forbidden: tuple[Path, ...],
    operation_id: str, failure_evidence: dict | None = None,
) -> dict:
    """Reserve/begin/create/bind before returning any writable location.

    The operation ID is chosen before this call and kept by its caller. An
    unknown result is inspected by that same ID, never retried as new work.
    """
    if not producer.is_valid_token(operation_id):
        raise ValueError("scratch operation ID must be an opaque producer token")
    record = _record(path, owner, mutable=True)
    forbidden = (*forbidden, *_boundaries(record))
    limits = failure_evidence if failure_evidence is not None else {
        "max_files": DEFAULT_DIAGNOSTIC_FILES, "max_bytes": DEFAULT_DIAGNOSTIC_BYTES,
    }
    limits = validate_values({"failure_evidence": limits}, machine_paths=False)["failure_evidence"]
    if set(limits) != {"max_files", "max_bytes"}:
        raise ValueError("scratch allocation needs fully resolved evidence limits")
    with admit_root(root_path, forbidden=forbidden, create=True) as admitted:
        recovery = {
            "schema": SCHEMA, "root": str(admitted.path), "root_identity": admitted.identity,
            "nonce": operation_id, "incarnation": record.creation_nonce,
            "failure_evidence": limits,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    with _root(recovery, forbidden) as root:
        record = _record(path, owner, mutable=True)
        if record.creation_nonce != recovery["incarnation"]:
            raise ValueError("scratch worktree incarnation changed before reservation")
        if any(
            c.has_producer and isinstance(c.producer, dict)
            and c.producer.get("namespace") == NAMESPACE
            and isinstance(c.producer.get("recovery"), dict)
            and c.producer["recovery"].get("nonce") == operation_id
            for c in record.resources
        ):
            raise ValueError("scratch operation already recorded; reconcile its existing token")
        reserved = _invoke(path, owner, "reserve", {
            "kind": "artifact", "namespace": NAMESPACE,
            "lifetime": producer.ARTIFACT_LIFETIME, "recovery": recovery,
        })
        permit = _invoke(path, owner, "begin", {"token": reserved["token"]})
        metadata = permit["producer"]
        root.create_child(_name(recovery))
        with root.child(_name(recovery)) as (fd, child):
            marker = {
                "schema": SCHEMA, "creator": owner, "token": reserved["token"],
                "recovery": recovery, "allocation_epoch": metadata["allocation_epoch"],
                "directory": directory_identity(os.fstat(fd)), "state": "allocated",
                "diagnostics": None,
                "purpose": "contained test-runner sandbox",
                "retention": "disposable; retained evidence requires an explicit owner cleanup pass",
            }
            _write_marker(fd, child, marker, initial=True)
            identity = _identity(root, fd, metadata)
            bound = _invoke(path, owner, "bind", {
                "token": reserved["token"], "identity": identity,
                "allocation_fence": permit["allocation_fence"],
            })
            # Once active, a crash/lost reply is deliberately not inferred
            # quiescent merely because this allocator's kernel lock released.
            _write_marker(fd, child, {**marker, "state": "active"})
            return {**bound, "path": str(child), "operation_id": operation_id}


def settle_after_cleanup(
    path: Path, owner: str, token: str, *, forbidden: tuple[Path, ...],
    diagnostic: bytes | None = None,
) -> dict:
    """Called only by an integrated process owner after verified descendant cleanup.

    Not exposed as a caller-supplied boolean/proof CLI. Unknown cleanup must
    never call this method; it leaves the marker active and custody unsettled.
    """
    _, claim = _claim(path, owner, token)
    forbidden = (*forbidden, *_boundaries(_record(path, owner)))
    metadata = claim.producer
    if metadata["phase"] not in {"bound", "retained"}:
        raise ValueError("scratch settlement requires a bound allocation")
    if diagnostic is not None and not isinstance(diagnostic, bytes):
        raise ValueError("scratch diagnostic must be producer-owned bytes")
    with _root(metadata["recovery"], forbidden) as root:
        with root.child(_name(metadata["recovery"])) as (fd, child):
            marker = _read_marker(fd, child, metadata)
            if producer.canonical_json(_identity(root, fd, metadata)) != producer.canonical_json(metadata["identity"]):
                raise ValueError("scratch allocation was replaced")
            if marker["state"] == "settled":
                if diagnostic is not None:
                    raise ValueError("settled scratch diagnostics are immutable")
            else:
                if not scratch._clear(fd, str(child), keep=_MARKER):
                    raise ValueError("scratch payload cleanup incomplete; active responsibility retained")
                limit = metadata["recovery"]["failure_evidence"]["max_bytes"]
                data = diagnostic[:limit] if diagnostic is not None else None
                if data is not None:
                    if os.name == "nt":
                        out = open_private(child / _DIAGNOSTIC, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                    else:
                        out = os.open(_DIAGNOSTIC, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                    with os.fdopen(out, "wb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                evidence = {
                    "files": int(data is not None), "bytes": len(data or b""),
                    "truncated": diagnostic is not None and len(diagnostic) > limit,
                }
                _write_marker(fd, child, {**marker, "state": "settled", "diagnostics": evidence})
    return _invoke(path, owner, "retain", {"token": token})


def status(path: Path, owner: str, ref: str, *, forbidden: tuple[Path, ...]) -> dict:
    """Provider proof from exact ledger + physical identity, never from a path alone."""
    envelope = producer.decode_ref(ref)
    record, claim = _claim(path, owner, envelope["token"])
    forbidden = (*forbidden, *_boundaries(record))
    if claim.ref != ref:
        raise ValueError("scratch callback subject changed")
    metadata = claim.producer
    proof = {"version": 1, "subject": ref}
    with _root(metadata["recovery"], forbidden) as root:
        try:
            with root.child(_name(metadata["recovery"])) as (fd, child):
                marker = _read_marker(fd, child, metadata)
                identity = _identity(root, fd, metadata)
                if "identity" in metadata and producer.canonical_json(identity) != producer.canonical_json(metadata["identity"]):
                    raise ValueError("scratch allocation identity changed")
                proof.update(
                    identity=identity, allocation_finished=True,
                    work_settled=marker["state"] in {"settled", "reclaiming"},
                    retention=copy.deepcopy(_RETENTION),
                )
                return {"available": True, "exists": True, "producer_verification": proof}
        except FileNotFoundError:
            if os.path.lexists(root.path / _name(metadata["recovery"])):
                raise ValueError("scratch inspection changed; directory absence is not established")
            # A bound active allocation's missing pathname is not process
            # settlement. Only its prior retained receipt proves that boundary.
            settled = producer.verified_retention(
                metadata.get("closeout"), ref, metadata.get("identity", {}),
            ) or (
                metadata["phase"] == "closed"
                and producer.verified_absence(metadata.get("closeout"), ref)
            )
            if "identity" in metadata and not settled:
                raise ValueError("scratch disappeared without a process-settlement receipt")
            proof.update(absent=True, allocation_finished=True)
            return {"available": True, "exists": False, "producer_verification": proof}


def callback_status(ref: str) -> dict:
    """Native registered callback; resolve authority rather than accepting a ledger path."""
    from . import config as cfg
    from .execution_spaces import resolve_owner_record_path

    try:
        envelope = producer.decode_ref(ref)
        owner = envelope["creator"]
        parsed = tracking.parse_claim_ref(owner)
        config = cfg.load_project_config(parsed.project)
        path, _, error = resolve_owner_record_path(owner, config)
        if error or path is None:
            raise ValueError("scratch owning record is unavailable")
        return status(path, owner, ref, forbidden=())
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
        return {"available": False, "reason": str(error)}


def recover(path: Path, owner: str, token: str, *, forbidden: tuple[Path, ...]) -> dict:
    """Recover only a never-started exact allocation, without creating anything."""
    record, claim = _claim(path, owner, token)
    forbidden = (*forbidden, *_boundaries(record))
    metadata = claim.producer
    if metadata["phase"] not in {"allocating", "bound"}:
        raise ValueError("scratch recovery requires an existing allocation attempt")
    with _root(metadata["recovery"], forbidden) as root:
        with root.child(_name(metadata["recovery"])) as (fd, child):
            marker = _read_marker(fd, child, metadata)
            if marker["state"] != "allocated":
                raise ValueError("scratch work may have started; process-owner reconciliation required")
            identity = _identity(root, fd, metadata)
    bound = _invoke(path, owner, "bind", {"token": token, "identity": identity})
    with _root(metadata["recovery"], forbidden) as root:
        with root.child(_name(metadata["recovery"])) as (fd, child):
            marker = _read_marker(fd, child, bound["producer"])
            if marker["state"] != "allocated" or _identity(root, fd, metadata) != identity:
                raise ValueError("scratch recovery changed; no writable location returned")
            _write_marker(fd, child, {**marker, "state": "active"})
            return {**bound, "path": str(child), "operation_id": metadata["recovery"]["nonce"]}


def preview_cleanup(path: Path, owner: str, token: str, *, forbidden: tuple[Path, ...]) -> dict:
    record, claim = _claim(path, owner, token)
    if record.status != "finalized" or claim.producer["phase"] not in {"retained", "closed"}:
        raise ValueError("independent cleanup requires a finalized owner and retained allocation")
    if tracking.claim_handoff_reservation(record, claim):
        raise ValueError("scratch allocation has an outstanding handoff")
    proof = status(path, owner, claim.ref, forbidden=forbidden)
    if not (
        producer.verified_retention(proof, claim.ref, claim.producer["identity"])
        or producer.verified_absence(proof, claim.ref)
    ):
        raise ValueError("scratch cleanup lacks current exact settlement proof")
    ticket = hashlib.sha256(producer.canonical_json({
        "owner": owner, "incarnation": record.creation_nonce, "ref": claim.ref,
        "proof": proof,
    }).encode("ascii")).hexdigest()
    return {"schema": SCHEMA, "token": token, "preview": ticket, "identity": claim.producer["identity"]}


def _remove_settled(path: Path, owner: str, token: str, *, forbidden: tuple[Path, ...]) -> dict:
    record, claim = _claim(path, owner, token)
    if claim.producer["phase"] not in {"retained", "closed"} or tracking.claim_handoff_reservation(record, claim):
        raise ValueError("scratch removal requires settled retained custody without a handoff")
    forbidden = (*forbidden, *_boundaries(record))
    metadata = claim.producer
    if metadata["phase"] == "closed":
        proof = status(path, owner, claim.ref, forbidden=forbidden)
        if not producer.verified_absence(proof, claim.ref):
            raise ValueError("closed scratch allocation has unexpected physical contents")
        return {**_invoke(path, owner, "close", {"token": token}), "outcome": "removed"}
    with _root(metadata["recovery"], forbidden) as root:
        # Freeze the directory through inspection; close its Windows no-delete
        # pin only for removal while retaining the private parent and op lock.
        try:
            with root.child(_name(metadata["recovery"])) as (fd, child):
                marker = _read_marker(fd, child, metadata)
                if marker["state"] not in {"settled", "reclaiming"} or _identity(root, fd, metadata) != metadata["identity"]:
                    raise ValueError("scratch process settlement or physical identity changed")
                if marker["state"] == "settled":
                    _write_marker(fd, child, {**marker, "state": "reclaiming"})
            root.verify()
            if directory_identity(child.lstat()) != metadata["identity"]["directory"]:
                raise ValueError("scratch directory changed at removal boundary")
            expected = metadata["identity"]["directory"]
            removed = scratch.remove_scratch(
                child, expected_directory_identity=(expected["device"], expected["inode"]),
            )
        except FileNotFoundError:
            if os.path.lexists(root.path / _name(metadata["recovery"])):
                raise ValueError("scratch removal changed; directory absence is not established")
            if not producer.verified_retention(metadata.get("closeout"), claim.ref, metadata["identity"]):
                raise ValueError("missing scratch lacks prior settlement proof")
            removed = True
    if removed:
        closed = _invoke(path, owner, "close", {"token": token})
        return {**closed, "outcome": "removed"}
    return {"schema": SCHEMA, "token": token, "outcome": "partial", "phase": "retained"}


def cleanup(
    path: Path, owner: str, token: str, *, preview: str, forbidden: tuple[Path, ...],
) -> dict:
    """Separate preview/revalidation pass; no scan, age heuristic or cache eviction."""
    import hmac

    current = preview_cleanup(path, owner, token, forbidden=forbidden)
    if not isinstance(preview, str) or not hmac.compare_digest(preview, current["preview"]):
        raise ValueError("scratch cleanup preview changed")
    return _remove_settled(path, owner, token, forbidden=forbidden)


def dispose_consumed(path: Path, owner: str, token: str, *, forbidden: tuple[Path, ...]) -> dict:
    """Integrated producer removes its just-consumed output after verified cleanup."""
    return _remove_settled(path, owner, token, forbidden=forbidden)
