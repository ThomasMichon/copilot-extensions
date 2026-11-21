"""Guarded historical-state preparation, not sealing or authority selection."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from single_instance_lease import SingleInstance

from .watch_private import (
    _sync_directory, confirm_private, require_private, write_private as write_private,
)
from .watch_registry import WatchRegistry
from .watch_storage import (
    _read_subscriptions_state,
    _regular_bytes,
    _reject_constant,
    _unique_object,
    read_transition_phase,
    watch_state_paths,
)

Document = dict[str, Any]
_SCHEMA = "agent-pull-requests.watch-state-transition"
_FIELDS = {"schema", "version", "phase", "binding", "lease"}


def _validate_record(document: Any, binding: Document, *, phase: str = "prepared") -> None:
    if (
        not isinstance(document, dict) or set(document) != _FIELDS
        or document["schema"] != _SCHEMA or type(document["version"]) is not int
        or document["version"] != 1 or document["phase"] != phase
        or json.dumps(document["binding"], sort_keys=True) != json.dumps(binding, sort_keys=True)
        or not isinstance(document["lease"], dict)
        or set(document["lease"]) != {"device", "inode"}
        or any(type(value) is not int or value < 0 for value in document["lease"].values())
    ):
        raise PermissionError("watch preparation journal binding or evidence is invalid")


def _validate_binding(root: Path, binding: Document) -> None:
    if (
        binding.get("stateRoot") != str(root)
        or (
            binding.get("kind") == "legacy" and set(binding) != {"kind", "stateRoot"}
        )
        or (
            binding.get("kind") == "installation" and (
                set(binding) != {"kind", "stateRoot", "installation"}
                or not isinstance(binding["installation"], dict)
                or binding["installation"].get("stateRoot") != str(root)
            )
        )
        or binding.get("kind") not in {"legacy", "installation"}
    ):
        raise PermissionError("watch preparation binding differs from its state root")


def _historical_identity(root: Path) -> tuple[int, int]:
    if not root.is_absolute() or root.resolve(strict=True) != root:
        raise PermissionError("watch preparation requires its canonical state root")
    info = watch_state_paths(root).historical_lock.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & 0x400
    ):
        raise PermissionError("watch preparation requires the original plain lease file")
    return info.st_dev, info.st_ino


def _pin_identity(root: Path) -> tuple[int, int]:
    try:
        info = watch_state_paths(root).preparation_pin.lstat()
    except OSError as error:
        raise PermissionError("watch preparation original inode pin is unavailable") from error
    if (
        not stat.S_ISREG(info.st_mode) or info.st_nlink < 2
        or getattr(info, "st_file_attributes", 0) & 0x400
    ):
        raise PermissionError("watch preparation requires its original non-link inode pin")
    return info.st_dev, info.st_ino


def _confirm_pin(root: Path, identity: tuple[int, int]) -> None:
    if _pin_identity(root) != identity:
        raise PermissionError("watch preparation inode pin differs from the original lease")
    descriptor = os.open(
        watch_state_paths(root).preparation_pin,
        os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != identity:
            raise PermissionError("watch preparation inode pin changed before confirmation")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _sync_directory(root)
    if _pin_identity(root) != identity:
        raise PermissionError("watch preparation inode pin changed during confirmation")


def _create_pin(root: Path, identity: tuple[int, int]) -> None:
    paths = watch_state_paths(root)
    os.link(paths.historical_lock, paths.preparation_pin, follow_symlinks=False)
    _confirm_pin(root, identity)


def read_preparation(
    root: Path, binding: Document, *, preparing_pin: bool = False,
) -> Document | None:
    """Validate a pre-move record before opening the original historical lease."""
    identity = _historical_identity(root)
    _validate_binding(root, binding)
    paths = watch_state_paths(root)
    phase = (
        read_transition_phase(root, allow_preparation_pin=True) if preparing_pin
        else read_transition_phase(root)
    )
    if phase == "absent":
        if preparing_pin and _pin_identity(root) != identity:
            raise PermissionError("watch preparation inode pin differs from the original lease")
        return None
    if phase != "prepared":
        raise PermissionError("watch preparation cannot recover a physical transition")
    # A prepared label never licenses use of a retained inode or a second lease.
    for path in (paths.retained_lock, paths.staged_barrier, paths.isolated_subscriptions.parent):
        if os.path.lexists(path):
            raise PermissionError("watch preparation has unexpected physical transition evidence")
    require_private(paths.journal)
    raw = _regular_bytes(paths.journal)
    if raw is None:
        raise PermissionError("watch preparation journal disappeared")
    document = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    _validate_record(document, binding)
    if identity != (document["lease"]["device"], document["lease"]["inode"]):
        raise PermissionError("watch preparation no longer names the original lease inode")
    if _pin_identity(root) != identity:
        raise PermissionError("watch preparation inode pin differs from the original lease")
    return cast(Document, document)


def prepare(
    root: Path, binding: Document, lease: SingleInstance, *,
    validate: Callable[[], None], check_deadline: Callable[[], None],
) -> Document:
    """Publish only after completed drain; the owner retains the original lease.

    The validator rechecks the owner's lifecycle, scope and deadline. It is not
    a substitute for native custody, and this record grants no later transition.
    """
    paths = watch_state_paths(root)
    if lease.lock_path != paths.historical_lock:
        raise PermissionError("watch preparation lease belongs to another state root")
    identity = lease.held_file_identity()
    if identity != _historical_identity(root):
        raise PermissionError("watch preparation does not hold the original lease inode")
    prior = read_preparation(root, binding)
    # Strict restoration validates all legacy outcomes without adopting effects.
    WatchRegistry().restore_state(
        _read_subscriptions_state(paths.legacy_subscriptions, require_plain=True), strict=True,
    )
    document = prior or {
        "schema": _SCHEMA, "version": 1, "phase": "prepared", "binding": binding,
        "lease": {"device": identity[0], "inode": identity[1]},
    }

    def recheck(*, preparing_pin: bool = False) -> None:
        validate()
        if lease.held_file_identity() != identity or _historical_identity(root) != identity:
            raise PermissionError("watch preparation native custody changed")
        if read_preparation(root, binding, preparing_pin=preparing_pin) != prior:
            raise PermissionError("watch preparation changed before publication")
        check_deadline()

    recheck()
    if prior is None:
        _create_pin(root, identity)
        write_private(
            paths.journal, document, replace=False,
            before_publish=lambda: recheck(preparing_pin=True), create_parent=False,
        )
    else:
        _confirm_pin(root, identity)
        # An interrupted durability acknowledgement is not proof of persistence.
        confirm_private(paths.journal)
        recheck()
    validate()
    if lease.held_file_identity() != identity or read_preparation(root, binding) != document:
        raise PermissionError("watch preparation changed after publication; recovery is required")
    check_deadline()
    return document
