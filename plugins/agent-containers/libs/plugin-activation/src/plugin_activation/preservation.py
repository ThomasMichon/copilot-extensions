"""Durable activation restoration, admitted only after positive child settlement."""

from __future__ import annotations

import logging
import re
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from time import sleep
from typing import Any

from single_instance_lease import OperationLock, OperationLockedError, OperationOwnershipError

from . import state

_SCHEMA = "copilot-extensions.activation-preservation"
_FIELDS = {"schema", "version", "home", "transaction", "phase", "witness", "snapshot"}
_SNAPSHOT_FIELDS = set(state.ActivationSnapshot.__dataclass_fields__)
SETTLEMENT_RETRY_RESERVE_S = 2 * state.RESTORE_RETRY_DELAY_S


def _plain(path: Path, *, directory: bool = False) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if (
        not expected or path.is_symlink()
        or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise state.PluginStateError(f"activation custody artifact is not plain: {path}")
    return True


def _snapshot(value: Any) -> state.ActivationSnapshot:
    if not isinstance(value, dict) or set(value) != _SNAPSHOT_FIELDS:
        raise state.PluginStateError("activation transaction has an invalid snapshot shape")
    state.validate_identity(value["identity"])
    flags = (
        "settings_existed", "enabled_plugins_existed", "user_value_existed",
        "inventory_record_existed",
    )
    if any(type(value[key]) is not bool for key in flags) or any(
        value[key] is not None and type(value[key]) is not bool
        for key in ("user_value", "inventory_enabled")
    ):
        raise state.PluginStateError("activation transaction has invalid snapshot field types")
    if (
        value["user_value_existed"] != (value["user_value"] is not None)
        or (value["user_value_existed"] and not value["enabled_plugins_existed"])
        or (value["enabled_plugins_existed"] and not value["settings_existed"])
        or (value["inventory_enabled"] is not None and not value["inventory_record_existed"])
    ):
        raise state.PluginStateError("activation transaction has inconsistent snapshot fields")
    return state.ActivationSnapshot(**value)


def _read(path: Path, home: Path) -> dict[str, Any] | None:
    if not _plain(path):
        return None
    if path.stat().st_size > 16_384:
        raise state.PluginStateError("activation transaction exceeds its size limit")
    _, record = state.read_json_object(path)
    if (
        set(record) != _FIELDS or record["schema"] != _SCHEMA
        or type(record["version"]) is not int or record["version"] != 1
        or record["home"] != str(home)
        or not isinstance(record["transaction"], str)
        or re.fullmatch(r"[0-9a-f]{32}", record["transaction"]) is None
        or record["phase"] not in ("running", "quiescent")
        or (
            record["phase"] == "running" and record["witness"] is not None
        )
        or (
            record["phase"] == "quiescent"
            and record["witness"] not in ("windows-job-empty", "not-launched")
        )
    ):
        raise state.PluginStateError(f"activation transaction is invalid or foreign: {path}")
    _snapshot(record["snapshot"])
    return record


def _assert_record(
    lock: OperationLock, path: Path, home: Path, expected: dict[str, Any],
) -> None:
    lock.assert_owned()
    if _read(path, home) != expected:
        raise state.PluginStateError("activation transaction changed during preservation")


def _publish(
    lock: OperationLock, path: Path, home: Path, record: dict[str, Any],
    previous: dict[str, Any] | None,
) -> None:
    for attempt in range(2):
        lock.assert_owned()
        if _read(path, home) != previous:
            raise state.PluginStateError("activation transaction changed before publication")
        try:
            state.write_json_object_atomic(path, record)
            return
        except OSError as error:
            code = getattr(error, "winerror", None)
            if attempt or code not in (32, 33):
                raise
            logging.getLogger(__name__).warning(
                "Activation snapshot publication for %s hit Windows lock error %s; "
                "retrying the same transition once",
                record["snapshot"]["identity"], code,
            )
            sleep(state.RESTORE_RETRY_DELAY_S)


def _verify_restored(snapshot: state.ActivationSnapshot, home: Path) -> None:
    documents = state._load_state(snapshot.identity, home)
    enabled = documents.settings.get("enabledPlugins", {})
    if (
        (snapshot.identity in enabled) != snapshot.user_value_existed
        or documents.user_activation != snapshot.user_value
        or (snapshot.enabled_plugins_existed and "enabledPlugins" not in documents.settings)
        or (snapshot.settings_existed and not documents.settings_path.exists())
    ):
        raise state.PluginStateError("original user activation was not restored")
    expected_inventory = (
        snapshot.inventory_enabled if snapshot.inventory_enabled is not None
        else bool(snapshot.user_value) if snapshot.user_value_existed else False
    )
    if documents.record is None:
        if snapshot.inventory_record_existed:
            raise state.PluginStateError("original plugin inventory disappeared during restoration")
    elif documents.record.get("enabled") is not expected_inventory:
        raise state.PluginStateError("original inventory activation was not restored")


def _restore(
    lock: OperationLock, path: Path, home: Path, record: dict[str, Any],
) -> None:
    _assert_record(lock, path, home, record)
    if record["phase"] != "quiescent":
        raise state.PluginStateError(
            f"activation command settlement is unproven; original snapshot retained at {path}"
        )
    snapshot = _snapshot(record["snapshot"])
    state.restore(snapshot, home)
    _verify_restored(snapshot, home)
    _assert_record(lock, path, home, record)
    path.unlink()


class ActivationPreservation:
    """A process owner may attest empty-Job cleanup or refusal before execution."""

    def __init__(
        self, lock: OperationLock, path: Path, home: Path, record: dict[str, Any],
    ) -> None:
        self._lock = lock
        self._path = path
        self._home = home
        self._record = record

    def _confirm(self, witness: str) -> None:
        if self._record["phase"] != "running":
            raise state.PluginStateError("activation command settlement was already confirmed")
        record = {**self._record, "phase": "quiescent", "witness": witness}
        _publish(self._lock, self._path, self._home, record, self._record)
        self._record = record
        _assert_record(self._lock, self._path, self._home, record)

    def confirm_quiescent(self) -> None:
        """Call only after the Windows process owner confirms Job empty and root reaped/drained."""
        self._confirm("windows-job-empty")

    def confirm_not_launched(self) -> None:
        """Call only when the process owner proves refusal before child execution."""
        self._confirm("not-launched")

    def _finish(self) -> None:
        _restore(self._lock, self._path, self._home, self._record)


class ActivationCustody:
    """Hold one home's acquisition custody, including update-only recovery."""

    def __init__(self, lock: OperationLock, path: Path, home: Path) -> None:
        self._lock, self._path, self._home = lock, path, home

    def _recover(self) -> None:
        self._lock.assert_owned()
        previous = _read(self._path, self._home)
        if previous is not None:
            _restore(self._lock, self._path, self._home, previous)

    @contextmanager
    def preserve(self, identity: str) -> Iterator[ActivationPreservation]:
        state.validate_identity(identity)
        self._recover()
        snapshot = state.capture(identity, self._home)
        record = {
            "schema": _SCHEMA, "version": 1, "home": str(self._home),
            "transaction": uuid.uuid4().hex, "phase": "running",
            "witness": None, "snapshot": asdict(snapshot),
        }
        _publish(self._lock, self._path, self._home, record, None)
        preservation = ActivationPreservation(self._lock, self._path, self._home, record)
        try:
            yield preservation
        except BaseException:
            if preservation._record["phase"] == "quiescent":
                preservation._finish()
            raise
        else:
            preservation._finish()


@contextmanager
def activation_custody(home: Path | None = None) -> Iterator[ActivationCustody]:
    """Recover witnessed restoration before any participating acquisition."""
    home = (home or state.copilot_home()).resolve()
    area = home / "activation-preservation"
    if not _plain(area, directory=True):
        area.mkdir(parents=True, exist_ok=True)
    _plain(area, directory=True)
    path = area / "transaction.json"
    try:
        with OperationLock(area / "operation.lock") as lock:
            custody = ActivationCustody(lock, path, home)
            custody._recover()
            yield custody
    except (OperationLockedError, OperationOwnershipError) as error:
        raise state.PluginStateError(f"activation preservation custody refused: {error}") from error


@contextmanager
def preserve_activation_durably(
    identity: str, home: Path | None = None,
) -> Iterator[ActivationPreservation]:
    """Retain originals; only the process owner may attest positive settlement."""
    state.validate_identity(identity)
    with activation_custody(home) as custody:
        with custody.preserve(identity) as preservation:
            yield preservation
