import os
import sqlite3
import sys
from contextlib import closing

import pytest

from single_instance_lease.operation import OperationLock
from update_driver import (
    InterruptedError, RecoveryError, RequestSpec, TrustedRunner, drain, enqueue,
    kernel_boot_identity, recover_after_restart, recovery_status, status,
)
from update_driver import boot

PLATFORM = "windows" if os.name == "nt" else ("darwin" if sys.platform == "darwin" else "linux")
OLD = PLATFORM + ":11111111-1111-4111-8111-111111111111"
NEW = PLATFORM + ":22222222-2222-4222-8222-222222222222"


def interrupted(area, monkeypatch, *, pending=False):
    root = area / "state"
    monkeypatch.setattr(boot, "kernel_boot_identity", lambda: OLD)
    request = enqueue(
        RequestSpec("fixture"), runner=TrustedRunner((sys.executable,), str(area)), root=root,
    )
    if pending:
        enqueue(RequestSpec("fixture", argv=("next",)), runner=request.runner, root=root)

    def fail(tx):
        assert tx.request.boot_id == OLD
        raise RuntimeError("synthetic interruption")

    with pytest.raises(RuntimeError):
        drain(root=root, executor=fail)
    return root, status(request.id, root=root)


def test_native_boot_identity_is_stable_and_valid():
    first = kernel_boot_identity()
    assert boot.valid_boot_identity(first)
    assert kernel_boot_identity() == first
    assert first.startswith(PLATFORM + ":")


@pytest.mark.parametrize("failure", ["none", "errno", "short", "unterminated", "zero", "invalid"])
def test_darwin_boot_identity_uses_native_session_uuid(monkeypatch, failure):
    import ctypes
    from types import SimpleNamespace

    identifier = b"12345678-1234-4234-8234-123456789ABC"

    def query(name, data, size, new, new_size):
        assert name == b"kern.bootsessionuuid"
        assert new is None and new_size == 0 and size._obj.value == 37
        if failure == "errno":
            ctypes.set_errno(1)
            return -1
        value = b"0" * 36 if failure == "invalid" else identifier
        if failure == "zero":
            value = b"00000000-0000-0000-0000-000000000000"
        data.raw = value + (b"x" if failure == "unterminated" else b"\0")
        size._obj.value = 36 if failure == "short" else 37
        return 0

    def load(path, **kwargs):
        assert path == "/usr/lib/libSystem.B.dylib" and kwargs == {"use_errno": True}
        return SimpleNamespace(sysctlbyname=query)

    monkeypatch.setattr(boot, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(boot, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(ctypes, "CDLL", load)
    if failure == "none":
        assert boot.kernel_boot_identity() == "darwin:" + identifier.decode().lower()
    else:
        with pytest.raises((OSError, RuntimeError)):
            boot.kernel_boot_identity()


@pytest.mark.parametrize("value", [
    "darwin:00000000-0000-0000-0000-000000000000",
    "darwin:12345678-1234-4234-8234-123456789ABC",
    "darwin:12345678123442348234123456789abc",
    "freebsd:12345678-1234-4234-8234-123456789abc",
])
def test_boot_identity_rejects_noncanonical_evidence(value):
    assert not boot.valid_boot_identity(value)


def test_same_boot_refuses_and_preserves_failure(area, monkeypatch):
    root, request = interrupted(area, monkeypatch)
    before = (root / "queue.sqlite3").read_bytes()
    availability = recovery_status(root=root)
    assert availability.eligible_ids == ()
    assert availability.refused[0].reason == "same_kernel_boot"
    with pytest.raises(RecoveryError, match="same_kernel_boot"):
        recover_after_restart(root=root)
    assert (root / "queue.sqlite3").read_bytes() == before
    assert status(request.id, root=root) == request


def test_changed_boot_settles_without_replay_or_success(area, monkeypatch):
    root, request = interrupted(area, monkeypatch, pending=True)
    pending = status(root=root)[1]
    monkeypatch.setattr(boot, "kernel_boot_identity", lambda: NEW)
    assert recovery_status(root=root).eligible_ids == (request.id,)
    outcomes = recover_after_restart(root=root)
    assert len(outcomes) == 1
    settled = outcomes[0]
    assert settled.state == "interrupted" and settled.exit_code is None
    assert settled.error == request.error
    assert settled.boot_id == OLD and settled.settled_boot_id == NEW
    assert settled.settled_at is not None
    assert status(pending.id, root=root).state == "pending"
    assert recover_after_restart(root=root) == ()
    executed = []
    drain(root=root, executor=lambda tx: executed.append(tx.request.id) or 0)
    assert executed == [pending.id]
    assert status(request.id, root=root).state == "interrupted"


def test_crashed_running_row_settles_as_interrupted_after_restart(area, monkeypatch):
    root, request = interrupted(area, monkeypatch)
    with closing(sqlite3.connect(root / "queue.sqlite3")) as db, db:
        db.execute(
            "UPDATE requests SET state='running',error=NULL,finished_at=NULL WHERE id=?",
            (request.id,),
        )
    monkeypatch.setattr(boot, "kernel_boot_identity", lambda: NEW)
    settled, = recover_after_restart(root=root)
    assert settled.state == "interrupted" and settled.exit_code is None
    assert settled.finished_at is not None and settled.settled_at is not None


@pytest.mark.parametrize("evidence", [None, "", "old", "windows:not-a-guid"])
def test_missing_malformed_admission_evidence_refuses(area, monkeypatch, evidence):
    root, request = interrupted(area, monkeypatch)
    with closing(sqlite3.connect(root / "queue.sqlite3")) as db, db:
        db.execute("UPDATE requests SET boot_id=? WHERE id=?", (evidence, request.id))
    monkeypatch.setattr(boot, "kernel_boot_identity", lambda: NEW)
    assert recovery_status(root=root).refused[0].reason == (
        "missing_or_malformed_admission_boot_identity"
    )
    with pytest.raises(RecoveryError):
        recover_after_restart(root=root)
    assert status(request.id, root=root).settled_at is None


def test_unavailable_current_boot_is_visible_and_readonly(area, monkeypatch):
    root, request = interrupted(area, monkeypatch)

    def unavailable():
        raise OSError("synthetic native reader failure")

    monkeypatch.setattr(boot, "kernel_boot_identity", unavailable)
    availability = recovery_status(root=root)
    assert availability.current_boot_id is None
    assert availability.error == "boot_identity_unavailable:OSError"
    with pytest.raises(RecoveryError, match="boot_identity_unavailable"):
        recover_after_restart(root=root)
    assert status(request.id, root=root).settled_at is None


def test_active_custody_cannot_be_recovered(area, monkeypatch):
    root, _request = interrupted(area, monkeypatch)
    monkeypatch.setattr(boot, "kernel_boot_identity", lambda: NEW)
    with OperationLock(root / "execution.lock"):
        with pytest.raises(RecoveryError, match="execution is active"):
            recover_after_restart(root=root)


def test_one_same_boot_fence_refuses_partial_settlement(area, monkeypatch):
    root, request = interrupted(area, monkeypatch, pending=True)
    other = status(root=root)[1]
    with closing(sqlite3.connect(root / "queue.sqlite3")) as db, db:
        db.execute(
            "UPDATE requests SET state='interrupted',boot_id=? WHERE id=?", (NEW, other.id),
        )
    monkeypatch.setattr(boot, "kernel_boot_identity", lambda: NEW)
    with pytest.raises(RecoveryError, match="same_kernel_boot"):
        recover_after_restart(root=root)
    assert status(request.id, root=root).settled_at is None
    with pytest.raises(InterruptedError):
        drain(root=root)


def test_availability_and_empty_recovery_never_create_state(area):
    root = area / "missing"
    assert recovery_status(root=root).error is None
    with pytest.raises(RecoveryError, match="queue missing"):
        recover_after_restart(root=root)
    assert not root.exists()


@pytest.mark.parametrize("replace", [False, True], ids=["disappeared", "replaced"])
def test_recovery_mutation_never_recreates_or_accepts_replaced_database(
    area, monkeypatch, replace,
):
    import os
    import shutil
    from contextlib import contextmanager
    from update_driver import recovery

    root, _request = interrupted(area, monkeypatch)
    monkeypatch.setattr(boot, "kernel_boot_identity", lambda: NEW)
    database = root / "queue.sqlite3"
    original_bytes = database.read_bytes()
    original_database = recovery._database

    @contextmanager
    def disappear_before_mutation(path, **kwargs):
        assert kwargs["writable"] is True and not kwargs.get("create", False)
        if replace:
            replacement = root / "replacement.sqlite3"
            shutil.copyfile(database, replacement)
            os.replace(replacement, database)
        else:
            database.unlink()
        with original_database(path, **kwargs) as db:
            yield db

    monkeypatch.setattr(recovery, "_database", disappear_before_mutation)
    with pytest.raises(RecoveryError, match="disappeared|replaced"):
        recover_after_restart(root=root)
    if replace:
        assert database.read_bytes() == original_bytes
    else:
        assert not database.exists()
