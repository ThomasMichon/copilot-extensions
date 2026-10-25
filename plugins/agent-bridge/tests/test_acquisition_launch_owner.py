"""Explicit local effect owners and real SQLite; no installed cohort qualification."""

from __future__ import annotations

import os
import asyncio
from dataclasses import replace
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from zdd.diagnostics import process_start_time

from agent_bridge.acp_client import AcpClient
from agent_bridge.acquisition_journal import AcquisitionJournal
from agent_bridge.acquisition_launch_owner import AcquisitionFrontend
from agent_bridge.acquisition_runtime import AcquisitionRuntime
from agent_bridge.models import SessionStatus
from agent_bridge.runtime_custody import RuntimeCustodyError, RuntimeProducer
from agent_bridge.session_manager import Session
from test_acquisition_foundation import request
from test_runtime_custody import close, installation


@pytest.fixture
def local_launch(session_manager, spawn_target):
    db = session_manager.db
    identity = installation(db)
    epoch = db.initialize_runtime_epoch(identity)
    producer = RuntimeProducer("local-frontend", os.getpid(), process_start_time(os.getpid()))
    admission = db.enroll_runtime_producer(identity, epoch.epoch, producer)
    runtime = AcquisitionRuntime(session_manager, None, db)
    frontend = AcquisitionFrontend.capture(runtime, identity, admission)
    original = request(
        allocation="created", intent="fresh",
        prior_session_id=None, prior_native_session_id=None,
    )
    AcquisitionJournal(db).claim(original, runtime_admission=admission)
    owner = frontend.prepare(original, session_id="bridge-a", native_session_id=None)
    spawn_target.worktree_id = original.worktree_id
    spawn_target.explicit_cwd = True
    session = Session("bridge-a", "a", spawn_target)
    session.status = SessionStatus.STARTING
    db.create_session(
        "bridge-a", "a", None, spawn_target.cwd, "local", "starting", 1.0,
        runtime_admission=admission,
    )
    session_manager._sessions["bridge-a"] = session
    owner.bind(session_manager, session, spawn_target)
    return SimpleNamespace(
        db=db, runtime=runtime, frontend=frontend, owner=owner,
        identity=identity, producer=producer, admission=admission,
        session=session, target=spawn_target, original=original,
    )


def test_acquisition_launch_final_host_fence_observes_closure(local_launch):
    rig = local_launch
    close(rig.db, rig.identity, rig.producer)
    with pytest.raises(RuntimeCustodyError, match="open epoch"):
        rig.owner.begin_host()
    assert AcquisitionJournal(rig.db).read_launch(rig.original)["started_at"] is None


def test_acquisition_launch_final_host_is_once_and_keeps_unknown(local_launch):
    rig = local_launch
    rig.owner.begin_host()
    with pytest.raises(RuntimeCustodyError, match="held"):
        rig.owner.begin_host()
    assert AcquisitionJournal(rig.db).read_launch(rig.original)["stage"] == "starting"


@pytest.mark.asyncio
async def test_acquisition_launch_native_checks_inside_scheduled_rpc(local_launch):
    rig = local_launch
    rig.owner.begin_host()
    client = AcpClient()
    client._host_mode, client._host_transport_alive, client._host_child_pid = True, True, os.getpid()
    client._connection = SimpleNamespace(new_session=AsyncMock())
    host = SimpleNamespace(
        host_pid=os.getpid(), child_pid=os.getpid(),
        proc=SimpleNamespace(pid=os.getpid(), poll=lambda: None), boundary="local",
    )
    rig.owner.attach_transport(client, host)
    invoke = rig.owner.native_method(client, "new", client._connection.new_session)
    pending = invoke(cwd=rig.target.cwd, mcp_servers=[])
    close(rig.db, rig.identity, rig.producer)
    with pytest.raises(RuntimeCustodyError, match="open epoch"):
        await pending
    client._connection.new_session.assert_not_awaited()
    assert AcquisitionJournal(rig.db).read_launch(rig.original)["result"] is None


@pytest.mark.asyncio
async def test_acquisition_launch_native_acceptance_survives_closed_reply(local_launch):
    rig = local_launch
    rig.owner.begin_host()
    client = AcpClient()
    client._host_mode, client._host_transport_alive, client._host_child_pid = True, True, os.getpid()

    async def accepted(**_kwargs):
        close(rig.db, rig.identity, rig.producer)
        return SimpleNamespace(session_id="native-new")

    client._connection = SimpleNamespace(new_session=AsyncMock(side_effect=accepted))
    host = SimpleNamespace(
        host_pid=os.getpid(), child_pid=os.getpid(),
        proc=SimpleNamespace(pid=os.getpid(), poll=lambda: None), boundary="local",
    )
    rig.owner.attach_transport(client, host)
    invoke = rig.owner.native_method(client, "new", client._connection.new_session)
    assert (await invoke(cwd=rig.target.cwd, mcp_servers=[])).session_id == "native-new"
    report = AcquisitionJournal(rig.db).read_launch(rig.original)
    assert report["result"]["native_session_id"] == "native-new"
    with pytest.raises(RuntimeCustodyError):
        await invoke(cwd=rig.target.cwd, mcp_servers=[])
    client._connection.new_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_acquisition_launch_thread_scheduling_cannot_bypass_final_popen(local_launch, monkeypatch):
    from agent_bridge.session_host.spawner import LocalSpawner

    rig = local_launch
    original_thread = asyncio.to_thread
    popen = AsyncMock(side_effect=AssertionError("must not spawn"))
    monkeypatch.setattr("agent_bridge.session_host.launcher.subprocess.Popen", popen)

    async def after_scheduling(func, *args, **kwargs):
        close(rig.db, rig.identity, rig.producer)
        return await original_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", after_scheduling)
    with pytest.raises(RuntimeCustodyError, match="open epoch"):
        await LocalSpawner().spawn(
            [sys.executable, "-c", "pass"], cwd=rig.target.cwd,
            session_id=rig.session.session_id, acquisition_launch=rig.owner,
        )
    popen.assert_not_called()
    assert rig.runtime.store.journal.read_launch(rig.original)["started_at"] is None


def test_acquisition_launch_closure_after_original_commit_keeps_unknown(local_launch, monkeypatch):
    rig = local_launch
    begin = rig.runtime.store.journal.begin_launch

    def close_after_begin(*args, **kwargs):
        assert begin(*args, **kwargs)
        close(rig.db, rig.identity, rig.producer)
        return True

    monkeypatch.setattr(rig.runtime.store.journal, "begin_launch", close_after_begin)
    with pytest.raises(RuntimeCustodyError, match="open epoch"):
        rig.owner.begin_host()
    assert rig.runtime.store.journal.read_launch(rig.original)["stage"] == "starting"


@pytest.mark.asyncio
@pytest.mark.parametrize("closed", [False, True])
async def test_acquisition_launch_host_child_rechecks_and_strips_original_context(
    local_launch, monkeypatch, closed,
):
    from agent_bridge.acquisition_host_admission import CONTEXT_ENV
    from agent_bridge.session_host.launcher import _spawn_child

    rig = local_launch
    argv = [sys.executable, "-c", "pass"]
    raw = rig.owner.child_context(argv, rig.target.cwd)
    rig.owner.begin_host()
    monkeypatch.setattr(os, "getppid", lambda: rig.producer.pid)
    spawn = AsyncMock(return_value=SimpleNamespace(pid=22))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    if closed:
        close(rig.db, rig.identity, rig.producer)
        with pytest.raises(RuntimeCustodyError, match="open epoch"):
            await _spawn_child(argv, rig.target.cwd, {
                CONTEXT_ENV: raw, "AGENT_BRIDGE_SESSION_ID": rig.session.session_id,
            })
        spawn.assert_not_awaited()
    else:
        before = rig.db.runtime_epoch(rig.identity)
        await _spawn_child(argv, rig.target.cwd, {
            CONTEXT_ENV: raw, "AGENT_BRIDGE_SESSION_ID": rig.session.session_id,
        })
        spawn.assert_awaited_once()
        assert CONTEXT_ENV not in spawn.call_args.kwargs["env"]
        assert rig.db.runtime_epoch(rig.identity) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    "session", "target", "cwd", "native", "connection", "host-handle", "host-pid", "child-pid", "stage", "handoff", "draining",
])
async def test_acquisition_launch_native_retains_exact_live_references(local_launch, change):
    rig = local_launch
    rig.owner.begin_host()
    client = AcpClient()
    client._host_mode, client._host_transport_alive, client._host_child_pid = True, True, os.getpid()
    client._connection = SimpleNamespace(new_session=AsyncMock())
    original = client._connection
    host = SimpleNamespace(
        host_pid=os.getpid(), child_pid=os.getpid(),
        proc=SimpleNamespace(pid=os.getpid(), poll=lambda: None), boundary="local",
    )
    rig.owner.attach_transport(client, host)
    invoke = rig.owner.native_method(client, "new", original.new_session)
    if change == "session":
        rig.runtime.manager._sessions[rig.session.session_id] = Session("bridge-a", "other", rig.target)
    elif change == "target":
        rig.session.target = replace(rig.target)
    elif change == "cwd":
        rig.target.cwd += "/changed"
    elif change == "native":
        rig.session.acp_session_id = "other"
    elif change == "connection":
        client._connection = SimpleNamespace(new_session=AsyncMock())
    elif change == "host-handle":
        host.proc = SimpleNamespace(pid=os.getpid(), poll=lambda: None)
    elif change == "host-pid":
        host.host_pid += 1
    elif change == "child-pid":
        host.child_pid += 1
    elif change == "stage":
        rig.runtime.store.stage(rig.original, "delivering")
    elif change == "handoff":
        rig.session._handoff_in_progress = True
    else:
        rig.runtime.manager._draining = True
    with pytest.raises(RuntimeCustodyError):
        await invoke(cwd=rig.target.cwd, mcp_servers=[])
    original.new_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_acquisition_launch_invalid_params_cannot_repeat_actual_native_call(local_launch, monkeypatch):
    from acp.exceptions import RequestError

    rig = local_launch
    monkeypatch.setattr(
        "agent_bridge.acp_subagents.subagent_event_meta",
        lambda: {"_meta": {"github.com/copilot": {}}},
    )
    rig.owner.begin_host()
    client = AcpClient()
    client._host_mode, client._host_transport_alive, client._host_child_pid = True, True, os.getpid()
    client._connection = SimpleNamespace(new_session=AsyncMock(side_effect=RequestError(-32602, "Invalid params")))
    host = SimpleNamespace(
        host_pid=os.getpid(), child_pid=os.getpid(),
        proc=SimpleNamespace(pid=os.getpid(), poll=lambda: None), boundary="local",
    )
    rig.owner.attach_transport(client, host)
    with pytest.raises(RuntimeCustodyError, match="held"):
        await client.new_session(rig.target.cwd, acquisition_launch=rig.owner)
    client._connection.new_session.assert_awaited_once()
    assert rig.runtime.store.journal.read_launch(rig.original)["result"] is None


@pytest.mark.asyncio
async def test_acquisition_launch_configuration_rechecks_each_actual_effect(local_launch, monkeypatch):
    rig = local_launch
    monkeypatch.delenv("AGENT_BRIDGE_MODEL_PROPAGATE", raising=False)
    rig.owner.begin_host()
    client = AcpClient(model_override="selected", effort_override="high")
    client._host_mode, client._host_transport_alive, client._host_child_pid = True, True, os.getpid()
    options = [
        {"id": "model", "type": "select", "currentValue": "old",
         "options": [{"value": "old"}, {"value": "selected"}]},
        {"id": "reasoning_effort", "type": "select", "currentValue": "low",
         "options": [{"value": "low"}, {"value": "high"}]},
    ]

    async def close_after_first_config(**_kwargs):
        close(rig.db, rig.identity, rig.producer)
        return SimpleNamespace()

    client._connection = SimpleNamespace(
        new_session=AsyncMock(return_value=SimpleNamespace(session_id="native-new", config_options=options)),
        set_config_option=AsyncMock(side_effect=close_after_first_config),
    )
    host = SimpleNamespace(
        host_pid=os.getpid(), child_pid=os.getpid(),
        proc=SimpleNamespace(pid=os.getpid(), poll=lambda: None), boundary="local",
    )
    rig.owner.attach_transport(client, host)
    with pytest.raises(RuntimeCustodyError, match="open epoch"):
        await client.new_session(rig.target.cwd, acquisition_launch=rig.owner)
    client._connection.new_session.assert_awaited_once()
    client._connection.set_config_option.assert_awaited_once()
    assert rig.runtime.store.journal.read_launch(rig.original)["result"]["native_session_id"] == "native-new"


@pytest.mark.parametrize("changes", [
    {"type": "ssh"}, {"elevated": True}, {"spawn_command": ["host"]},
    {"codespace": {"name": "remote"}}, {"container": {"name": "remote"}},
    {"host": "remote"}, {"remote_acp": {"url": "https://example.invalid"}},
    {"venue": {"instance_id": "remote"}},
])
def test_acquisition_launch_unsupported_targets_never_begin(local_launch, changes):
    from agent_bridge.acquisition_launch_owner import require_local

    rig = local_launch
    with pytest.raises(RuntimeCustodyError, match="exact local"):
        require_local(replace(rig.target, **changes))
    assert rig.runtime.store.journal.read_launch(rig.original)["started_at"] is None


@pytest.mark.asyncio
async def test_acquisition_launch_pending_association_cannot_downgrade_to_ordinary(local_launch):
    rig = local_launch
    with pytest.raises(RuntimeCustodyError, match="actual original frontend"):
        await rig.runtime.acquire(rig.original)
    assert rig.runtime.store.journal.read_launch(rig.original)["started_at"] is None


@pytest.mark.asyncio
async def test_acquisition_launch_associated_session_cannot_auto_resume(local_launch):
    rig = local_launch
    rig.session.status = SessionStatus.STOPPED
    rig.db.update_session_status(rig.session.session_id, "stopped", 2.0)
    with pytest.raises(RuntimeCustodyError, match="actual original owner"):
        await rig.runtime.manager.resume_session(rig.session.session_id)
    assert rig.runtime.store.journal.read_launch(rig.original)["started_at"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("late_association", [False, True])
async def test_acquisition_launch_resync_rechecks_original_association_before_teardown(
    local_launch, monkeypatch, late_association,
):
    from agent_bridge import session_manager as core

    rig = local_launch
    rig.session.status = SessionStatus.IDLE
    rig.session.acp_session_id = "retained-native"
    client = SimpleNamespace(shutdown=AsyncMock())
    rig.session.client = client
    spawn = AsyncMock(side_effect=AssertionError("must not spawn"))
    monkeypatch.setattr(core, "spawn", spawn)
    if late_association:
        from agent_bridge import acquisition_launch_owner

        original_association = acquisition_launch_owner.session_association

        def observe_association_under_lock(manager, session):
            if not session._lifecycle_lock.locked():
                return None
            return original_association(manager, session)

        async def prepared(*_args, **_kwargs):
            return SimpleNamespace()

        monkeypatch.setattr(acquisition_launch_owner, "session_association", observe_association_under_lock)
        monkeypatch.setattr(
            "agent_bridge.hosting_policy.require_session_hosting", prepared,
        )
    with pytest.raises(RuntimeCustodyError, match="resync"):
        await rig.runtime.manager.resync_session(rig.session.session_id)
    client.shutdown.assert_not_awaited()
    spawn.assert_not_awaited()
    assert rig.session.client is client
    assert rig.session.status == SessionStatus.IDLE


def test_acquisition_launch_durable_association_keeps_recovery_excluded(local_launch):
    from agent_bridge.session_manager import SessionManager

    rig = local_launch
    sid = rig.session.session_id
    assert not rig.db.get_session(sid)["background_recovery_enabled"]
    assert not rig.db.update_session_background_recovery(sid, True)
    assert not rig.db.update_session_stopped(sid, 3.0, True)
    assert not rig.db.get_session(sid)["background_recovery_enabled"]
    rig.db.execute_write(
        "UPDATE sessions SET background_recovery_enabled=1 WHERE id=?", (sid,),
    )
    successor = SessionManager(rig.db)
    assert not successor.get_session(sid).background_recovery_enabled
