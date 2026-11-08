"""Operator authorization releases legacy fences without inventing cleanup proof."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from single_instance_lease.operation import OperationLock
from update_driver import (
    RequestSpec, StateError, core, drain, enqueue, foundation_state, recovery, status,
)
from test_settlement import legacy_queue, request


def fenced(area: Path, *, schema: int = 2, state: str = "interrupted") -> tuple[Path, str]:
    root = legacy_queue(area)
    failed = request(area, root)
    with closing(sqlite3.connect(root / "queue.sqlite3")) as db, db:
        if schema >= 3:
            db.execute("ALTER TABLE requests ADD COLUMN settlement_kind TEXT")
        if schema >= 4:
            db.execute("ALTER TABLE requests ADD COLUMN recovery_authority TEXT")
            db.execute("ALTER TABLE requests ADD COLUMN released_at REAL")
        db.execute(f"PRAGMA user_version={schema}")
        db.execute(
            """UPDATE requests SET state=?,error='old preparation failure',
            started_at=1,finished_at=2,boot_id=NULL WHERE id=?""", (state, failed.id),
        )
    return root, failed.id


@pytest.mark.parametrize("schema", [2, 3, 4])
def test_operator_repair_releases_legacy_without_historical_evidence(
    area: Path, monkeypatch: pytest.MonkeyPatch, schema: int,
) -> None:
    root, failed_id = fenced(area, schema=schema)
    failed = status(failed_id, root=root)
    # Pending work was already accepted before the interruption.
    with closing(sqlite3.connect(root / "queue.sqlite3")) as db, db:
        db.execute("UPDATE requests SET state='pending' WHERE id=?", (failed_id,))
    pending = enqueue(
        RequestSpec("fixture", argv=("next",)), runner=failed.runner, root=root,
    )
    with closing(sqlite3.connect(root / "queue.sqlite3")) as db, db:
        db.execute("UPDATE requests SET state='interrupted' WHERE id=?", (failed_id,))

    def unavailable() -> str:
        pytest.fail("operator authorization must not require kernel boot evidence")

    monkeypatch.setattr(recovery.boot, "kernel_boot_identity", unavailable)
    released, = recovery.recover_for_repair(root=root, authorized=True)
    assert released.id == failed.id and released.state == "interrupted"
    assert released.error == "old preparation failure" and released.exit_code is None
    assert released.started_at == 1 and released.finished_at == 2
    assert released.boot_id is None and released.settled_boot_id is None
    assert released.settled_at is None and released.settlement_kind is None
    assert released.recovery_authority == "operator_repair"
    assert released.released_at is not None
    assert status(pending.id, root=root).state == "pending"
    assert recovery.recover_for_repair(root=root, authorized=True) == ()
    monkeypatch.undo()
    executed: list[str] = []
    drain(root=root, executor=lambda tx: executed.append(tx.request.id) or 0)
    assert executed == [pending.id]
    assert status(failed.id, root=root) == released
    with OperationLock(root / "execution.lock") as custody:
        operation = foundation_state.begin(root, "a" * 64, custody)
        foundation_state.finish(root, operation, custody, exit_code=0)
    assert enqueue(
        RequestSpec("fixture", argv=("new",)), runner=failed.runner, root=root,
    ).state == "pending"


def test_operator_repair_without_authorization_is_readonly(
    area: Path,
) -> None:
    root = legacy_queue(area)
    request(area, root)
    before = (root / "queue.sqlite3").read_bytes()
    with pytest.raises(recovery.RecoveryError, match="authorization"):
        recovery.recover_for_repair(root=root)
    assert (root / "queue.sqlite3").read_bytes() == before


@pytest.mark.parametrize("authorized", [False, None, 1, "true"])
def test_operator_repair_requires_literal_authorization(area: Path, authorized: object) -> None:
    root, _ = fenced(area)
    before = (root / "queue.sqlite3").read_bytes()
    with pytest.raises(recovery.RecoveryError, match="authorization"):
        recovery.recover_for_repair(root=root, authorized=authorized)
    assert (root / "queue.sqlite3").read_bytes() == before


@pytest.mark.parametrize("state", ["running", "interrupted", "blocked"])
def test_operator_repair_preserves_failed_outcomes(area: Path, state: str) -> None:
    root, identifier = fenced(area, state=state)
    released, = recovery.recover_for_repair(root=root, authorized=True)
    assert released.id == identifier and released.state == (
        "interrupted" if state == "running" else state
    )
    assert released.error == "old preparation failure" and released.exit_code is None
    assert released.finished_at == 2 and released.boot_id is None
    assert not released.unresolved and released.settled_at is None


def test_operator_repair_does_not_replace_live_custody(area: Path) -> None:
    root, _ = fenced(area)
    before = (root / "queue.sqlite3").read_bytes()
    with OperationLock(root / "execution.lock"):
        with pytest.raises(recovery.RecoveryError, match="execution is active"):
            recovery.recover_for_repair(root=root, authorized=True)
    assert (root / "queue.sqlite3").read_bytes() == before


def test_operator_repair_migration_failure_rolls_back(area: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = fenced(area)
    before = (root / "queue.sqlite3").read_bytes()
    validate = core._validate_db

    def fail(db: sqlite3.Connection) -> None:
        validate(db)
        if db.execute("PRAGMA user_version").fetchone()[0] == core.SCHEMA:
            raise StateError("synthetic migration failure")

    monkeypatch.setattr(core, "_validate_db", fail)
    with pytest.raises(recovery.RecoveryError, match="migration failure"):
        recovery.recover_for_repair(root=root, authorized=True)
    assert (root / "queue.sqlite3").read_bytes() == before


@pytest.mark.parametrize("change", [
    {"recovery_authority": None},
    {"released_at": None},
    {"recovery_authority": "native_scope"},
    {"released_at": "yesterday"},
    {"released_at": -1},
    {"finished_at": None},
    {"error": None},
    {"state": "pending"},
    {"state": "running"},
])
def test_malformed_operator_release_never_admits_work(area: Path, change: dict[str, object]) -> None:
    root, identifier = fenced(area)
    recovery.recover_for_repair(root=root, authorized=True)
    with closing(sqlite3.connect(root / "queue.sqlite3")) as db, db:
        db.execute(
            "UPDATE requests SET " + ",".join(f"{key}=?" for key in change) + " WHERE id=?",
            (*change.values(), identifier),
        )
    before = (root / "queue.sqlite3").read_bytes()
    with pytest.raises(StateError, match="recovery-authorization"):
        status(identifier, root=root)
    with pytest.raises(recovery.RecoveryError, match="recovery-authorization"):
        recovery.recover_for_repair(root=root, authorized=True)
    assert (root / "queue.sqlite3").read_bytes() == before


@pytest.mark.parametrize("replace", [False, True])
def test_operator_repair_refuses_database_identity_change(
    area: Path, monkeypatch: pytest.MonkeyPatch, replace: bool,
) -> None:
    import os
    import shutil
    from contextlib import contextmanager

    root, _ = fenced(area)
    database = root / "queue.sqlite3"
    before = database.read_bytes()
    original = recovery._database

    @contextmanager
    def changed(path: Path, **kwargs: object):
        if replace:
            replacement = root / "replacement.sqlite3"
            shutil.copyfile(database, replacement)
            os.replace(replacement, database)
        else:
            database.unlink()
        with original(path, **kwargs) as db:
            yield db

    monkeypatch.setattr(recovery, "_database", changed)
    with pytest.raises(recovery.RecoveryError, match="disappeared|replaced"):
        recovery.recover_for_repair(root=root, authorized=True)
    if replace:
        assert database.read_bytes() == before
    else:
        assert not database.exists()


def test_operator_repair_missing_queue_never_creates_state(area: Path) -> None:
    root = area / "missing"
    with pytest.raises(recovery.RecoveryError, match="queue missing"):
        recovery.recover_for_repair(root=root, authorized=True)
    assert not root.exists()


def test_operator_repair_never_silently_clears_foundation_fence(area: Path) -> None:
    root = legacy_queue(area)
    with OperationLock(root / "execution.lock") as custody:
        operation = foundation_state.begin(root, "a" * 64, custody)
        foundation_state.finish(root, operation, custody, exit_code=None, error="owner_lost_custody")
    before = (root / "queue.sqlite3").read_bytes()
    foundation = foundation_state.read(root)
    with pytest.raises(recovery.RecoveryError, match="foundation"):
        recovery.recover_for_repair(root=root, authorized=True)
    assert foundation_state.read(root) == foundation
    assert (root / "queue.sqlite3").read_bytes() == before


def test_schema_three_native_proof_survives_operator_migration(area: Path) -> None:
    root = legacy_queue(area)
    native = request(area, root)
    uncertain = enqueue(RequestSpec("fixture", argv=("second",)), runner=native.runner, root=root)
    with closing(sqlite3.connect(root / "queue.sqlite3")) as db, db:
        db.execute("ALTER TABLE requests ADD COLUMN settlement_kind TEXT")
        db.execute("PRAGMA user_version=3")
        db.execute(
            """UPDATE requests SET state='interrupted',error='native failure',finished_at=2,
            boot_id=?,settled_boot_id=?,settled_at=3,settlement_kind='native_scope' WHERE id=?""",
            (core.boot.kernel_boot_identity(), core.boot.kernel_boot_identity(), native.id),
        )
        db.execute(
            "UPDATE requests SET state='interrupted',error='uncertain failure' WHERE id=?",
            (uncertain.id,),
        )
    prior = status(native.id, root=root)
    released, = recovery.recover_for_repair(root=root, authorized=True)
    assert released.id == uncertain.id
    assert status(native.id, root=root) == prior
    assert prior.recovery_authority is None and prior.settlement_kind == "native_scope"
    assert recovery.recovery_status(root=root).refused == ()
    with closing(sqlite3.connect(root / "queue.sqlite3")) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == core.SCHEMA


def test_native_proof_and_operator_authority_cannot_be_conflated(area: Path) -> None:
    root, identifier = fenced(area)
    recovery.recover_for_repair(root=root, authorized=True)
    admission = core.boot.kernel_boot_identity()
    with closing(sqlite3.connect(root / "queue.sqlite3")) as db, db:
        db.execute(
            """UPDATE requests SET boot_id=?,settled_boot_id=?,settled_at=3,
            settlement_kind='native_scope' WHERE id=?""", (admission, admission, identifier),
        )
    with pytest.raises(StateError, match="recovery-authorization"):
        status(identifier, root=root)


def test_competing_operator_recovery_processes_release_once(area: Path) -> None:
    import json
    import sys
    from concurrent.futures import ThreadPoolExecutor
    from agent_procutil.owned_process import run_caller_owned

    root, identifier = fenced(area)
    child = area / "recover.py"
    child.write_text(
        "import json,sys\n"
        "from pathlib import Path\n"
        "from update_driver import RecoveryError,recover_for_repair\n"
        "try:\n"
        " rows=recover_for_repair(root=Path(sys.argv[1]),authorized=True)\n"
        " print(json.dumps([row.id for row in rows]))\n"
        "except RecoveryError as error:\n"
        " if 'execution is active' not in str(error): raise\n"
        " print('[]')\n",
        encoding="utf-8",
    )

    def invoke(_: int) -> list[str]:
        result = run_caller_owned(
            [sys.executable, str(child), str(root)], timeout=20,
            max_stdout_bytes=4096, max_stderr_bytes=4096,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(invoke, range(2)))
    assert sorted(item for result in results for item in result) == [identifier]
    released = status(identifier, root=root)
    assert released.recovery_authority == "operator_repair" and released.settled_at is None
