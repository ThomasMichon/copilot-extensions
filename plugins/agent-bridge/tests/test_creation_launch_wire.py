"""Original creation through actual Host/child/SDK with a synthetic local peer."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import AsyncMock

import pytest
from zdd.diagnostics import process_start_time

from agent_bridge import session_manager as core, workspace_creation as workspace
from agent_bridge.acp_client import AcpClient
from agent_bridge.acquisition_contract import AcquisitionRefused
from agent_bridge.creation_allocation import CreationFrontend
from agent_bridge.creation_custody import CreationJournal, CreationRequest
from agent_bridge.creation_launch_owner import CreationLaunchOwner
from agent_bridge.local_host_transport import LocalHostTransport
from agent_bridge.runtime_custody import RuntimeCustodyError, RuntimeProducer
from agent_bridge.session_host.host_index import HostIndex
from agent_bridge.session_host_connection import _SessionHostConnectionMixin
from agent_bridge.session_manager import SessionManager
from agent_bridge.session_prompts import _SessionPromptMixin
from agent_bridge.transport import SpawnTarget
from test_acquisition_launch_wire import assert_direct_lifecycle_refused
from test_runtime_custody import close, installation


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [
    "reply", "no-prompt", "drop-reply", "live-drop-reply", "live-initialize",
    "cancel-native", "ready-timeout", "environment",
    "lost-binding", "closed-binding", "closed-capture",
    "config", "closed-config", "closed-first-prompt", "closed-binding-scheduled", "closed-binding-start",
])
async def test_creation_launch_real_host_original_binding_and_first_prompt(
    session_manager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    manager, db = session_manager, session_manager.db
    identity = installation(db)
    epoch = db.initialize_runtime_epoch(identity)
    producer = RuntimeProducer("creation-frontend", os.getpid(), process_start_time(os.getpid()))
    admission = db.enroll_runtime_producer(identity, epoch.epoch, producer)
    frontend = CreationFrontend.capture(manager, identity, admission)
    if mode == "live-drop-reply":
        manager._timeouts = manager._timeouts.model_copy(update={"session_new": 0.25})
    if mode == "live-initialize":
        manager._timeouts = manager._timeouts.model_copy(update={"session_start": 0.25})
    if mode == "ready-timeout":
        manager._timeouts = manager._timeouts.model_copy(update={"session_host_ready": 0.0})
    manager._session_host_active_reap_seconds = 1800.0
    path = tmp_path / "workspace"
    path.mkdir()
    target = SpawnTarget(type="local", project="fixture", cwd=str(tmp_path))
    if mode == "environment":
        target.env = {"SYNTHETIC_BASE": "preserved", "SYNTHETIC_OVERRIDE": "old"}
    saved = {
        "id": "creation-worktree", "repo": "fixture", "path": str(path), "creation_nonce": "original-nonce",
    }
    head = None
    calls, handles, ordinary_registrations = [], [], []
    digest = hashlib.sha256(json.dumps(["fixture", "original"]).encode()).hexdigest()
    delivery = f"creation:{digest}"
    audit = tmp_path / "received.jsonl"
    peer = Path(__file__).parent / "fixtures" / "acquisition_launch_peer.py"

    async def command(args, project, *, before_spawn):
        nonlocal head
        assert project == "fixture"
        if args[0] == "bind-session" and mode == "closed-binding-scheduled":
            close(db, identity, producer)
        before_spawn()
        calls.append(args[0])
        if args[0] == "create":
            return {"creation": {"version": 1, "status": "complete"}, "worktree": saved}
        assert args[0] == "bind-session"
        head = args[args.index("--session-id") + 1]
        if mode == "lost-binding":
            raise ConnectionError("lost original binding reply")
        if mode == "closed-binding":
            close(db, identity, producer)
        return {}

    async def current(_project, _worktree, *, expected):
        assert expected == saved
        return {
            **saved, "status": "active", "session_count": 0 if head is None else 1,
            "sessions_indexed": True,
        }, head

    def register_lineage(worktree_id, native, **_kwargs):
        nonlocal head
        assert worktree_id == saved["id"]
        ordinary_registrations.append(native)
        head = native
        return True

    from agent_bridge.session_host import spawner

    original_launch = spawner.launch_session_host

    def record_launch(*args, **kwargs):
        handle = original_launch(*args, **kwargs, state_dir=tmp_path / "host-state")
        handles.append(handle)
        return handle

    async def resolve(resolved, **_kwargs):
        assert resolved.cwd == str(path) and resolved.worktree_id == saved["id"]
        if mode == "environment":
            assert resolved.env == {
                "SYNTHETIC_BASE": "preserved", "SYNTHETIC_OVERRIDE": "new", "SYNTHETIC_EXTRA": "added",
            }
        return [
            sys.executable, "-u", str(peer), str(db.db_path), str(audit), delivery, mode,
        ], resolved.cwd, dict(resolved.env)

    monkeypatch.setattr(workspace, "worktree_command", command)
    monkeypatch.setattr(workspace, "_workspace", current)
    monkeypatch.setattr("agent_bridge.worktree_lineage.register_session", register_lineage)
    monkeypatch.setattr(spawner, "launch_session_host", record_launch)
    monkeypatch.setattr("agent_bridge.transport.resolve_local_launch", resolve)
    monkeypatch.setattr("agent_bridge.local_static_preparation.prepare_for_launch", AsyncMock())
    monkeypatch.setattr(SessionManager, "_connect_via_session_host", _SessionHostConnectionMixin._connect_via_session_host)
    monkeypatch.setattr(core, "AcpClient", AcpClient)
    monkeypatch.setattr(manager, "_notify_host_reapable", AsyncMock())
    manager._host_index = HostIndex(tmp_path / "hosts.json")
    manager._host_index._flush()
    if mode == "closed-capture":
        capture = LocalHostTransport.capture

        def closed(cls, launch, session):
            close(db, identity, producer)
            return capture(launch, session)

        monkeypatch.setattr(LocalHostTransport, "capture", classmethod(closed))
    if mode == "closed-config":
        configuration = CreationLaunchOwner.require_configuration

        def closed_configuration(owner, client):
            close(db, identity, producer)
            configuration(owner, client)

        monkeypatch.setattr(CreationLaunchOwner, "require_configuration", closed_configuration)
    if mode == "closed-first-prompt":
        async def closed_prompt(*args, **kwargs):
            close(db, identity, producer)
            return await _SessionPromptMixin._submit_prompt_locked(manager, *args, **kwargs)

        monkeypatch.setattr(manager, "_submit_prompt_locked", closed_prompt)
    if mode == "closed-binding-start":
        begin_binding = CreationJournal.begin_binding

        def closed_start(journal, *args, **kwargs):
            result = begin_binding(journal, *args, **kwargs)
            close(db, identity, producer)
            return result

        monkeypatch.setattr(CreationJournal, "begin_binding", closed_start)
    prompt = None if mode == "no-prompt" else "Original creation prompt."
    options = {"model": "fixture-model", "effort": "high"} if mode in ("config", "closed-config") else {}
    if mode == "environment":
        options = {"env_overrides": {"SYNTHETIC_OVERRIDE": "new", "SYNTHETIC_EXTRA": "added"}}
    request = CreationRequest.capture("workspace", digest, {
        "target": json.loads(target.to_json()), "options": options, "prompt": prompt,
    })
    try:
        if mode == "cancel-native":
            launch = asyncio.create_task(frontend.start(target, key="original", prompt=prompt, **options))
            try:
                async with asyncio.timeout(10):
                    while not audit.exists():
                        await asyncio.sleep(0.01)
            finally:
                launch.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await launch
        elif mode in ("drop-reply", "live-drop-reply", "live-initialize", "ready-timeout"):
            with pytest.raises((RuntimeError, RuntimeCustodyError, ConnectionError, asyncio.TimeoutError)):
                await asyncio.wait_for(frontend.start(target, key="original", prompt=prompt, **options), 15)
        elif mode == "lost-binding":
            with pytest.raises(ConnectionError, match="lost original binding"):
                await asyncio.wait_for(frontend.start(target, key="original", prompt=prompt, **options), 15)
        elif mode.startswith("closed-"):
            with pytest.raises(RuntimeCustodyError, match="open epoch"):
                await asyncio.wait_for(frontend.start(target, key="original", prompt=prompt, **options), 15)
        else:
            session, result = await asyncio.wait_for(frontend.start(target, key="original", prompt=prompt, **options), 15)
            if session._prompt_task is not None:
                await asyncio.wait_for(session._prompt_task, 5)
            assert session.acp_session_id == "synthetic-created"
            assert result["turn_index"] == (None if prompt is None else 0)
            assert not session.background_recovery_enabled
            if prompt is not None:
                manager._idle_reap_ttl_seconds = 1
                session.updated_at = 0
                client = session.client
                assert not manager._host_reapable(session)
                assert await manager.sweep_idle_sessions() == 0
                assert session.client is client and handles[0].proc.poll() is None
            before = db.runtime_epoch(identity)
            replay, observed = await frontend.start(target, key="original", prompt=prompt, **options)
            assert replay is session and observed == {**result, "replayed": True}
            assert db.runtime_epoch(identity) == before
            close(db, identity, producer)
            before = db.runtime_epoch(identity)
            assert (await frontend.start(target, key="original", prompt=prompt, **options))[1] == observed
            assert db.runtime_epoch(identity) == before
        row = CreationJournal(db).read(request)
        assert row["state"] == {
            "reply": "complete", "no-prompt": "complete", "drop-reply": "launching",
            "live-drop-reply": "launching", "environment": "complete",
            "live-initialize": "launching", "cancel-native": "launching",
            "ready-timeout": "launching",
            "lost-binding": "binding_started", "closed-binding": "bound", "closed-capture": "bound",
            "config": "complete", "closed-config": "native_accepted",
            "closed-first-prompt": "delivering", "closed-binding-scheduled": "native_accepted",
            "closed-binding-start": "binding_started",
        }[mode]
        if mode not in ("reply", "no-prompt", "config", "environment"):
            with pytest.raises((AcquisitionRefused, RuntimeCustodyError)):
                await frontend.start(target, key="original", prompt=prompt, **options)
        if mode == "ready-timeout":
            owner = manager.list_sessions()[0]._local_launch_owner
            assert owner is not None and owner._host_process.poll() is None
            async with asyncio.timeout(5):
                while not Path(owner._state_file).exists():
                    await asyncio.sleep(0.01)
            actual = json.loads(Path(owner._state_file).read_text())
            assert actual["pid"] == owner._host_pid
            assert process_start_time(actual["child_pid"])
            assert owner._host_process.poll() is None and owner._host is None
            assert calls == ["create"] and not handles and not ordinary_registrations
            assert not db.execute_read("SELECT * FROM acquisition_attempts")
            assert len(db.execute_read("SELECT * FROM workspace_embodiments")) == 1
            return
        assert calls == (
            ["create"] if mode in (
                "drop-reply", "live-drop-reply", "live-initialize", "cancel-native", "closed-config",
                "closed-binding-scheduled", "closed-binding-start",
            )
            else ["create", "bind-session"]
        )
        assert len(handles) == 1
        if mode in ("live-drop-reply", "live-initialize", "cancel-native", "closed-config"):
            assert handles[0].proc.poll() is None
            assert process_start_time(handles[0].child_pid)
            session = manager.list_sessions()[0]
            owner = session._local_launch_owner
            assert owner is not None and owner._host_process is handles[0].proc
            assert session.client is owner._client and owner._connection is session.client._connection
            assert owner._host_pid == handles[0].host_pid and owner._child_pid == handles[0].child_pid
            assert session._native_turn_transport is None
            assert manager._host_index.get(session.session_id).host_pid == handles[0].host_pid
        argv = handles[0].proc.args
        assert float(argv[argv.index("--active-reap-seconds") + 1]) == 0
        assert float(argv[argv.index("--unexpected-reap-seconds") + 1]) == 0
        assert not ordinary_registrations
        received = [json.loads(line) for line in audit.read_text().splitlines()] if audit.exists() else []
        expected = [] if mode == "live-initialize" else ["session/new"]
        if mode == "config":
            expected += ["session/set_config_option", "session/set_config_option"]
        if mode in ("reply", "config", "environment"):
            expected += ["session/prompt"]
        assert [item["method"] for item in received] == expected
        if received:
            assert {item["pid"] for item in received} == {handles[0].child_pid}
            assert {item["handoff"] for item in received} == {json.loads(row["launch_handoff_json"])["id"]}
        if mode == "environment":
            assert all(item["environment"] == {
                "SYNTHETIC_BASE": "preserved", "SYNTHETIC_OVERRIDE": "new", "SYNTHETIC_EXTRA": "added",
            } for item in received)
            assert target.env == {"SYNTHETIC_BASE": "preserved", "SYNTHETIC_OVERRIDE": "old"}
            assert options == {"env_overrides": {"SYNTHETIC_OVERRIDE": "new", "SYNTHETIC_EXTRA": "added"}}
        assert not db.execute_read("SELECT * FROM acquisition_attempts")
        assert len(db.execute_read("SELECT * FROM workspace_embodiments")) == 1
        for session in manager.list_sessions():
            if session.client is not None:
                await assert_direct_lifecycle_refused(session.client, session.target.cwd)
                if session._native_turn_transport is None:
                    error, message = (
                        (ValueError, f"{session.status.value}, not idle") if session.status.value != "idle"
                        else (RuntimeCustodyError, "prompt")
                    )
                    with pytest.raises(error, match=message):
                        await manager.submit_prompt(session.session_id, "must not downgrade")
                with pytest.raises(RuntimeCustodyError, match="resync"):
                    await manager.resync_session(session.session_id)
                with pytest.raises(RuntimeCustodyError, match="handoff"):
                    await manager.handoff_session(session.session_id, seed_text="must not recreate")
    finally:
        for session in manager.list_sessions():
            if session._prompt_task is not None and not session._prompt_task.done():
                session._prompt_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await session._prompt_task
            if session.client is not None:
                with contextlib.suppress(Exception):
                    await session.client.session_host_client.terminate()
                await session.client.shutdown()
        for handle in handles:
            if handle.proc.poll() is None:
                handle.proc.terminate()
            await asyncio.to_thread(handle.proc.wait, timeout=5)
        for session in manager.list_sessions():
            owner = session._local_launch_owner
            if owner is not None and owner._host_process is not None:
                if owner._host_process.poll() is None:
                    owner._host_process.terminate()
                await asyncio.to_thread(owner._host_process.wait, timeout=5)
