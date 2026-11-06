"""Physical SQLite work and writer noninterference, not row-count/time proxies."""

from __future__ import annotations

import ast
import asyncio
from contextlib import closing
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_bridge import owner_membership as membership
from agent_bridge import owner_membership_read as reader
from agent_bridge.db import Database
from agent_bridge.owner_membership_signal import MembershipConnection
from agent_bridge.session_observation_contract import ObservationError
from test_owner_membership import OWNER, create, owner as owner


def unrelated_sessions(db, count=10_000):
    with db._write_lock:
        conn = db._get_conn()
        conn.executemany(
            "INSERT INTO sessions(id,name,target_type,status,target_json,created_at,updated_at) "
            "VALUES(?,?,'local','idle',?,1,1)",
            [
                (
                    f"unrelated-{index}",
                    "fixture",
                    json.dumps(
                        {
                            "caller_owner_ref": "fixture-host/demo/unrelated",
                            "project": "demo",
                            "worktree_id": "unrelated",
                        }
                    ),
                )
                for index in range(count)
            ],
        )
        conn.commit()


def unindexed(db: Database) -> None:
    with db._write_lock:
        conn = db._get_conn()
        for name in (
            "idx_sessions_membership_owner",
            "idx_relationships_membership_owner",
            "idx_live_sessions_membership_owner",
        ):
            conn.execute(f"DROP INDEX IF EXISTS {name}")
        conn.commit()


def limited_budget(db, monkeypatch):
    unindexed(db)
    budget_type = reader.ReadBudget
    baseline = reader.snapshot(
        db.db_path,
        (),
        budget=budget_type(max_steps=1_000_000, seconds=60),
    ).steps
    budgets = []

    def factory():
        result = budget_type(max_steps=baseline + 1000, seconds=60)
        budgets.append(result)
        return result

    monkeypatch.setattr(reader, "ReadBudget", factory)
    return budgets


def test_unknown_owner_large_history_has_actual_private_vm_cutoff(
    session_manager, tmp_db, monkeypatch
):
    unrelated_sessions(tmp_db)
    budgets = limited_budget(tmp_db, monkeypatch)
    with pytest.raises(ObservationError) as failed:
        membership.source_for(session_manager, "fixture-host/demo/unknown")
    assert failed.value.code == "capture-budget-exhausted"
    exhausted = [budget for budget in budgets if budget.exhausted]
    assert exhausted
    assert (
        exhausted[-1].max_steps
        < exhausted[-1].steps
        <= (exhausted[-1].max_steps + reader.PROGRESS_INTERVAL)
    )
    source = getattr(tmp_db, "_owner_membership_source", None)
    assert source is None or not source.has_owner("fixture-host/demo/unknown")
    assert tmp_db.get_session("unrelated-9999") is not None


def test_legacy_unindexed_query_scan_is_bounded_even_when_it_returns_no_rows(tmp_db):
    unindexed(tmp_db)
    unrelated_sessions(tmp_db)
    conn = tmp_db._get_conn()
    sql = (
        "SELECT id FROM sessions WHERE json_valid(target_json) AND "
        "CASE WHEN instr(json_extract(target_json,'$.caller_owner_ref'),'#')>0 "
        "THEN substr(json_extract(target_json,'$.caller_owner_ref'),1,"
        "instr(json_extract(target_json,'$.caller_owner_ref'),'#')-1) "
        "ELSE json_extract(target_json,'$.caller_owner_ref') END=? LIMIT 65"
    )
    plan = [
        row[3]
        for row in conn.execute(
            "EXPLAIN QUERY PLAN " + sql,
            ("fixture-host/demo/unknown",),
        )
    ]
    assert any("SCAN sessions" in item for item in plan)
    baseline = reader.snapshot(
        tmp_db.db_path,
        (),
        budget=reader.ReadBudget(max_steps=1_000_000, seconds=60),
    ).steps
    budget = reader.ReadBudget(max_steps=baseline + 1000, seconds=60)
    with pytest.raises(ObservationError) as failed:
        reader.snapshot(tmp_db.db_path, ("fixture-host/demo/unknown",), budget=budget)
    assert failed.value.code == "capture-budget-exhausted"
    assert budget.exhausted and budget.steps <= budget.max_steps + reader.PROGRESS_INTERVAL


def test_selected_owner_capture_does_not_scan_unrelated_registries(tmp_db: Database) -> None:
    unrelated_sessions(tmp_db)
    create(tmp_db, owner=OWNER + "#producer")
    foreign = "fixture-host/demo/unrelated#producer"
    with tmp_db._write_lock:
        conn = tmp_db._get_conn()
        conn.executemany(
            "INSERT INTO remote_acp_relationships(id,session_id,owner,recovery_json,phase) "
            "VALUES(?,?,?,'{}','reserved')",
            [(f"rel-{index}", f"unrelated-{index}", foreign) for index in range(10_000)]
            + [("selected-rel", "selected-remote", OWNER + "#producer")],
        )
        conn.executemany(
            "INSERT INTO live_sessions(session_id,status,venue,registered_at,updated_at) "
            "VALUES(?,'live',?,1,1)",
            [(f"live-{index}", json.dumps({"supervisor_ref": foreign})) for index in range(10_000)]
            + [("selected-live", json.dumps({"supervisor_ref": OWNER + "#producer"}))],
        )
        conn.execute(
            "INSERT INTO sessions(id,name,target_type,status,target_json,created_at,updated_at) "
            "VALUES('invalid-json','fixture','local','idle','not-json',1,1)"
        )
        conn.execute(
            "INSERT INTO live_sessions(session_id,status,venue,registered_at,updated_at) "
            "VALUES('invalid-json','live','not-json',1,1)"
        )
        conn.commit()
    baseline = reader.snapshot(
        tmp_db.db_path, (), budget=reader.ReadBudget(max_steps=1_000_000, seconds=60),
    ).steps
    budget = reader.ReadBudget(max_steps=baseline + 3000, seconds=60)
    result = reader.snapshot(
        tmp_db.db_path, (OWNER, "fixture-host/demo/unknown"), budget=budget,
    )
    assert set(result.states[OWNER]) == {
        "owned:child", "relationship:selected-rel", "represented:selected-live",
    }
    assert result.states["fixture-host/demo/unknown"] == {}
    assert not budget.exhausted


def test_only_writable_bootstrap_prepares_lookup_indexes(tmp_db: Database) -> None:
    unrelated_sessions(tmp_db)
    create(tmp_db)
    original = tmp_db.get_session("child")["target_json"]
    tmp_db.create_session(
        "invalid-json", "fixture", None, ".", "local", "idle", 1.0, target_json="not-json",
    )
    tmp_db.execute_write(
        "INSERT INTO live_sessions(session_id,status,venue,registered_at,updated_at) "
        "VALUES('invalid-json','live','not-json',1,1)"
    )
    path = tmp_db.db_path
    unindexed(tmp_db)
    tmp_db.close()
    with closing(Database(path, read_only=True)) as passive:
        with pytest.raises(ObservationError) as failed:
            reader.snapshot(
                path, ("fixture-host/demo/unknown",),
                budget=reader.ReadBudget(max_steps=100_000, seconds=60),
            )
        assert failed.value.code == "capture-budget-exhausted"
        assert passive.execute_read(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE '%membership_owner'"
        ) == []
    with closing(Database(path)) as prepared:
        capture = reader.snapshot(
            path, (OWNER, "fixture-host/demo/unknown"),
            budget=reader.ReadBudget(max_steps=100_000, seconds=60),
        )
        assert set(capture.states[OWNER]) == {"owned:child"}
        assert capture.states["fixture-host/demo/unknown"] == {}
        assert prepared.get_session("child")["target_json"] == original


def test_seed_never_takes_canonical_write_lock(session_manager, tmp_db):
    create(tmp_db)
    with tmp_db._write_lock:
        source = membership.source_for(session_manager, OWNER)
        assert source.frame(OWNER, None)["members"][0]["state"] == "starting"


def test_capture_uses_private_progress_handler_and_preserves_canonical_handler(
    tmp_db, monkeypatch
):
    unindexed(tmp_db)
    unrelated_sessions(tmp_db)
    conn = tmp_db._get_conn()
    calls = []

    def canonical_progress():
        calls.append(True)
        return 0

    conn.set_progress_handler(canonical_progress, 1)
    try:
        baseline = reader.snapshot(
            tmp_db.db_path,
            (),
            budget=reader.ReadBudget(max_steps=1_000_000, seconds=60),
        ).steps
        before = len(calls)
        budget = reader.ReadBudget(max_steps=baseline + 1000, seconds=60)
        with pytest.raises(ObservationError):
            reader.snapshot(tmp_db.db_path, ("fixture-host/demo/unknown",), budget=budget)
        assert len(calls) == before
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 10_000
        assert len(calls) > before
    finally:
        conn.set_progress_handler(None, 0)


def test_private_native_value_limit_is_enforced_without_changing_canonical_limit(tmp_db):
    canonical = tmp_db._get_conn()
    if not hasattr(canonical, "getlimit"):
        pytest.skip("selected SQLite runtime does not expose native value limits")
    original_limit = canonical.getlimit(sqlite3.SQLITE_LIMIT_LENGTH)
    target = json.dumps({
        "caller_owner_ref": OWNER, "project": "demo", "worktree_id": "large-child",
        "private_payload": "x" * (reader.MAX_VALUE_BYTES * 2),
    })
    tmp_db.create_session(
        "large-child", "fixture", None, ".", "local", "idle", 1.0, target_json=target,
    )
    budget = reader.ReadBudget(max_steps=1_000_000, seconds=60)
    with pytest.raises(ObservationError) as failed:
        reader.snapshot(tmp_db.db_path, (OWNER,), budget=budget)
    assert failed.value.code == "source-unavailable"
    assert isinstance(failed.value.__cause__, sqlite3.DataError)
    assert not budget.exhausted
    assert canonical.getlimit(sqlite3.SQLITE_LIMIT_LENGTH) == original_limit
    assert tmp_db.get_session("large-child")["target_json"] == target


def test_private_deadline_exhaustion_is_a_typed_gap_without_wallclock_sleep(tmp_db, monkeypatch):
    budget = reader.ReadBudget(max_steps=1_000_000, seconds=0.05, started=0)
    monkeypatch.setattr(reader, "time", SimpleNamespace(monotonic=lambda: 1.0))
    with pytest.raises(ObservationError) as failed:
        reader.snapshot(tmp_db.db_path, (), budget=budget)
    assert failed.value.code == "capture-budget-exhausted" and budget.exhausted
    assert budget.steps < budget.max_steps


def test_commit_does_no_capture_and_budget_failure_cannot_fail_canonical_write(owner, monkeypatch):
    _manager, db, source = owner
    unrelated_sessions(db)
    budgets = limited_budget(db, monkeypatch)
    original = membership.reader.snapshot
    reads = []

    def observed(*args, **kwargs):
        assert not db._write_lock.locked(), "optional query in canonical write critical path"
        reads.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(membership.reader, "snapshot", observed)
    db.update_session_status("child", "idle", 2.0)
    assert reads == []
    assert db.get_session("child")["status"] == "idle"
    with pytest.raises(ObservationError) as failed:
        source.frame(OWNER, None)
    assert failed.value.code == "capture-budget-exhausted"
    assert reads == [True] and any(budget.exhausted for budget in budgets)


def test_large_unrelated_event_commit_and_usage_do_not_dirty_or_capture(owner, monkeypatch):
    _manager, db, source = owner
    db.create_session("event-parent", "fixture", None, ".", "local", "idle", 1.0)
    source.frame(OWNER, None)
    revision = db._membership_signal.revision

    def forbidden(*_args, **_kwargs):
        pytest.fail("unrelated event/usage commit caused an optional membership query")

    monkeypatch.setattr(membership.reader, "snapshot", forbidden)
    db._write_event_batch(
        [("event-parent", index + 1, "agent_message", "{}", 1.0) for index in range(10_000)]
    )
    db.update_session_usage("event-parent", context_used=35, context_size=100, now=2.0)
    db.delete_events("event-parent")
    assert db._membership_signal.revision == revision
    assert source.frame(OWNER, None)["members"][0]["state"] == "starting"


@pytest.mark.asyncio
async def test_actual_registration_heartbeats_do_not_dirty_scan_or_wake(owner, monkeypatch):
    from test_owner_membership import register

    _manager, db, source = owner
    assert register(db) == "live"
    before = source.frame(OWNER, None)["position"]
    revision = db._membership_signal.revision
    original = reader.snapshot
    reads = []

    def counted(*args, **kwargs):
        reads.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(reader, "snapshot", counted)
    waiting = asyncio.create_task(source.wait(OWNER, before, timeout=60))
    try:
        for _ in range(50):
            if db._membership_signal._waiters:
                break
            await asyncio.sleep(0)
        assert db._membership_signal._waiters
        assert register(db, now=32.0) == "live"
        assert db.register_live_session(
            "represented", machine=None, cwd=None, worktree_id=None, repo=None,
            branch=None, pid=None, role=None, now=62.0,
        ) == "live"
        assert db.get_live_session("represented")["updated_at"] == 62.0
        await asyncio.sleep(0)
        assert db._membership_signal.revision == revision
        assert not db._membership_signal._scheduled and not waiting.done()
        assert source.frame(OWNER, before)["changes"] == [] and reads == []
        assert db.register_live_session(
            "represented", machine=None, cwd=None, worktree_id="updated-child",
            repo=None, branch=None, pid=123, role=None, now=63.0,
            process_started_at=1.0,
        ) == "live"
        assert db._membership_signal.revision == revision + 1
        await asyncio.wait_for(waiting, 1)
        changed = source.frame(OWNER, before)["changes"]
        assert reads == [True]
        assert changed[-1]["member"]["worktree_id"] == "updated-child"
    finally:
        if not waiting.done():
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
    assert not db._membership_signal._waiters and not source._scopes[OWNER].waiters


def test_registration_reservation_inheritance_signals_membership_changes(owner):
    from test_owner_membership import register

    _manager, db, source = owner
    assert register(db) == "live"
    before = source.frame(OWNER, None)["position"]
    revision = db._membership_signal.revision
    assert db.create_cli_mode_reservation(
        "interactive-child", now=3.0,
        venue=json.dumps({"supervisor_ref": "fixture-host/demo/new-supervisor"}),
    )
    assert db._membership_signal.revision == revision
    assert register(db, now=4.0) == "live"
    assert db._membership_signal.revision == revision + 1
    changes = source.frame(OWNER, before)["changes"]
    assert changes[-1]["kind"] == "registration-disappeared"
    assert changes[-1]["operation"] == "tombstone"
    assert changes[-1]["grants_claim_release"] is False


def test_multiple_readers_share_one_coalesced_capture(owner, monkeypatch):
    _manager, db, source = owner
    original = reader.snapshot
    calls = []

    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(reader, "snapshot", counted)
    db.update_session_status("child", "running", 2.0)
    db.update_session_status("child", "idle", 3.0)
    first = source.frame(OWNER, None)
    second = source.frame(OWNER, None)
    assert first["members"] == second["members"]
    assert calls == [True]
    assert "coalesced-observed-changes" in first["limitations"]


@pytest.mark.parametrize("operation", ["commit", "context", "script"])
def test_actual_sqlite_transaction_commit_paths_notify_after_commit(owner, operation):
    _manager, db, source = owner
    before = source.frame(OWNER, None)["position"]
    with db._write_lock:
        conn = db._get_conn()
        assert isinstance(conn, MembershipConnection)
        if operation == "context":
            with conn:
                conn.execute("UPDATE sessions SET status='idle' WHERE id='child'")
        elif operation == "script":
            conn.executescript(
                "BEGIN IMMEDIATE; UPDATE sessions SET status='idle' WHERE id='child'; COMMIT;"
            )
        else:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE sessions SET status='idle' WHERE id='child'")
            conn.commit()
    changes = source.frame(OWNER, before)["changes"]
    assert len(changes) == 1 and changes[0]["member"]["state"] == "idle"


def test_context_rollback_preserves_canonical_error_and_does_not_notify(owner):
    _manager, db, source = owner
    before = source.frame(OWNER, None)["position"]
    revision = db._membership_signal.revision
    with db._write_lock:
        conn = db._get_conn()
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            with conn:
                conn.execute("UPDATE sessions SET status='idle' WHERE id='child'")
                conn.execute("INSERT INTO schema_version VALUES(40)")
                conn.execute(
                    "INSERT INTO sessions(id,name,target_type,status,created_at,updated_at) "
                    "VALUES('child','duplicate','local','idle',1,1)"
                )
    assert db.get_session("child")["status"] == "starting"
    assert db._membership_signal.revision == revision
    assert source.frame(OWNER, before)["changes"] == []


def test_script_partial_commit_keeps_original_error_and_observes_committed_facts(owner):
    _manager, db, source = owner
    before = source.frame(OWNER, None)["position"]
    with db._write_lock:
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            db._get_conn().executescript(
                "UPDATE sessions SET status='idle' WHERE id='child'; "
                "INSERT INTO nonexistent_membership_fixture VALUES(1);"
            )
    assert db.get_session("child")["status"] == "idle"
    assert source.frame(OWNER, before)["changes"][0]["member"]["state"] == "idle"


def test_script_uncommitted_work_is_not_published_before_rollback(owner):
    _manager, db, source = owner
    before = source.frame(OWNER, None)["position"]
    revision = db._membership_signal.revision
    with db._write_lock:
        conn = db._get_conn()
        conn.executescript("BEGIN IMMEDIATE; UPDATE sessions SET status='idle' WHERE id='child';")
        conn.rollback()
    assert db._membership_signal.revision == revision
    assert db.get_session("child")["status"] == "starting"
    assert source.frame(OWNER, before)["changes"] == []


def test_database_authority_replacement_cannot_reuse_old_positions(owner):
    _manager, db, source = owner
    before = source.frame(OWNER, None)["position"]
    db.execute_write(
        "UPDATE remote_acp_relationship_authority SET id=? WHERE singleton=1",
        ("replacement-authority",),
        membership=True,
    )
    with pytest.raises(ObservationError) as failed:
        source.frame(OWNER, before)
    assert failed.value.code == "source-changed" and source.closed


def test_actual_registry_reaper_marks_expiry_and_purge_not_claim_retirement(owner):
    from test_owner_membership import register

    _manager, db, source = owner
    register(db)
    before = source.frame(OWNER, None)["position"]
    db.reap_stale_live_sessions(now=1000, purge_seconds=10000, pid_alive=lambda _pid: False)
    expired = source.frame(OWNER, before)
    assert expired["changes"][-1]["kind"] == "registration-expired"
    db.reap_stale_live_sessions(now=20000, purge_seconds=1, pid_alive=lambda _pid: False)
    purged = source.frame(OWNER, expired["position"])["changes"][-1]
    assert purged["kind"] == "registration-disappeared"
    assert purged["grants_claim_release"] is False


@pytest.mark.parametrize(
    ("filename", "methods"),
    [
        (
            "db_sessions.py",
            {
                "create_session",
                "update_session_status",
                "update_session_acp_id",
                "update_session_target",
            },
        ),
        (
            "db_relationships.py",
            {
                "save_relationship_claim",
                "enter_relationship_attempt",
                "save_relationship_absence",
                "save_relationship_native_id",
                "retire_relationship",
            },
        ),
        ("db_live_sessions.py", {"expire_live_sessions_for_worktree", "reap_stale_live_sessions"}),
        ("remote_relationships.py", {"_reconcile_native_id", "_end_locked"}),
    ],
)
def test_audited_membership_producers_explicitly_mark_projected_changes(filename, methods):
    module = ast.parse(Path(membership.__file__).with_name(filename).read_text())
    found = {
        node.name
        for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(child, ast.keyword)
            and child.arg == "membership"
            and isinstance(child.value, ast.Constant)
            and child.value.value is True
            for child in ast.walk(node)
        )
    }
    assert methods <= found


def test_commit_observer_has_no_sql_or_source_capture_calls():
    module = ast.parse(
        Path(membership.__file__).with_name("owner_membership_signal.py").read_text()
    )
    cls = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "MembershipConnection"
    )
    calls = {
        node.func.attr
        for node in ast.walk(cls)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not calls & {"execute", "records", "snapshot", "refresh", "after_commit"}
    assert reader.MAX_VM_STEPS == 100_000 and reader.MAX_READ_SECONDS == 0.05
