"""Guarded historical-state preparation, not sealing or authority selection."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

from single_instance_lease import SingleInstance

from .watch_private import confirm_private, require_private, write_private as write_private
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


def read_preparation(root: Path, binding: Document) -> Document | None:
    """Validate a pre-move record before opening the original historical lease."""
    identity = _historical_identity(root)
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
    paths = watch_state_paths(root)
    phase = read_transition_phase(root)
    if phase == "absent":
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
    if (
        not isinstance(document, dict) or set(document) != _FIELDS
        or document["schema"] != _SCHEMA or type(document["version"]) is not int
        or document["version"] != 1 or document["phase"] != "prepared"
        or json.dumps(document["binding"], sort_keys=True) != json.dumps(binding, sort_keys=True)
        or not isinstance(document["lease"], dict)
        or set(document["lease"]) != {"device", "inode"}
        or any(type(value) is not int or value < 0 for value in document["lease"].values())
    ):
        raise PermissionError("watch preparation journal binding or evidence is invalid")
    if identity != (document["lease"]["device"], document["lease"]["inode"]):
        raise PermissionError("watch preparation no longer names the original lease inode")
    return document


def prepare(
    root: Path, binding: Document, lease: SingleInstance, *,
    validate: Callable[[], None],
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
    WatchRegistry().restore_state(_read_subscriptions_state(paths.legacy_subscriptions), strict=True)
    document = prior or {
        "schema": _SCHEMA, "version": 1, "phase": "prepared", "binding": binding,
        "lease": {"device": identity[0], "inode": identity[1]},
    }

    def recheck() -> None:
        validate()
        if lease.held_file_identity() != identity or _historical_identity(root) != identity:
            raise PermissionError("watch preparation native custody changed")
        if read_preparation(root, binding) != prior:
            raise PermissionError("watch preparation changed before publication")

    recheck()
    if prior is None:
        write_private(paths.journal, document, replace=False, before_publish=recheck)
    else:
        # An interrupted durability acknowledgement is not proof of persistence.
        confirm_private(paths.journal)
        recheck()
    validate()
    if lease.held_file_identity() != identity or read_preparation(root, binding) != document:
        raise PermissionError("watch preparation changed after publication; recovery is required")
    return document
