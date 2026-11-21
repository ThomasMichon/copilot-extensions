"""Internal Windows no-delete-sharing sealing; no registry import or activation."""

from __future__ import annotations

import json
import os
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from single_instance_lease import SingleInstance

from .watch_private import (
    _windows_private, confirm_private, require_private, windows_move_no_replace, write_private,
)
from .watch_registry import WatchRegistry
from .watch_sealing import _record_identity, retain_original_lease
from .watch_storage import (
    _MAX_METADATA_BYTES, _read_subscriptions_state, _regular_bytes, _reject_constant,
    _unique_object, watch_state_paths,
)
from .watch_transition import _pin_identity, _validate_binding, _validate_record, read_preparation

Document = dict[str, Any]
_BACKEND = "windows-move-no-replace"
_IDENTITY_MAX = (1 << 128) - 1


def _identity(path: Path, *, directory: bool = False, installed: bool = False) -> tuple[int, int]:
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise PermissionError("watch Windows sealing requires plain native paths")
    if not all(0 <= value <= _IDENTITY_MAX for value in (info.st_dev, info.st_ino)):
        raise PermissionError("watch Windows sealing requires supported native file identities")
    if directory:
        _windows_private(path, protect=False)
        entries = list(path.iterdir())
        if entries and (
            not installed or entries != [path / "writer.lock"]
        ):
            raise PermissionError("watch Windows barrier has unexpected entries")
        for entry in entries:
            _identity(entry)
    return info.st_dev, info.st_ino


def read_sealing(root: Path, binding: Document) -> tuple[Document, str]:
    """Inspect actual topology without opening a retained or raced historical file."""
    if sys.platform != "win32":
        raise NotImplementedError("watch Windows sealing requires Windows")
    if not root.is_absolute() or root.resolve(strict=True) != root:
        raise PermissionError("watch sealing requires its canonical state root")
    _validate_binding(root, binding)
    paths = watch_state_paths(root)
    require_private(paths.journal)
    raw = _regular_bytes(paths.journal)
    if raw is None:
        raise PermissionError("watch sealing requires a prepared record")
    document = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    if not isinstance(document, dict):
        raise PermissionError("watch sealing record is invalid")
    if "seal" not in document:
        prior = read_preparation(root, binding)
        if prior is None:
            raise PermissionError("watch sealing requires completed preparation")
        return prior, "original"
    return document, _inspect_sealing(root, binding, document)


def _inspect_sealing(root: Path, binding: Document, document: Document) -> str:
    """Validate original v1 evidence against topology, without adapting its record."""
    if sys.platform != "win32":
        raise NotImplementedError("watch Windows sealing requires Windows")
    if not root.is_absolute() or root.resolve(strict=True) != root:
        raise PermissionError("watch sealing requires its canonical state root")
    _validate_binding(root, binding)
    paths = watch_state_paths(root)
    phase = document.get("phase")
    if phase not in ("prepared", "sealed-legacy"):
        raise PermissionError("watch sealing cannot downgrade a later transition")
    _validate_record(
        {key: value for key, value in document.items() if key != "seal"}, binding, phase=phase,
    )
    seal = document.get("seal")
    if (
        not isinstance(seal, dict) or set(seal) != {"backend", "barrier", "boot"}
        or seal["backend"] != _BACKEND or seal["boot"] is not None
    ):
        raise PermissionError("watch Windows sealing evidence is invalid")
    original, barrier = _record_identity(document["lease"]), _record_identity(seal["barrier"])
    if original == barrier or _pin_identity(root) != original:
        raise PermissionError("watch sealing lost its original inode pin")
    if os.path.lexists(paths.isolated_subscriptions.parent):
        raise PermissionError("watch sealing has conflicting isolated state")
    if os.path.lexists(paths.staged_barrier):
        if _identity(paths.staged_barrier, directory=True) != barrier or phase != "prepared":
            raise PermissionError("watch sealing staged barrier differs from its record")
        if os.path.lexists(paths.retained_lock):
            if _identity(paths.retained_lock) != original or os.path.lexists(paths.historical_lock):
                raise PermissionError("watch sealing is blocked by a raced historical path")
            return "moved"
        if _identity(paths.historical_lock) != original:
            raise PermissionError("watch sealing no longer names the original lease")
        return "original"
    if (
        _identity(paths.retained_lock) != original
        or _identity(paths.historical_lock, directory=True, installed=True) != barrier
    ):
        raise PermissionError("watch sealing installed barrier differs from its record")
    return "sealed"


def _acquire_barrier_lease(root: Path, document: Document, lease: SingleInstance) -> None:
    paths = watch_state_paths(root)
    if _identity(paths.historical_lock, directory=True, installed=True) != _record_identity(
        document["seal"]["barrier"],
    ):
        raise PermissionError("watch sealing cannot acquire before its qualified barrier")
    if lease.held:
        if lease.lock_path != paths.writer_lock:
            raise PermissionError("watch sealing retains an unexpected original lease")
    else:
        try:
            descriptor = os.open(paths.writer_lock, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        identity = _identity(paths.writer_lock)
        lease.lock_path = paths.writer_lock
        lease.acquire(expected_file_identity=identity)
    if lease.held_file_identity() != _identity(paths.writer_lock):
        raise PermissionError("watch sealing lost its barrier writer lease")


def seal(
    root: Path, binding: Document, lease: SingleInstance, *,
    validate: Callable[[], None], check_deadline: Callable[[], None],
) -> Document:
    """Release drained original custody, seal without replacement, then serialize recovery."""
    return _seal(
        root, lease, read=lambda: read_sealing(root, binding),
        validate=validate, check_deadline=check_deadline,
    )


def _require_sealing_space(document: Document) -> None:
    # Reserve full Windows 128-bit file-ID widths before allocating a barrier.
    candidate = document if "seal" in document else {
        **document, "seal": {
            "backend": _BACKEND,
            "barrier": {"device": _IDENTITY_MAX, "inode": _IDENTITY_MAX}, "boot": None,
        },
    }
    for phase in (candidate["phase"], "sealed-legacy"):
        if len(json.dumps({**candidate, "phase": phase}, allow_nan=False).encode("utf-8")) > _MAX_METADATA_BYTES:
            raise ValueError("watch Windows sealing journal exceeds its bounded size")


def _seal(
    root: Path, lease: SingleInstance, *,
    read: Callable[[], tuple[Document, str]],
    validate: Callable[[], None], check_deadline: Callable[[], None],
) -> Document:
    """Share the physical owner/order; each entry supplies its own strict reader."""
    validate()
    document, topology = read()
    _require_sealing_space(document)
    paths = watch_state_paths(root)
    if topology == "original":
        retain_original_lease(root, document, False, lease)
    elif lease.held and (topology != "sealed" or lease.lock_path != paths.writer_lock):
        raise PermissionError("watch sealing must not hold the moved original inode")

    def recheck(*, original_held: bool = False, barrier_held: bool = False) -> None:
        validate()
        if read() != (document, topology):
            raise PermissionError("watch Windows sealing evidence changed before its boundary")
        if original_held and lease.held_file_identity() != _record_identity(document["lease"]):
            raise PermissionError("watch Windows original custody changed")
        if barrier_held and (
            lease.lock_path != paths.writer_lock
            or lease.held_file_identity() != _identity(paths.writer_lock)
        ):
            raise PermissionError("watch Windows barrier custody changed")
        WatchRegistry().restore_state(
            _read_subscriptions_state(paths.legacy_subscriptions, require_plain=True), strict=True,
        )
        check_deadline()

    recheck(original_held=topology == "original")

    def publish(value: Document, before_publish: Callable[[], None]) -> None:
        if len(json.dumps(value, allow_nan=False).encode("utf-8")) > _MAX_METADATA_BYTES:
            raise ValueError("watch Windows sealing journal exceeds its bounded size")
        write_private(paths.journal, value, before_publish=before_publish, create_parent=False)

    if "seal" not in document:
        prior_bytes = _regular_bytes(paths.journal)
        if prior_bytes is None or json.dumps(
            json.loads(prior_bytes, object_pairs_hook=_unique_object, parse_constant=_reject_constant),
            sort_keys=True,
        ) != json.dumps(document, sort_keys=True):
            raise PermissionError("watch Windows preparation changed before staging")
        paths.staged_barrier.mkdir(mode=0o700)
        _windows_private(paths.staged_barrier, protect=True)
        barrier = _identity(paths.staged_barrier, directory=True)
        staged = {
            **document, "seal": {
                "backend": _BACKEND, "barrier": {"device": barrier[0], "inode": barrier[1]},
                "boot": None,
            },
        }

        def before_staging() -> None:
            validate()
            require_private(paths.journal)
            if (
                _regular_bytes(paths.journal) != prior_bytes
                or _identity(paths.historical_lock) != _record_identity(document["lease"])
                or _pin_identity(root) != _record_identity(document["lease"])
                or _identity(paths.staged_barrier, directory=True) != barrier
                or lease.held_file_identity() != _record_identity(document["lease"])
                or os.path.lexists(paths.retained_lock)
                or os.path.lexists(paths.isolated_subscriptions.parent)
            ):
                raise PermissionError("watch Windows preparation changed while staging")
            WatchRegistry().restore_state(
                _read_subscriptions_state(paths.legacy_subscriptions, require_plain=True), strict=True,
            )
            check_deadline()

        publish(staged, before_staging)
        document = staged
    confirm_private(paths.journal)
    recheck(original_held=topology == "original")
    if topology == "original":
        lease.release()
        recheck()
        windows_move_no_replace(paths.historical_lock, paths.retained_lock)
        topology = "moved"
        recheck()
    if topology == "moved":
        recheck()
        windows_move_no_replace(paths.staged_barrier, paths.historical_lock)
        topology = "sealed"
        recheck()
    _acquire_barrier_lease(root, document, lease)
    recheck(barrier_held=True)
    if document["phase"] == "sealed-legacy":
        confirm_private(paths.journal)
        recheck(barrier_held=True)
        return document
    sealed = {**document, "phase": "sealed-legacy"}
    publish(sealed, lambda: recheck(barrier_held=True))
    document = sealed
    recheck(barrier_held=True)
    return document
