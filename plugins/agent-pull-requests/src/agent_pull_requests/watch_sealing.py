"""Internal physical sealing; legacy state remains the sole authority."""

from __future__ import annotations

import json
import os
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from single_instance_lease import SingleInstance
from update_driver.boot import kernel_boot_identity, valid_boot_identity

from .watch_private import _sync_directory, confirm_private, require_private, write_private
from .watch_registry import WatchRegistry
from .watch_storage import (
    _read_subscriptions_state, _regular_bytes, _reject_constant, _unique_object,
    watch_state_paths,
)
from .watch_transition import (
    _pin_identity, _validate_binding, _validate_record, read_preparation,
)

Document = dict[str, Any]
_BACKEND = "linux-renameat2-exchange"


def require_backend() -> None:
    if sys.platform != "win32":
        _exchange_backend()


def _exchange_backend() -> Callable[[Path, Path], None]:
    if not sys.platform.startswith("linux"):
        raise NotImplementedError("watch physical sealing requires the Linux exchange backend")
    import ctypes

    try:
        rename = ctypes.CDLL(None, use_errno=True).renameat2
    except AttributeError as error:
        raise NotImplementedError("watch physical sealing requires renameat2 exchange") from error
    rename.argtypes = [
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint,
    ]
    rename.restype = ctypes.c_int

    def exchange(first: Path, second: Path) -> None:
        if rename(-100, os.fsencode(first), -100, os.fsencode(second), 2) != 0:
            code = ctypes.get_errno()
            raise OSError(code, "watch atomic lease/barrier exchange refused")

    return exchange


def _identity(path: Path, *, directory: bool = False) -> tuple[int, int]:
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise PermissionError("watch sealing requires plain native paths")
    if directory:
        if sys.platform == "win32":
            raise PermissionError("watch Linux sealing cannot validate a Windows barrier")
        if (
            info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700
            or next(path.iterdir(), None) is not None
        ):
            raise PermissionError("watch sealing barrier must be empty and owner-private")
    return info.st_dev, info.st_ino


def _record_identity(value: Any) -> tuple[int, int]:
    if (
        not isinstance(value, dict) or set(value) != {"device", "inode"}
        or any(type(item) is not int or item < 0 for item in value.values())
    ):
        raise PermissionError("watch sealing inode evidence is invalid")
    return value["device"], value["inode"]


def read_sealing(root: Path, binding: Document) -> tuple[Document, bool]:
    """Validate both sides of an interrupted exchange without opening a lease."""
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
        return prior, False
    phase = document.get("phase")
    if phase not in ("prepared", "sealed-legacy"):
        raise PermissionError("watch sealing cannot downgrade a later transition")
    _validate_record(
        {key: value for key, value in document.items() if key != "seal"}, binding, phase=phase,
    )
    seal = document["seal"]
    if (
        not isinstance(seal, dict) or set(seal) != {"backend", "barrier", "boot"}
        or seal["backend"] != _BACKEND
        or (
            phase == "prepared" and seal["boot"] is not None
        )
        or (
            phase == "sealed-legacy" and (
                not valid_boot_identity(seal["boot"])
                or not seal["boot"].startswith("linux:")
            )
        )
    ):
        raise PermissionError("watch sealing epoch evidence is invalid")
    original = _record_identity(document["lease"])
    barrier = _record_identity(seal["barrier"])
    if original == barrier or _pin_identity(root) != original:
        raise PermissionError("watch sealing lost its original inode pin")
    for path in (paths.staged_barrier, paths.isolated_subscriptions.parent):
        if os.path.lexists(path):
            raise PermissionError("watch sealing has conflicting transition evidence")
    exchanged = paths.historical_lock.is_dir()
    historical = _identity(paths.historical_lock, directory=exchanged)
    retained = _identity(paths.retained_lock, directory=not exchanged)
    if (
        (historical, retained) != ((barrier, original) if exchanged else (original, barrier))
        or (phase == "sealed-legacy" and not exchanged)
    ):
        raise PermissionError("watch sealing paths differ from the recorded exchange")
    return document, exchanged


def retain_original_lease(
    root: Path, document: Document, exchanged: bool, lease: SingleInstance,
) -> None:
    """Keep the same descriptor, or acquire only the validated original inode."""
    paths = watch_state_paths(root)
    selected = paths.retained_lock if exchanged else paths.historical_lock
    if lease.lock_path not in (paths.historical_lock, paths.retained_lock):
        raise PermissionError("watch sealing lease belongs to another state root")
    identity = _record_identity(document["lease"])
    lease.lock_path = selected
    if not lease.held:
        lease.acquire(expected_file_identity=identity)
    if lease.held_file_identity() != identity:
        raise PermissionError("watch sealing does not hold the original lease inode")


def seal(
    root: Path, binding: Document, lease: SingleInstance, *,
    validate: Callable[[], None], check_deadline: Callable[[], None],
) -> Document:
    """Seal/recover under drained custody; never import, qualify boot or activate."""
    if sys.platform == "win32":
        from .watch_windows_sealing import seal as seal_windows

        return seal_windows(
            root, binding, lease, validate=validate, check_deadline=check_deadline,
        )
    exchange = _exchange_backend()
    document, exchanged = read_sealing(root, binding)
    retain_original_lease(root, document, exchanged, lease)
    paths = watch_state_paths(root)

    def recheck() -> None:
        validate()
        if read_sealing(root, binding) != (document, exchanged):
            raise PermissionError("watch sealing evidence changed before its boundary")
        if lease.held_file_identity() != _record_identity(document["lease"]):
            raise PermissionError("watch sealing native custody changed")
        WatchRegistry().restore_state(
            _read_subscriptions_state(paths.legacy_subscriptions, require_plain=True), strict=True,
        )
        check_deadline()

    recheck()
    if "seal" not in document:
        prior_bytes = _regular_bytes(paths.journal)
        if prior_bytes is None or json.dumps(
            json.loads(prior_bytes, object_pairs_hook=_unique_object, parse_constant=_reject_constant),
            sort_keys=True,
        ) != json.dumps(document, sort_keys=True):
            raise PermissionError("watch sealing preparation changed before staging")
        paths.retained_lock.mkdir(mode=0o700)
        barrier = _identity(paths.retained_lock, directory=True)
        _sync_directory(paths.retained_lock)
        _sync_directory(root)
        staged = {
            **document,
            "seal": {
                "backend": _BACKEND, "barrier": {"device": barrier[0], "inode": barrier[1]},
                "boot": None,
            },
        }

        def before_staging() -> None:
            validate()
            # The newly staged directory deliberately invalidates read_preparation.
            require_private(paths.journal)
            if (
                _regular_bytes(paths.journal) != prior_bytes
                or _identity(paths.historical_lock) != _record_identity(document["lease"])
                or _pin_identity(root) != _record_identity(document["lease"])
                or _identity(paths.retained_lock, directory=True) != barrier
                or lease.held_file_identity() != _record_identity(document["lease"])
                or os.path.lexists(paths.staged_barrier)
                or os.path.lexists(paths.isolated_subscriptions.parent)
            ):
                raise PermissionError("watch sealing preparation changed while staging")
            WatchRegistry().restore_state(
                _read_subscriptions_state(paths.legacy_subscriptions, require_plain=True), strict=True,
            )
            check_deadline()

        write_private(
            paths.journal, staged, before_publish=before_staging, create_parent=False,
        )
        document = staged
        recheck()
    if not exchanged:
        confirm_private(paths.journal)
        recheck()
        exchange(paths.historical_lock, paths.retained_lock)
        exchanged = True
        retain_original_lease(root, document, exchanged, lease)
        recheck()
    _sync_directory(paths.historical_lock)
    _sync_directory(root)
    recheck()
    if document["phase"] == "sealed-legacy":
        confirm_private(paths.journal)
        recheck()
        return document
    boot = kernel_boot_identity()
    if not valid_boot_identity(boot) or not boot.startswith("linux:"):
        raise PermissionError("watch sealing requires a native Linux boot epoch")
    sealed = {**document, "phase": "sealed-legacy", "seal": {**document["seal"], "boot": boot}}
    write_private(paths.journal, sealed, before_publish=recheck, create_parent=False)
    document = sealed
    recheck()
    return document
