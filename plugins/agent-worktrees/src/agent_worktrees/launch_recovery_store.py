"""Actor-free recovery journal in the original worktree owner's launch state."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile

from agent_remote_driver.headed.recovery import RecoveryDescriptor, RecoveryRecord
from agent_remote_driver.headed.recovery_codec import (
    MAX_RECORD_BYTES,
    decode_recovery_record,
    encode_recovery_record,
)

from . import checkout_durability, config as cfg, locks, status_monitor_runtime
from . import tracking, tracking_write
from .execution_spaces import require_project_record_mutation
from .worktree_status_daemon import _is_safe_identity_token

_VERB = "launch_recovery_compare_and_swap"
_SCHEMA = "worktree-launch-recovery-store"
_MAX_RECORDS = 64
_MAX_BYTES = 2 * 1024 * 1024


def _safe_path(path: Path) -> None:
    for item in (path, *path.parents):
        try:
            metadata = item.lstat()
        except FileNotFoundError:
            continue
        if (
            stat.S_ISLNK(metadata.st_mode)
            or getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("Indirect recovery state is unsupported")
    if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        raise ValueError("Recovery state must be a regular, unshared file")


def _binding(path: Path):
    if (
        not path.is_absolute() or path.suffix != ".yaml"
        or not _is_safe_identity_token(path.stem)
    ):
        raise ValueError("An exact canonical worktree record is required")
    _safe_path(path)
    record = tracking.load_record(path)
    canonical = cfg.project_dir(record.repo) / "worktrees" / f"{record.worktree_id}.yaml"
    if (
        record.worktree_id != path.stem or canonical != path
        or type(record.creation_nonce) is not str or not 1 <= len(record.creation_nonce) <= 512
    ):
        raise ValueError("Recovery record is not bound to a canonical worktree incarnation")
    owner = tracking.format_claim_ref(record.execution_machine, record.repo, record.worktree_id)
    return record, owner


def _descriptor(value: RecoveryDescriptor, owner: str, worktree_id: str) -> None:
    if (
        type(value) is not RecoveryDescriptor
        or value.owner_ref != owner or value.worktree_ref != worktree_id
    ):
        raise ValueError("Recovery descriptor does not belong to this worktree owner")


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate recovery store field")
        value[key] = item
    return value


def _read(path: Path, nonce: str, owner: str, worktree_id: str) -> list[RecoveryRecord]:
    _safe_path(path)
    try:
        with path.open("rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("Recovery state must be a regular file")
            raw = stream.read(_MAX_BYTES + 1)
    except FileNotFoundError:
        return []
    if len(raw) > _MAX_BYTES:
        raise ValueError("Recovery store exceeds its budget")
    value = json.loads(raw, object_pairs_hook=_unique)
    if (
        type(value) is not dict
        or set(value) != {"schema", "version", "creation_nonce", "records"}
        or value["schema"] != _SCHEMA or type(value["version"]) is not int
        or value["version"] != 1 or value["creation_nonce"] != nonce
        or type(value["records"]) is not list
        or not 1 <= len(value["records"]) <= _MAX_RECORDS
    ):
        raise ValueError("Invalid recovery store binding or format")
    records = []
    for item in value["records"]:
        if type(item) is not str:
            raise ValueError("A complete encoded recovery record is required")
        record = decode_recovery_record(item.encode("utf-8"))
        _descriptor(record.descriptor, owner, worktree_id)
        if any(
            old.operation_id == record.operation_id or old.descriptor == record.descriptor
            for old in records
        ):
            raise ValueError("Repeated recovery operation or descriptor")
        records.append(record)
    return records


def _write(path: Path, nonce: str, records: list[RecoveryRecord]) -> None:
    if len(records) > _MAX_RECORDS:
        raise ValueError("Recovery store is full; retained operations cannot be discarded")
    raw = json.dumps({
        "schema": _SCHEMA, "version": 1, "creation_nonce": nonce,
        "records": [encode_recovery_record(record).decode("utf-8") for record in records],
    }, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > _MAX_BYTES:
        raise ValueError("Recovery store exceeds its budget")
    _safe_path(path)
    path.parent.mkdir(exist_ok=True)
    checkout_durability.directory(path.parent.parent)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        checkout_durability.replace(Path(temporary), path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _apply(args: dict) -> dict:
    if (
        type(args) is not dict
        or set(args) != {"yaml_path", "creation_nonce", "expected_revision", "record"}
        or type(args["yaml_path"]) is not str or type(args["record"]) is not str
        or len(args["record"]) > MAX_RECORD_BYTES
        or type(args["expected_revision"]) is not int or args["expected_revision"] < 0
    ):
        raise ValueError("Invalid recovery compare-and-swap request")
    proposed = decode_recovery_record(args["record"].encode("utf-8"))
    path = Path(args["yaml_path"])
    _binding(path)
    _safe_path(path.with_suffix(".lock"))
    with tracking._RecordLock(path, require_sidecar=True):
        record, owner = _binding(path)
        require_project_record_mutation(record)
        if record.creation_nonce != args["creation_nonce"]:
            raise ValueError("Recovery worktree incarnation changed")
        _descriptor(proposed.descriptor, owner, record.worktree_id)
        target = path.parent / path.stem / "launch-recovery.json"
        records = _read(target, record.creation_nonce, owner, record.worktree_id)
        current = next((r for r in records if r.descriptor == proposed.descriptor), None)
        revision = current.revision if current is not None else 0
        if (
            revision != args["expected_revision"] or proposed.revision != revision + 1
            or any(
                old.operation_id == proposed.operation_id and old.descriptor != proposed.descriptor
                for old in records
            )
        ):
            return {"committed": False}
        if current is not None:
            if (
                proposed.operation_id != current.operation_id
                or proposed.steps[:-1] != current.steps
                or proposed.legacy_holds[:len(current.legacy_holds)] != current.legacy_holds
            ):
                return {"committed": False}
            records[records.index(current)] = proposed
        else:
            records.append(proposed)
        _write(target, record.creation_nonce, records)
    return {"committed": True}


class WorktreeRecoveryStore:
    """Bookkeeping only; construction and reads never create state.

    The owner reference is the canonical worktree claim ref; worktree_ref is
    its worktree ID. Other descriptor fields remain exact journal keys, not
    current lease, native-scope or effect authority. No deletion/reset API.
    """

    def __init__(self, record_path: Path):
        record, owner = _binding(record_path)
        self._record_path = record_path
        self._nonce = record.creation_nonce
        self._owner = owner

    def load(self, descriptor: RecoveryDescriptor) -> RecoveryRecord | None:
        record, owner = _binding(self._record_path)
        if record.creation_nonce != self._nonce or owner != self._owner:
            raise ValueError("Recovery worktree owner or incarnation changed")
        _descriptor(descriptor, owner, record.worktree_id)
        records = _read(
            self._record_path.parent / self._record_path.stem / "launch-recovery.json",
            self._nonce, owner, record.worktree_id,
        )
        return next((record for record in records if record.descriptor == descriptor), None)

    def compare_and_swap(
        self, descriptor: RecoveryDescriptor, expected_revision: int, record: RecoveryRecord,
    ) -> bool:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("An exact store revision is required")
        _descriptor(descriptor, self._owner, self._record_path.stem)
        payload = encode_recovery_record(record)
        if record.descriptor != descriptor:
            raise ValueError("Recovery record and descriptor differ")
        result = tracking_write.dispatch(
            _VERB, {
                "yaml_path": str(self._record_path), "creation_nonce": self._nonce,
                "expected_revision": expected_revision, "record": payload.decode("utf-8"),
            },
            read_lock_data=lambda: locks.read_lock(status_monitor_runtime._monitor_lock_path()),
            ensure_monitor=None, boot_wait_s=0.0, min_version=1,
        )
        if type(result) is not dict or set(result) != {"committed"} or type(result["committed"]) is not bool:
            raise ValueError("Recovery commit acknowledgement is unconfirmed; reread the operation")
        return result["committed"]


tracking_write.register_verb(_VERB, _apply, version=1)
