"""Journal adaptation under the existing native writer; never import or activate."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from single_instance_lease import SingleInstance
from zdd.cutover_lock import CutoverLock

from .watch_installation import WatchInstallation
from .watch_private import confirm_private, require_private, write_private as write_private
from .watch_registry import WatchRegistry
from .watch_storage import (
    _MAX_METADATA_BYTES, _read_subscriptions_state, _regular_bytes, _reject_constant, _unique_object,
    watch_state_paths,
)
from .watch_transition import (
    _FIELDS, _confirm_pin, _historical_identity, _pin_identity, _validate_binding,
    _validate_record,
)

Document = dict[str, Any]


def _controller_environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key != "COPILOT_PLUGIN_ROOT"}


def _validate_scope(
    root: Path, binding: object, installation: WatchInstallation | None,
    guard: CutoverLock | None,
) -> None:
    if not isinstance(binding, dict):
        raise PermissionError("watch transition recovery binding is invalid")
    _validate_binding(root, binding)
    if installation is None:
        if binding["kind"] != "legacy":
            raise PermissionError("watch transition recovery requires its governed installation")
    else:
        if binding["kind"] != "installation" or guard is None:
            raise PermissionError("watch transition recovery requires its installation transaction")
        installation.require_transition_origin(
            binding["installation"], guard=guard, environment=_controller_environment(),
        )


def _read_prepared(
    root: Path, installation: WatchInstallation | None, guard: CutoverLock | None,
) -> tuple[Document, bytes]:
    paths = watch_state_paths(root)
    identity = _historical_identity(root)
    for path in (paths.retained_lock, paths.staged_barrier, paths.isolated_subscriptions.parent):
        if os.path.lexists(path):
            raise PermissionError("watch prepared recovery has physical transition evidence")
    require_private(paths.journal)
    raw = _regular_bytes(paths.journal)
    if raw is None:
        raise PermissionError("watch prepared recovery journal disappeared")
    document = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    if not isinstance(document, dict) or type(document.get("version")) is not int:
        raise PermissionError("watch prepared recovery journal is invalid")
    if document["version"] == 1:
        origin = document
    elif (
        document["version"] == 2 and set(document) == _FIELDS | {"origin", "controller"}
        and isinstance(document["origin"], dict)
    ):
        origin = document["origin"]
        if any(
            json.dumps(document[field], sort_keys=True) != json.dumps(origin.get(field), sort_keys=True)
            for field in ("schema", "phase", "binding", "lease")
        ):
            raise PermissionError("watch prepared recovery changed immutable origin evidence")
        _validate_scope(root, document["controller"], installation, guard)
    else:
        raise PermissionError("watch prepared recovery journal version is unsupported")
    _validate_scope(root, origin.get("binding"), installation, guard)
    _validate_record(origin, origin["binding"])
    if (
        identity != (origin["lease"]["device"], origin["lease"]["inode"])
        or _historical_identity(root) != identity
        or _pin_identity(root) != identity
    ):
        raise PermissionError("watch prepared recovery no longer names its original pinned inode")
    for path in (paths.retained_lock, paths.staged_barrier, paths.isolated_subscriptions.parent):
        if os.path.lexists(path):
            raise PermissionError("watch prepared recovery has physical transition evidence")
    require_private(paths.journal)
    if _regular_bytes(paths.journal) != raw:
        raise PermissionError("watch prepared recovery journal changed during admission")
    return cast(Document, document), raw


def recover_prepared(
    root: Path, lease: SingleInstance, *,
    installation: WatchInstallation | None, guard: CutoverLock | None,
    validate: Callable[[], None], check_deadline: Callable[[], None],
) -> Document:
    """Adapt only validated prepared bytes; never select, seal or import state."""
    return _recover(
        root, lease, installation=installation, guard=guard, validate=validate,
        check_deadline=check_deadline, sealed=False,
    )


def _read_sealed(
    root: Path, installation: WatchInstallation | None, guard: CutoverLock | None,
) -> tuple[Document, bytes]:
    document, raw, _topology = _read_windows_transition(root, installation, guard, sealed=True)
    return document, raw


def _read_windows_transition(
    root: Path, installation: WatchInstallation | None, guard: CutoverLock | None, *,
    sealed: bool, validate_scope: Callable[[object], None] | None = None,
) -> tuple[Document, bytes, str]:
    from .watch_windows_sealing import _inspect_sealing

    if sys.platform != "win32":
        raise NotImplementedError("watch sealed recovery has no qualified POSIX domain epoch")
    if not root.is_absolute() or root.resolve(strict=True) != root:
        raise PermissionError("watch sealed recovery requires its canonical state root")
    paths = watch_state_paths(root)
    scope = validate_scope or (lambda binding: _validate_scope(root, binding, installation, guard))
    require_private(paths.journal)
    raw = _regular_bytes(paths.journal)
    if raw is None:
        raise PermissionError("watch sealed recovery journal disappeared")
    document = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    if not isinstance(document, dict) or type(document.get("version")) is not int:
        raise PermissionError("watch sealed recovery journal is invalid")
    if document["version"] == 1 and sealed:
        origin = document
    elif (
        document["version"] == 2
        and set(document) == _FIELDS | {"origin", "controller"} | ({"seal"} if "seal" in document else set())
        and isinstance(document["origin"], dict)
    ):
        origin = document["origin"]
        if any(
            json.dumps(document[field], sort_keys=True) != json.dumps(origin.get(field), sort_keys=True)
            for field in ("schema", "binding", "lease")
        ) or (
            "seal" in origin
            and json.dumps(document.get("seal"), sort_keys=True) != json.dumps(origin["seal"], sort_keys=True)
        ):
            raise PermissionError("watch sealed recovery changed immutable origin evidence")
        scope(document["controller"])
    else:
        raise PermissionError("watch sealed recovery journal version is unsupported")
    scope(origin.get("binding"))
    if origin.get("phase") == "sealed-legacy" and document.get("phase") != "sealed-legacy":
        raise PermissionError("watch current sealing cannot downgrade its sealed origin")
    if "seal" in origin:
        if _inspect_sealing(root, origin["binding"], origin) != "sealed" and sealed:
            raise PermissionError("watch sealed recovery origin no longer names its installed barrier")
    else:
        _validate_record(origin, origin["binding"])
    # Validate current physical facts without rewriting the retained v1 receipt.
    current = {key: value for key, value in document.items() if key in _FIELDS | {"seal"}}
    current["version"] = 1

    def topology() -> str:
        if "seal" in current:
            selected = _inspect_sealing(root, origin["binding"], current)
        else:
            _validate_record(current, origin["binding"])
            identity = _historical_identity(root)
            if identity != (origin["lease"]["device"], origin["lease"]["inode"]) or _pin_identity(root) != identity:
                raise PermissionError("watch current sealing lost its original pinned inode")
            for path in (paths.retained_lock, paths.staged_barrier, paths.isolated_subscriptions.parent):
                if os.path.lexists(path):
                    raise PermissionError("watch current sealing has unrecorded physical evidence")
            selected = "original"
        if sealed and (current.get("phase") != "sealed-legacy" or selected != "sealed"):
            raise PermissionError("watch sealed recovery requires its installed barrier")
        return selected

    selected = topology()
    require_private(paths.journal)
    if _regular_bytes(paths.journal) != raw:
        raise PermissionError("watch sealed recovery journal changed during admission")
    if topology() != selected:
        raise PermissionError("watch current sealing topology changed during admission")
    return cast(Document, document), raw, selected


def seal_recovered(
    root: Path, lease: SingleInstance, *,
    installation: WatchInstallation | None, guard: CutoverLock | None,
    validate: Callable[[], None], check_deadline: Callable[[], None],
) -> Document:
    """Progress a v2 envelope through the existing Windows owner, never its v1 reader."""
    from .watch_windows_sealing import _require_sealing_space, _seal

    validated_scopes: set[str] = set()
    controller: Document = {"kind": "legacy", "stateRoot": str(root)}
    if installation is not None:
        controller.update(kind="installation", installation=installation.evidence())

    def scope(binding: object) -> None:
        # Only immutable provenance interpretation is reused, within this one operation.
        key = json.dumps(binding, sort_keys=True, allow_nan=False)
        if key not in validated_scopes:
            _validate_scope(root, binding, installation, guard)
            validated_scopes.add(key)

    def read() -> tuple[Document, str]:
        if installation is not None:
            from zdd.cutover_lock import lock_path

            if guard is None or guard.path != lock_path(installation.run_root) or installation.state_root != root:
                raise PermissionError("watch current sealing requires its installation transaction")
            guard.require_owned()
            installation.require_current(environment=_controller_environment())
            guard.require_owned()
        elif guard is not None:
            raise PermissionError("watch legacy sealing has no installation transaction")
        document, _raw, topology = _read_windows_transition(
            root, installation, guard, sealed=False, validate_scope=scope,
        )
        _require_sealing_space({**document, "controller": controller})
        WatchRegistry().restore_state(
            _read_subscriptions_state(watch_state_paths(root).legacy_subscriptions, require_plain=True),
            strict=True,
        )
        check_deadline()
        return document, topology

    document = _seal(root, lease, read=read, validate=validate, check_deadline=check_deadline)
    if document["controller"] == controller:
        return document
    return recover_sealed(
        root, lease, installation=installation, guard=guard,
        validate=validate, check_deadline=check_deadline,
    )


def recover_sealed(
    root: Path, lease: SingleInstance, *,
    installation: WatchInstallation | None, guard: CutoverLock | None,
    validate: Callable[[], None], check_deadline: Callable[[], None],
) -> Document:
    """Receive only a qualified Windows barrier; retain legacy sole authority."""
    return _recover(
        root, lease, installation=installation, guard=guard, validate=validate,
        check_deadline=check_deadline, sealed=True,
    )


def _recover(
    root: Path, lease: SingleInstance, *,
    installation: WatchInstallation | None, guard: CutoverLock | None,
    validate: Callable[[], None], check_deadline: Callable[[], None], sealed: bool,
) -> Document:
    read = _read_sealed if sealed else _read_prepared
    validate()
    prior, original_bytes = read(root, installation, guard)
    identity: tuple[int, int] = (prior["lease"]["device"], prior["lease"]["inode"])
    paths = watch_state_paths(root)
    if not sealed and lease.lock_path != paths.historical_lock:
        raise PermissionError("watch prepared recovery lease belongs to another state root")
    controller: Document = {"kind": "legacy", "stateRoot": str(root)}
    if installation is not None:
        controller.update(kind="installation", installation=installation.evidence())
    origin = prior if prior["version"] == 1 else prior["origin"]
    document = {
        **prior, "version": 2, "origin": origin, "controller": controller,
    }
    published_bytes = json.dumps(document, allow_nan=False).encode("utf-8")
    if len(published_bytes) > _MAX_METADATA_BYTES:
        raise ValueError("watch transition recovery journal exceeds its bounded size")

    def source() -> None:
        WatchRegistry().restore_state(
            _read_subscriptions_state(paths.legacy_subscriptions, require_plain=True), strict=True,
        )

    source()
    validate()
    if read(root, installation, guard)[1] != original_bytes:
        raise PermissionError("watch transition recovery journal changed before acquisition")
    check_deadline()
    if sealed:
        from .watch_windows_sealing import _acquire_barrier_lease

        _acquire_barrier_lease(root, document, lease)
    elif not lease.held:
        lease.acquire(expected_file_identity=identity)

    def custody() -> None:
        if sealed:
            from .watch_windows_sealing import _identity

            if lease.lock_path != paths.writer_lock or lease.held_file_identity() != _identity(
                paths.writer_lock,
            ):
                raise PermissionError("watch sealed recovery native barrier custody changed")
        elif lease.held_file_identity() != identity:
            raise PermissionError("watch prepared recovery native custody changed")

    def recheck(expected_bytes: bytes) -> None:
        validate()
        custody()
        source()
        validate()
        _, current_bytes = read(root, installation, guard)
        if current_bytes != expected_bytes:
            raise PermissionError("watch transition recovery journal changed before publication")
        custody()
        check_deadline()

    recheck(original_bytes)
    _confirm_pin(root, identity)
    if document == prior:
        confirm_private(paths.journal)
        recheck(original_bytes)
    else:
        write_private(
            paths.journal, document, create_parent=False,
            before_publish=lambda: recheck(original_bytes),
        )
        recheck(published_bytes)
    return document
