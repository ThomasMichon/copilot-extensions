"""Positive local preparation failures retain the original keyed allocation."""

import asyncio

import pytest

from agent_bridge import local_launch_preparation, workspace_creation
from agent_bridge.acquisition_contract import AcquisitionRefused
from agent_bridge.models import SessionStatus
from agent_bridge.session_host_connection import _SessionHostConnectionMixin
from agent_bridge.session_manager import Session, SessionManager
from test_workspace_creation import creation_rig as _creation_rig, rig as _rig

creation_rig = _creation_rig
rig = _rig

pytestmark = pytest.mark.contract("agent_bridge.workspace_creation")


@pytest.mark.asyncio
async def test_preparation_refusal_retries_same_reserved_identity(creation_rig, monkeypatch):
    rig = creation_rig
    resolve = local_launch_preparation.resolve_local_launch
    calls = []

    async def refused(target, **kwargs):
        calls.append((target, kwargs))
        assert not rig.manager.list_sessions()
        assert not rig.db.execute_read("SELECT * FROM sessions")
        assert rig.db.execute_read("SELECT state FROM workspace_embodiments")[0]["state"] == "preparing"
        raise ValueError("Installed marketplace catalog is ambiguous")

    monkeypatch.setattr(local_launch_preparation, "resolve_local_launch", refused)
    with pytest.raises(AcquisitionRefused, match="catalog is ambiguous") as failure:
        await workspace_creation.start(rig.manager, rig.target, key="logical-work")
    assert failure.value.code == "creation_preparation_failed"
    assert failure.value.effect == "not_started"
    saved = dict(rig.db.execute_read("SELECT * FROM workspace_embodiments")[0])
    assert saved["state"] == "allocated"
    assert not rig.db.execute_read("SELECT * FROM acquisition_launch_gates")
    assert not rig.db.execute_read("SELECT * FROM sessions")
    monkeypatch.setattr(local_launch_preparation, "resolve_local_launch", resolve)
    session, receipt = await workspace_creation.start(rig.manager, rig.target, key="logical-work")
    assert session.session_id == saved["session_id"] == calls[0][1]["session_id"]
    assert receipt["worktree"]["id"] == rig.row["id"]
    assert sum(args[0] == "create" for args, _ in rig.calls) == 1
    assert len(rig.db.execute_read("SELECT * FROM sessions")) == 1
    assert not rig.db.execute_read("SELECT * FROM turns")


@pytest.mark.asyncio
async def test_holder_change_during_preparation_refuses_before_registration(creation_rig, monkeypatch):
    rig = creation_rig

    async def changed(target, **_kwargs):
        rig.head["id"] = "foreign-native"
        return ["copilot", "--acp"], target.cwd, {}

    monkeypatch.setattr(local_launch_preparation, "resolve_local_launch", changed)
    with pytest.raises(AcquisitionRefused) as failure:
        await workspace_creation.start(rig.manager, rig.target, key="logical-work")
    assert failure.value.code == "live_holder"
    assert not rig.manager.list_sessions()
    assert not rig.db.execute_read("SELECT * FROM sessions")
    assert not rig.db.execute_read("SELECT * FROM acquisition_launch_gates")
    assert rig.db.execute_read("SELECT state FROM workspace_embodiments")[0]["state"] == "allocated"


@pytest.mark.asyncio
async def test_cancelled_preparation_stays_pending_without_retry(creation_rig, monkeypatch):
    rig = creation_rig
    calls = []

    async def interrupted(*_args, **_kwargs):
        calls.append(True)
        raise asyncio.CancelledError()

    monkeypatch.setattr(local_launch_preparation, "resolve_local_launch", interrupted)
    with pytest.raises(asyncio.CancelledError):
        await workspace_creation.start(rig.manager, rig.target, key="logical-work")
    with pytest.raises(AcquisitionRefused, match="reconciliation"):
        await workspace_creation.start(rig.manager, rig.target, key="logical-work")
    assert calls == [True]
    assert rig.db.execute_read("SELECT state FROM workspace_embodiments")[0]["state"] == "preparing"
    assert len(rig.db.execute_read("SELECT * FROM acquisition_launch_gates")) == 1
    assert not rig.db.execute_read("SELECT * FROM sessions")


@pytest.mark.asyncio
async def test_prepared_plan_reaches_host_once_and_spawn_failure_stays_pending(creation_rig, monkeypatch):
    rig = creation_rig
    calls = []
    spawns = []

    async def resolve(target, **kwargs):
        calls.append(kwargs["session_id"])
        return ["copilot", "--acp", "--stdio"], target.cwd, {"PREPARED": "yes"}

    class Spawner:
        boundary = "local"

        async def spawn(self, args, *, cwd, env, session_id):
            spawns.append((
                list(args), cwd, dict(env), session_id,
                rig.db.get_session(session_id) is not None,
                rig.db.execute_read("SELECT state FROM workspace_embodiments")[0]["state"],
            ))
            raise RuntimeError("spawn outcome unknown")

    monkeypatch.setattr(local_launch_preparation, "resolve_local_launch", resolve)
    monkeypatch.setattr(
        SessionManager, "_connect_via_session_host",
        _SessionHostConnectionMixin._connect_via_session_host,
    )
    monkeypatch.setattr("agent_bridge.session_host.spawner.LocalSpawner", lambda **_kwargs: Spawner())
    monkeypatch.setattr(
        "agent_bridge.transport.resolve_local_launch",
        lambda *_args, **_kwargs: pytest.fail("must consume the prepared plan"),
    )
    with pytest.raises(AcquisitionRefused, match="reconciliation"):
        await workspace_creation.start(rig.manager, rig.target, key="logical-work")
    with pytest.raises(AcquisitionRefused, match="reconciliation"):
        await workspace_creation.start(rig.manager, rig.target, key="logical-work")
    assert len(calls) == 1
    assert len(spawns) == 1
    args, cwd, env, session_id, registered, state = spawns[0]
    assert args[:3] == ["copilot", "--acp", "--stdio"]
    assert cwd == rig.row["path"] and env["PREPARED"] == "yes"
    assert session_id == calls[0] and registered and state == "launching"
    assert len(rig.db.execute_read("SELECT * FROM sessions")) == 1
    assert rig.db.execute_read("SELECT state FROM workspace_embodiments")[0]["state"] == "launching"


@pytest.mark.asyncio
async def test_prepared_plan_rejects_changed_session_or_target(creation_rig):
    rig = creation_rig
    plan = await local_launch_preparation.prepare(rig.target, "session-a")
    with pytest.raises(ValueError, match="identity changed"):
        plan.resolve(rig.target, "session-b")
    rig.target.cwd = "/different-checkout"
    with pytest.raises(ValueError, match="identity changed"):
        plan.resolve(rig.target, "session-a")


@pytest.mark.asyncio
async def test_positive_preparation_refusal_survives_manager_reconstruction(creation_rig, monkeypatch):
    rig = creation_rig
    resolve = local_launch_preparation.resolve_local_launch

    async def refused(*_args, **_kwargs):
        raise OSError("plugin prerequisite unavailable")

    monkeypatch.setattr(local_launch_preparation, "resolve_local_launch", refused)
    with pytest.raises(AcquisitionRefused):
        await workspace_creation.start(rig.manager, rig.target, key="logical-work")
    saved = dict(rig.db.execute_read("SELECT * FROM workspace_embodiments")[0])
    manager = SessionManager(rig.db)
    monkeypatch.setattr(local_launch_preparation, "resolve_local_launch", resolve)
    session, receipt = await workspace_creation.start(manager, rig.target, key="logical-work")
    assert session.session_id == saved["session_id"]
    assert receipt["worktree"]["id"] == rig.row["id"]
    assert len(rig.db.execute_read("SELECT * FROM sessions")) == 1
    assert sum(args[0] == "create" for args, _ in rig.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("persist_failed_session", [False, True])
async def test_historical_launch_error_is_not_preparation_proof(
    creation_rig, monkeypatch, persist_failed_session,
):
    rig = creation_rig

    async def unknown(*_args, **_kwargs):
        raise RuntimeError("Installed marketplace catalog is ambiguous")

    monkeypatch.setattr(rig.manager, "start_session", unknown)
    with pytest.raises(RuntimeError, match="catalog is ambiguous"):
        await workspace_creation.start(rig.manager, rig.target, key="logical-work")
    saved = dict(rig.db.execute_read("SELECT * FROM workspace_embodiments")[0])
    rig.db.execute_write("UPDATE workspace_embodiments SET state='launching'")
    if persist_failed_session:
        session = Session(saved["session_id"], "original", rig.target, None)
        session.status = SessionStatus.FAILED
        rig.manager._sessions[session.session_id] = session
        rig.db.create_session(
            session.session_id, session.name, None, rig.row["path"], "local",
            "failed", 1, target_json=rig.target.to_json(),
        )
    monkeypatch.setattr(
        local_launch_preparation, "resolve_local_launch",
        lambda *_args, **_kwargs: pytest.fail("historical absence cannot authorize preflight"),
    )
    with pytest.raises(AcquisitionRefused, match="reconciliation"):
        await workspace_creation.start(rig.manager, rig.target, key="logical-work")
    assert rig.db.execute_read("SELECT state FROM workspace_embodiments")[0]["state"] == "launching"
    assert len(rig.db.execute_read("SELECT * FROM acquisition_launch_gates")) == 1
