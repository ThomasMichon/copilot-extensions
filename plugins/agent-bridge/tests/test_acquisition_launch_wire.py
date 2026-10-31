"""Actual local Host, child and ACP SDK; synthetic peers, not native qualification."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import AsyncMock

import pytest
from zdd.diagnostics import process_start_time

from agent_bridge import session_manager as core
from agent_bridge.acp_client import AcpClient
from agent_bridge.acquisition_contract import AcquisitionRefused
from agent_bridge.acquisition_launch_owner import AcquisitionFrontend
from agent_bridge.models import SessionStatus
from agent_bridge.runtime_custody import RuntimeCustodyError, RuntimeProducer
from agent_bridge.session_host.host_index import HostIndex
from agent_bridge.session_host_connection import _SessionHostConnectionMixin
from agent_bridge.session_manager import Session, SessionManager
from agent_bridge.session_prompts import _SessionPromptMixin
from agent_bridge.transport import SpawnTarget
from test_acquisition_runtime import req, rig as rig
from test_runtime_custody import close, installation


async def assert_direct_lifecycle_refused(client: AcpClient, cwd: str) -> None:
    owner = client._acquisition_launch
    assert owner is not None
    native = client.acp_session_id
    for candidate in (None, copy.copy(owner)):
        with pytest.raises(RuntimeCustodyError):
            await client.new_session(cwd, acquisition_launch=candidate)
        with pytest.raises(RuntimeCustodyError):
            await client.load_session(cwd, native, acquisition_launch=candidate)
    with pytest.raises((RuntimeCustodyError, AcquisitionRefused)):
        await client.new_session(cwd, acquisition_launch=owner)
    with pytest.raises((RuntimeCustodyError, AcquisitionRefused)):
        await client.load_session(cwd, native, acquisition_launch=owner)
    assert client.acp_session_id == native


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,mode", [
    ("new", "reply"), ("load", "reply"), ("new", "drop-reply"), ("load", "drop-reply"),
    ("new", "live-drop-reply"), ("load", "live-drop-reply"),
    ("new", "close-before-capture"), ("load", "close-before-capture"),
    ("new", "stop-reap-resume"),
    ("new", "shutdown-rehydrate"),
    ("new", "shutdown-active-reaper"),
])
async def test_acquisition_launch_real_local_host_and_original_sdk_delivery(
    rig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str, mode: str,
) -> None:
    identity = installation(rig.db)
    epoch = rig.db.initialize_runtime_epoch(identity)
    producer = RuntimeProducer("synthetic-frontend", os.getpid(), process_start_time(os.getpid()))
    admission = rig.db.enroll_runtime_producer(identity, epoch.epoch, producer)
    frontend = AcquisitionFrontend.capture(rig.runtime, identity, admission)
    if mode == "live-drop-reply":
        rig.manager._timeouts = rig.manager._timeouts.model_copy(update={"session_new": 0.25})
    if mode in ("shutdown-rehydrate", "shutdown-active-reaper"):
        rig.manager._session_host_active_reap_seconds = (
            1800.0 if mode == "shutdown-rehydrate" else 0.1
        )
    audit = tmp_path / "received.jsonl"
    peer = Path(__file__).parent / "fixtures" / "acquisition_launch_peer.py"
    handles, captures = [], []
    from agent_bridge.session_host import launcher, spawner

    original_launch = spawner.launch_session_host
    original_tail_capture = launcher._CrashTailCapture

    def capture_tail(*args, **kwargs):
        capture = original_tail_capture(*args, **kwargs)
        captures.append(capture)
        return capture

    def record_launch(*args, **kwargs):
        handle = original_launch(*args, **kwargs, state_dir=tmp_path / "host-state")
        handles.append(handle)
        return handle

    async def resolve(target, **_kwargs):
        return (
            [sys.executable, "-u", str(peer), str(rig.db.db_path), str(audit), "delivery-a", mode],
            target.cwd, dict(target.env),
        )

    monkeypatch.setattr(spawner, "launch_session_host", record_launch)
    monkeypatch.setattr(launcher, "_CrashTailCapture", capture_tail)
    monkeypatch.setattr("agent_bridge.transport.resolve_local_launch", resolve)
    monkeypatch.setattr("agent_bridge.local_static_preparation.prepare_for_launch", AsyncMock())
    monkeypatch.setattr(SessionManager, "_connect_via_session_host", _SessionHostConnectionMixin._connect_via_session_host)
    monkeypatch.setattr(core, "AcpClient", AcpClient)
    monkeypatch.setattr(rig.manager, "_run_prompt", _SessionPromptMixin._run_prompt.__get__(rig.manager))
    monkeypatch.setattr(rig.manager, "_notify_host_reapable", AsyncMock())
    rig.manager._host_index = HostIndex(tmp_path / "hosts.json")
    # An explicit empty test baseline, not inferred absence in an installation.
    rig.manager._host_index._flush()
    original = req()
    if mode == "close-before-capture":
        from agent_bridge.acquisition_host_transport import AcquisitionHostTransport

        original_capture = AcquisitionHostTransport.capture

        def close_before_capture(cls, launch, session):
            close(rig.db, identity, producer)
            return original_capture(launch, session)

        monkeypatch.setattr(AcquisitionHostTransport, "capture", classmethod(close_before_capture))
    if kind == "load":
        target = SpawnTarget(
            type="local", cwd=str(tmp_path), worktree_id="worktree-a",
            project="test-project", explicit_cwd=True,
        )
        session = Session("retained-bridge", "retained", target, "task-worker")
        session.acp_session_id, session.status = "synthetic-existing", SessionStatus.STOPPED
        rig.db.create_session(
            session.session_id, session.name, session.agent_name, target.cwd, "local", "stopped",
            time.time(), acp_session_id=session.acp_session_id, target_json=target.to_json(),
            runtime_admission=admission,
        )
        rig.manager._sessions[session.session_id] = session
        rig.head["id"] = session.acp_session_id
        rig.source["task"]["owner_session_id"] = session.session_id
        rig.source["acquisition_reservation"].update(
            worktree_ownership="reused", session_handle=f"local-body:{session.session_id}",
        )
        original = req(
            allocation="reused", intent="continue",
            prior_session_id=session.session_id, prior_native_session_id=session.acp_session_id,
        )
    try:
        if mode in ("drop-reply", "live-drop-reply"):
            with pytest.raises((RuntimeError, ConnectionError, asyncio.TimeoutError)):
                await asyncio.wait_for(rig.runtime.acquire(original, custody=frontend), 15)
            with pytest.raises(RuntimeCustodyError, match="held"):
                await rig.runtime.acquire(original, custody=frontend)
            if mode == "live-drop-reply":
                session = rig.manager.list_sessions()[0]
                owner = session._local_launch_owner
                assert owner is not None and owner._host_process is handles[0].proc
                assert handles[0].proc.poll() is None and process_start_time(handles[0].child_pid)
                assert session.client is owner._client and owner._connection is session.client._connection
                assert rig.manager._host_index.get(session.session_id) is owner._host_record
            if kind == "load":
                close(rig.db, identity, producer)
                spawn = AsyncMock(side_effect=AssertionError("must not spawn"))
                monkeypatch.setattr(core, "spawn", spawn)
                with pytest.raises(RuntimeCustodyError, match="resync"):
                    await rig.manager.resync_session(session.session_id)
                spawn.assert_not_awaited()
        elif mode == "close-before-capture":
            with pytest.raises(RuntimeCustodyError, match="open epoch"):
                await asyncio.wait_for(rig.runtime.acquire(original, custody=frontend), 15)
            session = next(s for s in rig.manager.list_sessions() if s.acp_session_id)
            assert session.status == SessionStatus.IDLE
            assert session._native_turn_transport is None
            await assert_direct_lifecycle_refused(session.client, session.target.cwd)
            with pytest.raises(RuntimeCustodyError, match="prompt"):
                await rig.manager.submit_prompt(session.session_id, "must not send")
            with pytest.raises(RuntimeCustodyError, match="not been captured"):
                await session.client.send_prompt("must not send directly")
            start = AsyncMock(side_effect=AssertionError("must not start a successor"))
            monkeypatch.setattr(rig.manager, "start_session", start)
            for seed in (None, "external seed"):
                with pytest.raises(RuntimeCustodyError, match="lifecycle handoff"):
                    await rig.manager.handoff_session(session.session_id, seed_text=seed)
            start.assert_not_awaited()
            assert session.turn_count == 0
            assert rig.db.get_turn(session.session_id, 0) is None
        else:
            result = await asyncio.wait_for(rig.runtime.acquire(original, custody=frontend), 15)
            session = rig.manager.get_session(result.session_id)
            assert session is not None and session._native_turn_transport is not None
            await asyncio.wait_for(session._prompt_task, 5)
            assert result.native_session_id == ("synthetic-created" if kind == "new" else "synthetic-existing")
            assert await rig.runtime.acquire(original) == result
            turn = rig.db.get_turn(result.session_id, 0)
            assert turn["native_accepted_at"] is not None and turn["native_consumed_at"] is None
            await assert_direct_lifecycle_refused(session.client, session.target.cwd)
            if mode == "stop-reap-resume":
                retained_owner = session._native_turn_transport
                await rig.manager.stop_session(session.session_id, reap_host=True)
                await asyncio.to_thread(handles[0].proc.wait, timeout=5)
                assert session.client is None and session._native_turn_transport is retained_owner
                continuation = req(
                    delivery_id="delivery-b", allocation="reused", intent="continue",
                    prior_session_id=session.session_id, prior_native_session_id=session.acp_session_id,
                )
                rig.runtime.store.journal.claim(continuation, runtime_admission=admission)
                launch = frontend.prepare(
                    continuation, session_id=session.session_id, native_session_id=session.acp_session_id,
                )
                with pytest.raises(RuntimeCustodyError, match="without replay"):
                    await rig.manager.resume_session(
                        session.session_id, drain=False, acquisition_launch=launch,
                    )
                assert rig.runtime.store.journal.read_launch(continuation)["started_at"] is None
                assert session._native_turn_transport is retained_owner
            elif mode in ("shutdown-rehydrate", "shutdown-active-reaper"):
                detach = session.client.session_host_client.detach
                detach_spy = AsyncMock(wraps=detach)
                monkeypatch.setattr(session.client.session_host_client, "detach", detach_spy)
                await rig.manager.stop_session(
                    session.session_id, cancel_turn=False, allow_background_recovery=True,
                )
                detach_spy.assert_awaited_once_with(False)
                await asyncio.sleep(0.4)
                assert handles[0].proc.poll() is None
                argv = handles[0].proc.args
                assert float(argv[argv.index("--active-reap-seconds") + 1]) == 0
                assert float(argv[argv.index("--unexpected-reap-seconds") + 1]) == 0
                stored = rig.db.get_session(session.session_id)
                assert not stored["background_recovery_enabled"]
                assert stored["cold_recovery_stop_token"] is None
                close(rig.db, identity, producer)
                successor = SessionManager(rig.db)
                successor._host_index = rig.manager._host_index
                retained = successor.get_session(session.session_id)
                assert retained.client is None and not retained.background_recovery_enabled
                attach = AsyncMock(side_effect=AssertionError("must not reattach"))
                monkeypatch.setattr(successor, "_reattach_one", attach)
                assert await successor.reattach_session_hosts(recover_cold=False) == 0
                attach.assert_not_awaited()
                with pytest.raises(RuntimeCustodyError, match="Host reattachment"):
                    await successor._try_reattach_live_host(retained)
                with pytest.raises(RuntimeCustodyError, match="Host reattachment"):
                    await _SessionHostConnectionMixin._reattach_one(
                        successor, successor._host_index.get(session.session_id),
                        retained, new_status=SessionStatus.IDLE,
                    )
                with pytest.raises(RuntimeCustodyError, match="prompt"):
                    await successor.submit_prompt(session.session_id, "must not recover")
                assert retained.client is None and handles[0].proc.poll() is None, captures[0].tail_text()
        received = [json.loads(line) for line in audit.read_text().splitlines()]
        assert [item["method"] for item in received] == (
            [f"session/{kind}"] if mode in ("drop-reply", "live-drop-reply", "close-before-capture")
            else [f"session/{kind}", "session/prompt"]
        )
        assert len(handles) == 1
        assert {item["pid"] for item in received} == {handles[0].child_pid}
        row = rig.db.execute_read("SELECT * FROM acquisition_attempts")[0]
        assert {item["handoff"] for item in received} == {json.loads(row["launch_handoff_json"])["id"]}
        assert row["runtime_custody_json"] == admission.document()
        assert (row["launch_result_json"] is not None) == (mode not in ("drop-reply", "live-drop-reply"))
        assert rig.db.runtime_custody_inventory(identity)["restore_qualified"] is False
    finally:
        for session in rig.manager.list_sessions():
            if session.client is not None:
                with contextlib.suppress(Exception):
                    await session.client.session_host_client.terminate()
                await session.client.shutdown()
            if session._prompt_task is not None and not session._prompt_task.done():
                session._prompt_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await session._prompt_task
        for handle in handles:
            if handle.proc.poll() is None:
                handle.proc.terminate()
            await asyncio.to_thread(handle.proc.wait, timeout=5)
