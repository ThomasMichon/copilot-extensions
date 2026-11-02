"""Original creation's concrete Host/ACP adapter, without installed enrollment."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from agent_bridge.acp_client import AcpClient
from agent_bridge.creation_custody import CreationJournal
from agent_bridge.models import SessionStatus
from agent_bridge.runtime_custody import RuntimeCustodyError
from agent_bridge.session_manager import Session
from test_creation_allocation import allocation as allocation
from test_runtime_custody import close


@pytest_asyncio.fixture
async def creation_launch(allocation):
    rig = allocation
    row = await rig.frontend.allocate(rig.target, key="original-key")
    owner = rig.frontend.prepare(rig.request)
    from dataclasses import replace

    target = replace(
        rig.target, worktree_id=rig.worktree["id"], cwd=rig.worktree["path"], explicit_cwd=True,
    )
    Path(target.cwd).mkdir()
    session = Session(row["session_id"], "original-creation", target)
    session.status = SessionStatus.STARTING
    rig.db.create_session(
        session.session_id, session.name, None, target.cwd, "local", "starting", 1.0,
        runtime_admission=rig.admission, target_json=target.to_json(),
    )
    rig.manager._sessions[session.session_id] = session
    owner.bind(rig.manager, session, target)
    rig.owner, rig.session, rig.launch_target = owner, session, target
    return rig


def attach(rig, method):
    client = AcpClient()
    client._host_mode, client._host_transport_alive, client._host_child_pid = True, True, os.getpid()
    client._connection = SimpleNamespace(new_session=method)
    host = SimpleNamespace(
        host_pid=os.getpid(), child_pid=os.getpid(),
        proc=SimpleNamespace(pid=os.getpid(), poll=lambda: None), boundary="local",
    )
    from test_acquisition_launch_owner import retain_synthetic_transport

    retain_synthetic_transport(rig.owner, client, host)
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["scheduled", "committed", "open"])
async def test_creation_launch_actual_popen_is_once_and_finally_admitted(creation_launch, monkeypatch, when):
    from agent_bridge.session_host.spawner import LocalSpawner

    rig = creation_launch
    popen = AsyncMock(side_effect=AssertionError("test stops before actual spawn"))
    monkeypatch.setattr("agent_bridge.session_host.launcher.subprocess.Popen", popen)
    if when == "scheduled":
        original = asyncio.to_thread

        async def closed(func, *args, **kwargs):
            close(rig.db, rig.identity, rig.producer)
            return await original(func, *args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", closed)
    elif when == "committed":
        original = CreationJournal.begin_launch

        def closed(journal, *args, **kwargs):
            result = original(journal, *args, **kwargs)
            close(rig.db, rig.identity, rig.producer)
            return result

        monkeypatch.setattr(CreationJournal, "begin_launch", closed)
    if when != "open":
        with pytest.raises(RuntimeCustodyError, match="open epoch"):
            await LocalSpawner().spawn(
                [sys.executable, "-c", "pass"], cwd=rig.launch_target.cwd,
                session_id=rig.session.session_id, acquisition_launch=rig.owner,
            )
        popen.assert_not_called()
    else:
        rig.owner.begin_host()
        with pytest.raises(RuntimeCustodyError, match="held"):
            rig.owner.begin_host()
    assert (rig.owner.read_launch()["started_at"] is not None) == (when != "scheduled")
    assert not rig.db.execute_read("SELECT * FROM acquisition_attempts")


@pytest.mark.asyncio
@pytest.mark.parametrize("closed", [False, True])
async def test_creation_launch_child_checks_same_row_and_strips_context(creation_launch, monkeypatch, closed):
    from agent_bridge.acquisition_host_admission import CONTEXT_ENV
    from agent_bridge.session_host.launcher import _spawn_child

    rig = creation_launch
    argv = [sys.executable, "-c", "pass"]
    raw = rig.owner.child_context(argv, rig.launch_target.cwd)
    rig.owner.begin_host()
    monkeypatch.setattr(os, "getppid", lambda: rig.producer.pid)
    spawn = AsyncMock(return_value=SimpleNamespace(pid=22))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    if closed:
        close(rig.db, rig.identity, rig.producer)
        with pytest.raises(RuntimeCustodyError, match="open epoch"):
            await _spawn_child(argv, rig.launch_target.cwd, {
                CONTEXT_ENV: raw, "AGENT_BRIDGE_SESSION_ID": rig.session.session_id,
            })
        spawn.assert_not_awaited()
    else:
        before = rig.db.runtime_epoch(rig.identity)
        await _spawn_child(argv, rig.launch_target.cwd, {
            CONTEXT_ENV: raw, "AGENT_BRIDGE_SESSION_ID": rig.session.session_id,
        })
        assert CONTEXT_ENV not in spawn.call_args.kwargs["env"]
        assert rig.db.runtime_epoch(rig.identity) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("closed", ["before", "reply", "lost"])
async def test_creation_launch_native_scheduling_and_unknown_are_original(creation_launch, closed):
    rig = creation_launch
    rig.owner.begin_host()

    async def reply(**_kwargs):
        if closed == "lost":
            raise ConnectionError("lost native reply")
        close(rig.db, rig.identity, rig.producer)
        return SimpleNamespace(session_id="original-native")

    method = AsyncMock(side_effect=reply)
    client = attach(rig, method)
    invoke = rig.owner.native_method(client, "new", method)
    pending = invoke(cwd=rig.launch_target.cwd, mcp_servers=[])
    if closed == "before":
        close(rig.db, rig.identity, rig.producer)
        with pytest.raises(RuntimeCustodyError, match="open epoch"):
            await pending
        method.assert_not_awaited()
    elif closed == "lost":
        with pytest.raises(ConnectionError):
            await pending
        with pytest.raises(RuntimeCustodyError, match="held"):
            await invoke(cwd=rig.launch_target.cwd, mcp_servers=[])
        method.assert_awaited_once()
    else:
        assert (await pending).session_id == "original-native"
        with pytest.raises(RuntimeCustodyError, match="open epoch"):
            rig.owner.require_configuration(client)
    assert (rig.owner.read_launch()["result"] is not None) == (closed == "reply")
    assert len(rig.db.execute_read("SELECT * FROM workspace_embodiments")) == 1
    assert not rig.db.execute_read("SELECT * FROM acquisition_attempts")


@pytest.mark.asyncio
async def test_creation_launch_attested_exec_rechecks_after_attestation(creation_launch, monkeypatch):
    from agent_bridge.acquisition_host_admission import CONTEXT_ENV
    from agent_bridge.preference_attestation import SHELL_MARKER
    from agent_bridge.session_host.launcher import run_host

    rig = creation_launch
    argv = [SHELL_MARKER, "synthetic"]
    raw = rig.owner.child_context(argv, rig.launch_target.cwd)
    rig.owner.begin_host()
    monkeypatch.setattr(os, "getppid", lambda: rig.producer.pid)
    monkeypatch.setattr("agent_bridge.session_host.launcher.apply_host_survival", lambda: None)
    called = []

    async def attested(_argv, _cwd, _env, _spawn, *, before_exec):
        called.append("attestation")
        close(rig.db, rig.identity, rig.producer)
        before_exec()
        raise AssertionError("must not permit attested exec after closure")

    monkeypatch.setattr("agent_bridge.session_host.preference_spawn.spawn_attested", attested)
    with pytest.raises(RuntimeCustodyError, match="open epoch"):
        await run_host(
            argv, cwd=rig.launch_target.cwd, env={CONTEXT_ENV: raw},
            session_id=rig.session.session_id,
        )
    assert called == ["attestation"]
    assert rig.owner.read_launch()["result"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"elevated": True}, {"host": "remote"}, {"type": "ssh"}, {"container": {"name": "remote"}},
    {"native_resume_id": "existing"}, {"explicit_cwd": True},
])
async def test_creation_launch_unsupported_target_refuses_before_allocation(allocation, change):
    from dataclasses import replace

    rig = allocation
    with pytest.raises(RuntimeCustodyError):
        await rig.frontend.start(replace(rig.target, **change), key="original-key")
    assert not rig.calls
    assert not rig.db.execute_read("SELECT * FROM workspace_embodiments")


@pytest.mark.asyncio
async def test_creation_launch_unsupported_options_refuse_before_allocation(allocation):
    rig = allocation
    with pytest.raises(RuntimeCustodyError, match="launch options"):
        await rig.frontend.start(rig.target, key="original-key", provider_instance=True)
    assert not rig.calls
    assert not rig.db.execute_read("SELECT * FROM workspace_embodiments")


@pytest.mark.asyncio
@pytest.mark.parametrize("captured", [False, True])
async def test_creation_launch_associated_host_is_not_ordinary_reapable(creation_launch, captured):
    rig = creation_launch
    rig.session.status = SessionStatus.IDLE
    if captured:
        rig.session._native_turn_transport = object()
    assert not rig.manager._host_reapable(rig.session)


def test_creation_launch_unassociated_idle_host_remains_ordinary_reapable(session_manager, spawn_target):
    session = Session("legacy-host", "legacy", spawn_target)
    session.status = SessionStatus.IDLE
    session_manager.db.create_session("legacy-host", "legacy", None, spawn_target.cwd, "local", "idle", 1)
    assert session_manager._host_reapable(session)
