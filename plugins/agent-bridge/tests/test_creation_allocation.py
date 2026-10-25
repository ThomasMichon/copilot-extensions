"""Original allocation owner and subprocess; synthetic peer, not native qualification."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from zdd.diagnostics import process_start_time

from agent_bridge import acquisition_authority, workspace_creation
from agent_bridge.acquisition_contract import AcquisitionRefused
from agent_bridge.creation_allocation import CreationFrontend
from agent_bridge.creation_custody import CreationJournal, CreationRequest
from agent_bridge.creation_launch_owner import CreationLaunchOwner
from agent_bridge.db import Database
from agent_bridge.runtime_custody import RuntimeCustodyError, RuntimeProducer
from agent_bridge.transport import SpawnTarget
from test_runtime_custody import close, installation
from test_creation_custody import creation_request


@pytest.fixture
def allocation(session_manager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = session_manager.db
    identity = installation(db)
    epoch = db.initialize_runtime_epoch(identity)
    producer = RuntimeProducer("allocation-frontend", os.getpid(), process_start_time(os.getpid()))
    admission = db.enroll_runtime_producer(identity, epoch.epoch, producer)
    frontend = CreationFrontend.capture(session_manager, identity, admission)
    target = SpawnTarget(type="local", project="fixture", cwd=str(tmp_path))
    worktree = {
        "id": "original-worktree", "repo": "fixture", "path": str(tmp_path / "workspace"),
        "creation_nonce": "original-nonce",
    }
    calls = []

    async def command(args, project, *, before_spawn=None):
        assert args[0] == "create" and project == "fixture"
        assert before_spawn is not None
        before_spawn()
        calls.append(args)
        return {"creation": {"version": 1, "status": "complete"}, "worktree": worktree}

    monkeypatch.setattr(workspace_creation, "worktree_command", command)
    monkeypatch.setattr("agent_bridge.hosting_policy.require_target_hosting", AsyncMock())
    monkeypatch.setattr(session_manager, "start_session", AsyncMock(side_effect=AssertionError("no native launch")))
    request = CreationRequest.capture(
        "workspace", hashlib.sha256(json.dumps(["fixture", "original-key"]).encode()).hexdigest(),
        {"target": json.loads(target.to_json()), "options": {}, "prompt": None},
    )
    return SimpleNamespace(
        db=db, identity=identity, producer=producer, admission=admission,
        frontend=frontend, manager=session_manager, target=target, worktree=worktree,
        request=request, calls=calls, command=command,
    )


@pytest.mark.parametrize("state", ["fresh", "claimed", "prepared", "outstanding", "accepted"])
@pytest.mark.parametrize("closed", [False, True])
def test_local_creation_frontend_refuses_provider_without_changing_custody(
    allocation, state: str, closed: bool,
) -> None:
    rig = allocation
    request = creation_request("provider")
    journal = CreationJournal(rig.db)
    if state != "fresh":
        journal.claim(request, rig.identity, rig.admission)
    handoff = None
    if state in ("prepared", "outstanding", "accepted"):
        handoff = journal.prepare_launch(
            request, rig.identity, rig.admission, consumer_incarnation="original-provider",
        )
    if state in ("outstanding", "accepted"):
        journal.begin_launch(request, rig.identity, rig.admission, handoff["id"], now=10)
    if state == "accepted":
        journal.report_launch(request, handoff["id"], native_session_id="provider-native", now=11)
    if closed:
        close(rig.db, rig.identity, rig.producer)
    rows = rig.db.execute_read("SELECT * FROM provider_embodiments")
    before = [dict(row) for row in rows]
    epoch = rig.db.runtime_epoch(rig.identity)
    with pytest.raises(RuntimeCustodyError, match="original workspaces only"):
        rig.frontend.prepare(request)
    with pytest.raises(RuntimeCustodyError, match="original workspaces only"):
        CreationLaunchOwner(
            rig.frontend, request, "not-a-local-session",
            handoff["id"] if handoff else "not-a-local-handoff",
        )
    assert [dict(row) for row in rig.db.execute_read("SELECT * FROM provider_embodiments")] == before
    assert rig.db.runtime_epoch(rig.identity) == epoch
    if handoff:
        assert journal.read_launch(request)["handoff"] == handoff
    for table in ("workspace_embodiments", "acquisition_attempts", "sessions", "turns"):
        assert not rig.db.execute_read(f"SELECT * FROM {table}")
    assert not rig.calls
    rig.manager.start_session.assert_not_awaited()


@pytest.mark.parametrize("original", [None, object()])
def test_local_creation_frontend_refuses_non_request_before_journal(allocation, original) -> None:
    rig = allocation
    before = rig.db.runtime_epoch(rig.identity)
    with pytest.raises(RuntimeCustodyError, match="original workspaces only"):
        rig.frontend.prepare(original)
    assert rig.db.runtime_epoch(rig.identity) == before
    assert not rig.db.execute_read("SELECT * FROM workspace_embodiments")
    assert not rig.db.execute_read("SELECT * FROM provider_embodiments")


@pytest.mark.asyncio
async def test_original_allocation_replay_never_starts_native_or_allocates_again(allocation) -> None:
    rig = allocation
    row = await rig.frontend.allocate(rig.target, key="original-key")
    assert row["state"] == "allocated"
    assert json.loads(row["worktree_json"]) == rig.worktree
    assert row["runtime_custody_json"] == rig.admission.document()
    assert row["native_session_id"] is row["turn_index"] is None
    assert await rig.frontend.allocate(rig.target, key="original-key") == row
    assert len(rig.calls) == 1
    with pytest.raises(AcquisitionRefused) as refused:
        await workspace_creation.start(rig.manager, rig.target, key="original-key")
    assert refused.value.code == "creation_custody_required"
    rig.manager.start_session.assert_not_awaited()
    assert not rig.db.execute_read("SELECT * FROM sessions")
    assert not rig.db.execute_read("SELECT * FROM turns")
    assert not rig.db.execute_read("SELECT * FROM acquisition_launch_gates")


@pytest.mark.asyncio
@pytest.mark.parametrize("draining", [False, True])
async def test_allocation_closed_epoch_replay_is_observation_only(
    allocation, monkeypatch, draining: bool,
) -> None:
    rig = allocation
    row = await rig.frontend.allocate(rig.target, key="original-key")
    close(rig.db, rig.identity, rig.producer)
    before = rig.db.runtime_epoch(rig.identity)
    if draining:
        monkeypatch.setattr(type(rig.manager), "is_draining", property(lambda _self: True))
    assert await rig.frontend.allocate(rig.target, key="original-key") == row
    assert rig.db.runtime_epoch(rig.identity) == before
    assert len(rig.calls) == 1
    with pytest.raises(RuntimeCustodyError, match="open epoch"):
        await rig.frontend.allocate(rig.target, key="new-key")
    assert len(rig.db.execute_read("SELECT * FROM workspace_embodiments")) == 1


@pytest.mark.asyncio
async def test_allocation_closure_during_preparation_prevents_actual_spawn(allocation, monkeypatch) -> None:
    rig = allocation

    async def policy(*_args, **_kwargs):
        close(rig.db, rig.identity, rig.producer)

    monkeypatch.setattr("agent_bridge.hosting_policy.require_target_hosting", policy)
    with pytest.raises(RuntimeCustodyError, match="open epoch"):
        await rig.frontend.allocate(rig.target, key="original-key")
    assert not rig.calls
    assert CreationJournal(rig.db).read(rig.request)["state"] == "allocating"


@pytest.mark.asyncio
async def test_allocation_closure_after_final_commit_preserves_unknown_without_spawn(
    allocation, monkeypatch,
) -> None:
    rig = allocation
    begin = CreationJournal.begin_allocation

    def closed(journal, *args, **kwargs):
        begin(journal, *args, **kwargs)
        close(rig.db, rig.identity, rig.producer)

    monkeypatch.setattr(CreationJournal, "begin_allocation", closed)
    with pytest.raises(RuntimeCustodyError, match="open epoch"):
        await rig.frontend.allocate(rig.target, key="original-key")
    assert not rig.calls
    assert CreationJournal(rig.db).read(rig.request)["state"] == "allocation_started"


@pytest.mark.asyncio
async def test_allocation_uses_existing_executor_across_owner_await(allocation, monkeypatch) -> None:
    rig = allocation
    entered, release = asyncio.Event(), asyncio.Event()

    async def waiting(args, project, **kwargs):
        entered.set()
        await release.wait()
        return await rig.command(args, project, **kwargs)

    monkeypatch.setattr(workspace_creation, "worktree_command", waiting)
    first = asyncio.create_task(rig.frontend.allocate(rig.target, key="original-key"))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(AcquisitionRefused) as refused:
            await rig.frontend.allocate(rig.target, key="original-key")
        assert refused.value.code == "executor_live"
    finally:
        release.set()
        row = await asyncio.wait_for(first, 5)
    assert row["state"] == "allocated"
    assert len(rig.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["lost", "closed", "malformed", "wrong-workspace", "cancelled"])
async def test_allocation_unknown_or_closed_reply_keeps_original_attempt(
    allocation, monkeypatch, outcome: str,
) -> None:
    rig = allocation

    async def reply(args, project, **kwargs):
        response = await rig.command(args, project, **kwargs)
        if outcome == "lost":
            raise ConnectionError("lost original reply")
        if outcome == "cancelled":
            raise asyncio.CancelledError()
        if outcome == "closed":
            close(rig.db, rig.identity, rig.producer)
        if outcome == "malformed":
            return {"creation": {"version": 1, "status": "pending"}}
        if outcome == "wrong-workspace":
            response["worktree"] = {**rig.worktree, "repo": "foreign"}
        return response

    monkeypatch.setattr(workspace_creation, "worktree_command", reply)
    if outcome == "closed":
        row = await rig.frontend.allocate(rig.target, key="original-key")
        assert row["state"] == "allocated"
        assert CreationJournal(rig.db).read(rig.request) == row
        assert await rig.frontend.allocate(rig.target, key="original-key") == row
    else:
        with pytest.raises((ConnectionError, AcquisitionRefused, RuntimeCustodyError, asyncio.CancelledError)):
            await rig.frontend.allocate(rig.target, key="original-key")
        row = CreationJournal(rig.db).read(rig.request)
        assert row["state"] == "allocation_started" and row["worktree_json"] is None
        with pytest.raises(AcquisitionRefused) as refused:
            await rig.frontend.allocate(rig.target, key="original-key")
        assert refused.value.code == "creation_allocation_pending"
    assert len(rig.calls) == 1
    reopened = Database(rig.db.db_path, read_only=True)
    try:
        assert CreationJournal(reopened).read(rig.request) == row
    finally:
        reopened.close()
    report = rig.db.runtime_custody_inventory(rig.identity)
    assert report["rows"]["workspace_embodiments"][0]["session_id"] == row["session_id"]
    assert report["restore_qualified"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["manager-db", "draining", "pid", "birth", "producer-document", "file"])
async def test_allocation_final_spawn_checks_actual_frontend(allocation, monkeypatch, change: str) -> None:
    rig = allocation

    async def changed(args, project, **kwargs):
        if change == "manager-db":
            monkeypatch.setattr(type(rig.manager), "db", property(lambda _self: object()))
        elif change == "draining":
            monkeypatch.setattr(type(rig.manager), "is_draining", property(lambda _self: True))
        elif change == "pid":
            monkeypatch.setattr("agent_bridge.frontend_process.os.getpid", lambda: rig.producer.pid + 1)
        elif change == "birth":
            monkeypatch.setattr("agent_bridge.frontend_process.process_start_time", lambda _pid: "reused")
        elif change == "producer-document":
            monkeypatch.setattr(RuntimeProducer, "document", lambda _self: "{}")
        else:
            object.__setattr__(rig.frontend, "database_identity", (0, 0))
        return await rig.command(args, project, **kwargs)

    monkeypatch.setattr(workspace_creation, "worktree_command", changed)
    with pytest.raises(RuntimeCustodyError):
        await rig.frontend.allocate(rig.target, key="original-key")
    assert not rig.calls
    assert CreationJournal(rig.db).read(rig.request)["state"] == "allocating"


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"type": "ssh"}, {"host": "remote"}, {"elevated": True},
    {"worktree_id": "existing"}, {"explicit_cwd": True}, {"container": "container-a"},
])
async def test_allocation_rejects_unsupported_target_before_attempt(allocation, changes: dict) -> None:
    rig = allocation
    with pytest.raises(RuntimeCustodyError):
        await rig.frontend.allocate(replace(rig.target, **changes), key="original-key")
    assert not rig.calls
    assert not rig.db.execute_read("SELECT * FROM workspace_embodiments")


@pytest.mark.asyncio
async def test_allocation_preserves_exact_options_and_cannot_rewrite_result(allocation) -> None:
    rig = allocation
    row = await rig.frontend.allocate(
        rig.target, key="original-key", caller_owner_ref="machine/project/worktree#original",
    )
    assert rig.calls[0][-2:] == ["--owner-ref", "machine/project/worktree#original"]
    with pytest.raises(RuntimeCustodyError):
        await rig.frontend.allocate(
            rig.target, key="original-key", caller_owner_ref="machine/project/worktree#replacement",
        )
    request = CreationRequest("workspace", rig.request.key_digest, row["creation_facts_json"])
    close(rig.db, rig.identity, rig.producer)
    journal = CreationJournal(rig.db)
    assert journal.report_allocation(request, rig.worktree) == row
    with pytest.raises(RuntimeCustodyError, match="replaced"):
        journal.report_allocation(request, {**rig.worktree, "id": "replacement"})
    assert journal.read(request) == row
    assert len(rig.calls) == 1


@pytest.mark.asyncio
async def test_original_allocation_reaches_actual_subprocess_once(allocation, monkeypatch) -> None:
    rig = allocation
    script = "import json,sys; print(json.dumps(json.loads(sys.argv[1])))"
    result = json.dumps({"creation": {"version": 1, "status": "complete"}, "worktree": rig.worktree})
    monkeypatch.setattr(
        acquisition_authority, "_agent_worktrees_launch_prefix",
        lambda: [sys.executable, "-c", script, result],
    )
    monkeypatch.setattr(acquisition_authority, "_agent_worktrees_environment", lambda: dict(os.environ))
    monkeypatch.setattr(workspace_creation, "worktree_command", acquisition_authority.worktree_command)
    actual_spawn = asyncio.create_subprocess_exec
    rows_at_spawn = []

    async def spawn(*args, **kwargs):
        rows_at_spawn.append(CreationJournal(rig.db).read(rig.request))
        assert rig.db._get_conn().in_transaction is False
        return await actual_spawn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    row = await rig.frontend.allocate(rig.target, key="original-key")
    assert await rig.frontend.allocate(rig.target, key="original-key") == row
    assert len(rows_at_spawn) == 1 and rows_at_spawn[0]["state"] == "allocation_started"
    assert rows_at_spawn[0]["session_id"] == row["session_id"]
    assert row["state"] == "allocated"


@pytest.mark.asyncio
async def test_actual_allocation_resolver_closure_blocks_before_subprocess(allocation, monkeypatch) -> None:
    rig = allocation

    def resolve():
        close(rig.db, rig.identity, rig.producer)
        return [sys.executable, "-c", "raise AssertionError('must not run')"]

    monkeypatch.setattr(acquisition_authority, "_agent_worktrees_launch_prefix", resolve)
    monkeypatch.setattr(acquisition_authority, "_agent_worktrees_environment", lambda: {})
    monkeypatch.setattr(workspace_creation, "worktree_command", acquisition_authority.worktree_command)
    spawn = AsyncMock(side_effect=AssertionError("must not spawn"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(RuntimeCustodyError, match="open epoch"):
        await rig.frontend.allocate(rig.target, key="original-key")
    spawn.assert_not_awaited()
    assert CreationJournal(rig.db).read(rig.request)["state"] == "allocating"


@pytest.mark.asyncio
async def test_allocation_journal_transitions_are_full_durable_and_restore_policy(allocation) -> None:
    rig = allocation
    journal = CreationJournal(rig.db)
    journal.claim(rig.request, rig.identity, rig.admission)
    conn = rig.db._get_conn()
    observed = []

    def record(sql: str) -> None:
        if sql.startswith("UPDATE workspace_embodiments SET"):
            observed.append(conn.execute("PRAGMA synchronous").fetchone()[0])

    conn.set_trace_callback(record)
    try:
        journal.begin_allocation(rig.request, rig.identity, rig.admission)
        journal.report_allocation(rig.request, rig.worktree)
    finally:
        conn.set_trace_callback(None)
    assert observed and set(observed) == {2}
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1
