"""Model-free ACP load crosses the actual selected Worktrees parser process."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from agent_procutil import direct_python, no_window_kwargs

from agent_bridge import acquisition_worktree_writer as writers, retained_load_acceptance as acceptance
from agent_bridge import worktree_lineage
from agent_bridge import retained_recovery_settlement as settlement, startup_progress
from agent_bridge.acquisition_contract import AcquisitionRefused, WorktreeWriterRequirement
from agent_bridge.connect import ConnectTracker
from agent_bridge.creation_worktree_writer import CreationWriterScope, JOURNAL_KEY
from agent_bridge.events import EventLog
from agent_bridge.models import PhasedTimeouts, RetainedRecoveryRequest
from agent_bridge.retained_session_recovery import RetainedRecovery
from agent_bridge.session_manager import Session
from agent_bridge.session_status import SessionStatus
from agent_bridge.transport import SpawnTarget

NATIVE = "11111111-2222-4333-8444-555555555555"
pytestmark = pytest.mark.contract("agent_bridge.retained_hosted_registration")


@pytest.mark.asyncio
@pytest.mark.parametrize("completion", [
    "direct", "accepted", "historical", "committed", "restart", "lost-response",
    "stop", "foreign", "raced-stop", "configured", "config-failed", "config-crash",
    "config-missing", "config-stop", "config-activity",
    "config-retained", "config-timeout", "config-timeout-restart", "config-unqualified-client",
])
async def test_acknowledged_hosted_load_registers_without_a_cli_hold(
    session_manager: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    completion: str,
) -> None:
    if not sys.platform.startswith("linux"):
        pytest.skip("Hosted activation is qualified against Linux procfs")
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "plugins/agent-worktrees/src"))
    for library in ("lazy-cli-dispatch", "work-coalescing-singleton", "update-telemetry"):
        monkeypatch.syspath_prepend(str(root / "libs" / library / "src"))
    from agent_worktrees import tracking, session_liveness

    state = tmp_path / "authority"
    state.mkdir()
    identity = {"installation_root": str(state / "owner"), "runtime_generation": "a" * 32}
    (state / "writer-identity.json").write_text(json.dumps(identity), encoding="utf-8")
    interpreter, environment = direct_python(sys.executable)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    prefix = (
        interpreter, str(root / "plugins/agent-worktrees/tests/fixtures/worktree_record_owner.py"),
        str(root), str(state), "example", "host",
    )
    protected = subprocess.run(
        [*prefix, "protect"], capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert protected.returncode == 0, protected.stderr + protected.stdout
    path = state / "project/worktrees/host.yaml"
    seeded = subprocess.run(
        [*prefix, "seed-ended-native", NATIVE],
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert seeded.returncode == 0, seeded.stderr + seeded.stdout
    before = tracking.load_record(path)
    checkout = Path(before.worktree_path)
    (checkout / ".git").mkdir()
    policy = checkout / ".copilot-extensions/agent-worktrees/config.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text("expose_agent: true\n", encoding="utf-8")
    transcript = tmp_path / "acp.jsonl"
    configured = completion.startswith("config")

    async def resolve(*_args: Any, **_kwargs: Any) -> Any:
        mode = (
            "config-timeout" if completion.startswith("config-timeout")
            else "config-failed" if completion in {"config-failed", "config-retained", "config-unqualified-client"}
            else "configured"
        )
        return [
            interpreter, str(Path(__file__).with_name("cold_drain_peer.py")),
            str(transcript), NATIVE,
        ] + ([mode] if configured else []), str(checkout), dict(os.environ)

    monkeypatch.setattr("agent_bridge.transport.resolve_local_launch", resolve)
    monkeypatch.setattr("agent_bridge.local_static_preparation.prepare_for_launch", AsyncMock())
    monkeypatch.setattr(worktree_lineage, "_agent_worktrees_launch_prefix", lambda: list(prefix))
    requirement = WorktreeWriterRequirement.model_validate({
        "version": 1, "identity": identity, "creation_nonce": before.creation_nonce,
    })
    writer = writers.RetainedWriter(
        CreationWriterScope("a" * 64, "example", "host", str(checkout), requirement), prefix,
        execution_identity=requirement.identity,
    )
    monkeypatch.setattr(writers, "current", lambda: writer)
    manager = session_manager
    manager._generation_id = "c" * 32
    target = SpawnTarget(
        type="local", cwd=str(checkout), project="example", worktree_id="host",
        venue={JOURNAL_KEY: "a" * 64},
    )
    session = Session("original-control", "Original", target)
    session.acp_session_id, session.status = NATIVE, SessionStatus.STOPPED
    manager.db.create_session(
        session.session_id, session.name, None, target.cwd, "local", "stopped", time.time(),
        acp_session_id=NATIVE, target_json=target.to_json(),
    )
    event_log = EventLog(db=manager.db, session_id=session.session_id)
    session.event_log = event_log
    manager._sessions[session.session_id] = session
    request = RetainedRecoveryRequest(
        operation_id="original-load", journal_key="a" * 64, native_session_id=NATIVE,
        head_revision=before.head_revision, bridge_installation=str(tmp_path / "bridge"),
    )
    owner = RetainedRecovery(manager, session, request, target.to_json(), asyncio.current_task())
    retained_frontend = completion in {
        "config-retained", "config-timeout", "config-timeout-restart", "config-unqualified-client",
    }
    if retained_frontend:
        session._retained_recovery_owner = owner
    if completion.startswith("config-timeout"):
        manager._timeouts = PhasedTimeouts(session_new=2)
    if configured:
        monkeypatch.setattr("agent_bridge.session_host_connection.client_preferences", lambda *_args: {
            "preference_source": "target-settings",
            "confirmed_preferences": {"model": "original-model", "reasoning_effort": "high"},
        })
    if completion == "direct":
        monkeypatch.setattr(owner, "require_current", Mock())
    else:
        from agent_bridge import creation_writer_inspection, workspace_creation
        from agent_bridge.acquisition_store import AcquisitionStore

        AcquisitionStore(manager.db)
        facts = workspace_creation.creation_facts(target, None, {"worktree_writer": requirement.model_dump()})
        manager.db.execute_write(
            "INSERT INTO workspace_embodiments "
            "(key_digest,facts_digest,state,session_id,native_session_id,worktree_json,"
            "creation_writer_json,creation_recoveries_json) VALUES (?,?,'complete',?,?,?,?,?)",
            (
                request.journal_key, hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest(),
                session.session_id, NATIVE, json.dumps({
                    "id": "host", "repo": "example", "path": str(checkout),
                    "creation_nonce": before.creation_nonce,
                }),
                json.dumps({
                    "version": 1, "facts": facts, "worktree_writer": requirement.model_dump(),
                    "binding": {"version": 1, "prefix": list(prefix)},
                }),
                json.dumps({request.operation_id: {"request": request.model_dump(), "state": "invoking"}}),
            ),
        )
        manager.db.execute_write(
            "INSERT INTO acquisition_launch_gates VALUES (?,?,'cli',?,?,'retained-recovery')",
            ("host", f"retained-recovery:{request.operation_id}", session.session_id, NATIVE),
        )
        monkeypatch.setattr(creation_writer_inspection, "_instance", lambda: {
            "installation_root": request.bridge_installation,
        })
        if completion == "historical":
            def legacy_record(original: Any, document: dict[str, Any]) -> None:
                original.accepted_load = document
            monkeypatch.setattr(acceptance, "record", legacy_record)
        manager._set_background_recovery_enabled(session, False)
        if completion == "config-crash":
            event_log.append("startup_progress", {"phase": "native-session-load", "state": "finished"})
        session.event_log.append(settlement.START_EVENT, {"version": 1, "request": request.model_dump()})
    owner.hosted_epoch = acceptance.epoch()
    client = None
    try:
        def progress_event(name: str, data: dict[str, Any]) -> None:
            if completion == "config-crash" and data.get("phase") in {"native-session-load", "resume"} and (
                data.get("state") in {"finished", "failed"}
            ):
                return
            event_log.append(name, data)

        progress = startup_progress.StartupProgress(progress_event)
        token = startup_progress._active.set(progress)
        progress.update("resume", "started")
        failed_config = configured and completion != "configured"
        try:
            from agent_bridge.acp_client import AcpClient
            apply = AcpClient._apply_model_config

            async def fail_configuration(self: Any, *args: Any, **kwargs: Any) -> None:
                assert owner.accepted_load is not None
                raise RuntimeError("fixture frontend lost after durable ACK")

            with monkeypatch.context() as startup:
                if failed_config and completion != "config-failed" and not retained_frontend:
                    startup.setattr(AcpClient, "_apply_model_config", fail_configuration)
                try:
                    if retained_frontend:
                        from agent_bridge import retained_session_recovery as recovery
                        recovery_token = recovery._current.set(owner)
                        try:
                            await manager.resume_session(
                                session.session_id, drain=False, allow_recreate=False,
                                owner_native_resume=True, recovery_operation=owner,
                            )
                        finally:
                            recovery._current.reset(recovery_token)
                        raise AssertionError("Original configuration must fail before recovery")
                    else:
                        client, native = await manager._connect_via_session_host(
                            target, tracker=ConnectTracker(), session_id=session.session_id,
                            on_acp_event=lambda event, data: manager._route_acp_event(session, event, data),
                            permission_callback=None, load_session_id=NATIVE, recovery_operation=owner,
                        )
                except RuntimeError as exc:
                    assert failed_config
                    assert (
                        "timed out" in str(exc) if completion.startswith("config-timeout")
                        else "required model/effort was not verified" in str(exc) if completion in {
                            "config-failed", "config-retained", "config-unqualified-client",
                        }
                        else str(exc).endswith("fixture frontend lost after durable ACK")
                    )
                    client, native = session.client, NATIVE
            assert AcpClient._apply_model_config is apply
        finally:
            # Fault after the actual ACK, before the caller can finish registration.
            progress.update("resume", "failed")
            startup_progress._active.reset(token)
        assert native == NATIVE and owner.accepted_load is not None and client is not None
        activation = acceptance.registration(owner, client)
        child = activation["child"]["pid"]
        assert not session_liveness.session_process_owns_hold(
            NATIVE, child, activation["child"]["start_time"],
        )
        if completion != "direct":
            if completion == "committed":
                assert worktree_lineage.register_session(
                    "host", NATIVE, pid=child, worktree_dir=str(checkout), hosted_activation=activation,
                )
            manager._set_background_recovery_enabled(session, False)
            if not retained_frontend or completion == "config-timeout-restart":
                await client.shutdown()
                client, session.client = None, None
            else:
                assert session.client is client and session.status == SessionStatus.STOPPED
            owner.active = False
            manager.db.execute_write(
                "UPDATE workspace_embodiments SET creation_recoveries_json=? WHERE key_digest=?",
                (json.dumps({request.operation_id: {"request": request.model_dump(), "state": "unknown"}}),
                 request.journal_key),
            )
            if completion in {"restart", "config-timeout-restart"}:
                index = manager._host_index
                assert index.release(session.session_id, manager._generation_id)
                previous_session = session
                manager = type(manager)(
                    manager.db, session_host_state_dir=str(index._path.parent),
                )
                session = manager.get_session(previous_session.session_id)
                assert session is not None and session is not previous_session and session.client is None
            original_events = manager.db.get_events(session.session_id)
            if completion == "config-unqualified-client":
                owner.socket = object()
                with pytest.raises(AcquisitionRefused, match="unqualified frontend"):
                    await settlement.inspect(manager, session.session_id, request.operation_id, lambda: True)
                assert client is not None and session.client is client and client.is_running
                assert manager.db.execute_read("SELECT * FROM acquisition_launch_gates")
                return
            if completion.startswith("config-timeout"):
                assert any(item["event_type"] == "acp_launch_timeout" for item in original_events)
            if completion in {"config-stop", "config-activity"}:
                route = manager._route_acp_event

                def concurrent_activity(current: Any, name: str, data: dict[str, Any]) -> None:
                    route(current, name, data)
                    if name == "preference_resolution":
                        if completion == "config-stop":
                            manager.db.execute_write(
                                "UPDATE sessions SET cold_recovery_stop_token='config-stop' WHERE id=?",
                                (session.session_id,),
                            )
                        else:
                            current.client._emit("user_message", {"text": "concurrent fixture activity"})

                lineage = path.read_bytes()
                with monkeypatch.context() as held:
                    held.setattr(manager, "_route_acp_event", concurrent_activity)
                    with pytest.raises(AcquisitionRefused):
                        await settlement.inspect(manager, session.session_id, request.operation_id, lambda: True)
                if completion == "config-activity":
                    persisted = manager.db.get_events(session.session_id)
                    assert sum(item["event_type"] == "user_message" for item in persisted) == 1
                    with pytest.raises(AcquisitionRefused, match="conflicting subsequent activity"):
                        await settlement.inspect(manager, session.session_id, request.operation_id, lambda: True)
                    assert manager.db.get_events(session.session_id) == persisted
                assert session.client is None and session.status == SessionStatus.STOPPED
                assert manager.db.execute_read("SELECT * FROM acquisition_launch_gates")
                assert path.read_bytes() == lineage and manager._host_index.get(session.session_id) is not None
                messages = [json.loads(line) for line in transcript.read_text().splitlines()]
                assert [item["method"] for item in messages if "method" in item] == [
                    "initialize", "session/load", "initialize",
                ]
                return
            if completion == "config-missing":
                accepted = next(item for item in original_events if item["event_type"] == acceptance.EVENT)
                accepted["data"].pop("configuration")
                manager.db.execute_write(
                    "UPDATE events SET data_json=? WHERE session_id=? AND event_id=?",
                    (json.dumps(accepted["data"]), session.session_id, accepted["event_id"]),
                )
                with pytest.raises(AcquisitionRefused, match="pending configuration response"):
                    await settlement.inspect(manager, session.session_id, request.operation_id, lambda: True)
                assert session.client is None and manager.db.execute_read("SELECT * FROM acquisition_launch_gates")
                messages = [json.loads(line) for line in transcript.read_text().splitlines()]
                assert [item["method"] for item in messages if "method" in item] == [
                    "initialize", "session/load", "initialize",
                ]
                return
            if completion in {"stop", "foreign", "raced-stop"}:
                if completion == "stop":
                    manager.db.execute_write(
                        "UPDATE sessions SET cold_recovery_stop_token='operator-stop' WHERE id=?",
                        (session.session_id,),
                    )
                elif completion == "foreign":
                    index = manager._host_index
                    assert index.release(session.session_id, manager._generation_id)
                    index.claim(
                        session.session_id, generation="foreign", owner_pid=os.getpid(),
                        pid_alive=lambda _pid: True,
                    )
                else:
                    import agent_remote_driver.session_host.inspection as inspection

                    probe = inspection.probe
                    calls = 0

                    async def stopped(*args: Any, **kwargs: Any) -> Any:
                        nonlocal calls
                        result = await probe(*args, **kwargs)
                        calls += 1
                        if calls == 2:
                            manager.db.execute_write(
                                "UPDATE sessions SET cold_recovery_stop_token='raced-stop' WHERE id=?",
                                (session.session_id,),
                            )
                        return result
                    monkeypatch.setattr(inspection, "probe", stopped)
                lineage = path.read_bytes()
                original_row = manager.db.execute_read("SELECT * FROM workspace_embodiments")
                gates = manager.db.execute_read("SELECT * FROM acquisition_launch_gates")
                reaper = Mock(side_effect=AssertionError("Unknown original Host must not be reaped"))
                with monkeypatch.context() as held:
                    held.setattr(manager, "_reap_host_record", reaper)
                    with pytest.raises(AcquisitionRefused):
                        await settlement.inspect(manager, session.session_id, request.operation_id, lambda: True)
                assert path.read_bytes() == lineage
                assert manager.db.execute_read("SELECT * FROM workspace_embodiments") == original_row
                assert manager.db.execute_read("SELECT * FROM acquisition_launch_gates") == gates
                assert session.client is None and session.status == SessionStatus.STOPPED
                assert manager._host_index.get(session.session_id) is not None
                messages = [json.loads(line) for line in transcript.read_text().splitlines()]
                assert [item["method"] for item in messages if "method" in item].count("session/load") == 1
                assert not manager.db.execute_read("SELECT * FROM turns")
                return
            if completion == "lost-response":
                register = worktree_lineage.register_session
                lineage = path.read_bytes()

                def committed_response_lost(*args: Any, **kwargs: Any) -> bool:
                    assert register(*args, **kwargs)
                    return False

                with monkeypatch.context() as lost:
                    lost.setattr(worktree_lineage, "register_session", committed_response_lost)
                    with pytest.raises(AcquisitionRefused, match="registration remains"):
                        await settlement.inspect(manager, session.session_id, request.operation_id, lambda: True)
                assert session.client is None and path.read_bytes() != lineage
                committed = path.read_bytes()
                assert manager.db.execute_read("SELECT * FROM acquisition_launch_gates")
            result = await settlement.inspect(
                manager, session.session_id, request.operation_id, lambda: True,
            )
            client = session.client
            assert result["state"] == "complete" and client is not None
            assert result == await settlement.inspect(
                manager, session.session_id, request.operation_id, lambda: True,
            )
            final_events = manager.db.get_events(session.session_id)
            assert final_events[:len(original_events)] == original_events
            if failed_config:
                assert client._preferences_ready
                assert client.confirmed_preferences == {"model": "original-model", "reasoning_effort": "high"}
                assert any(item["event_type"] == "preference_selected" for item in final_events[len(original_events):])
            else:
                assert final_events == original_events
            assert session.status == SessionStatus.IDLE
            assert not manager.db.execute_read("SELECT * FROM acquisition_launch_gates")
            if completion == "lost-response":
                assert path.read_bytes() == committed
        else:
            assert worktree_lineage.register_session(
                "host", NATIVE, pid=child, worktree_dir=str(checkout), hosted_activation=activation,
            )
        registered = path.read_bytes()
        assert worktree_lineage.register_session(
            "host", NATIVE, pid=child, worktree_dir=str(checkout), hosted_activation=activation,
        )
        assert path.read_bytes() == registered
        after = tracking.load_record(path)
        entry = after.session_entry(NATIVE)
        previous = before.session_entry(NATIVE)
        assert previous is not None
        assert entry is not None and len(entry.activations) == 2
        assert entry.activations[0] == previous.activations[0]
        assert entry.activations[1].start_source == "retained-hosted-acp"
        assert entry.activations[1].ended_at is None and entry.pid == child
        assert after.creation_nonce == before.creation_nonce
        assert not (state / "resident-calls").exists()
        original_host = manager._host_index.get(session.session_id)
        if completion == "direct":
            assert client is not None
            manager._set_background_recovery_enabled(session, False)
            await client.shutdown()
            session.client = None
            assert await manager._reattach_one(
                original_host, session, new_status=SessionStatus.IDLE,
            )
            client = manager.get_session(session.session_id).client
        assert client is not None
        assert client.acp_session_id == NATIVE
        assert acceptance.host_binding(manager._host_index.get(session.session_id)) == acceptance.host_binding(
            original_host,
        )
        assert path.read_bytes() == registered
        messages = [json.loads(line) for line in transcript.read_text().splitlines()]
        assert sum("boot" in item for item in messages) == 1
        assert [item["method"] for item in messages if "method" in item] == (
            ["initialize", "session/load"]
            + (["session/set_config_option"] * 2 if configured and (
                not failed_config or completion == "config-failed" or retained_frontend
            ) else [])
            + ["initialize"]
            + (["session/set_config_option"] * 2 if failed_config else [])
            + (["initialize"] if completion == "lost-response" else [])
        )
        assert not manager.db.execute_read("SELECT * FROM turns")
    finally:
        client = client or session.client
        if client is not None:
            await client.shutdown()
        for host in manager._host_index.all():
            manager._reap_host_record(host, "owned hosted-registration fixture cleanup")


@pytest.mark.asyncio
@pytest.mark.parametrize("start_failure", [False, True])
async def test_retained_reattach_does_not_reap_an_unconfirmed_original_child(
    session_manager: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, start_failure: bool,
) -> None:
    from types import SimpleNamespace
    from agent_bridge.session_host.host_index import HostRecord

    manager = session_manager
    target = SpawnTarget(type="local", cwd=str(tmp_path))
    session = Session("retained", "Original", target)
    session.acp_session_id, session.status = NATIVE, SessionStatus.STOPPED
    manager._sessions[session.session_id] = session
    manager.db.create_session(
        session.session_id, session.name, None, target.cwd, "local", "stopped", time.time(),
        acp_session_id=NATIVE, target_json=target.to_json(),
    )
    host = HostRecord(
        session_id=session.session_id, host_pid=90000001, child_pid=90000002,
        port=1234, nonce="fixture-nonce",
    )
    manager._host_index.register(host)
    before = manager._host_index._path.read_bytes()
    socket = SimpleNamespace(attach=AsyncMock(return_value=SimpleNamespace(child_pid=host.child_pid)),
                             close=AsyncMock())
    streams = SimpleNamespace(
        reader=object(), writer=object(), child_exit_code=9, aclose=AsyncMock(),
    )
    client = Mock()
    client.start_streams = AsyncMock(side_effect=RuntimeError("child exited") if start_failure else None)
    client.shutdown = AsyncMock()
    monkeypatch.setattr("agent_remote_driver.session_host.client.SessionHostClient.connect",
                        AsyncMock(return_value=socket))
    monkeypatch.setattr("agent_bridge.session_host.acp_adapter.open_acp_streams",
                        AsyncMock(return_value=streams))
    monkeypatch.setattr("agent_bridge.session_manager.AcpClient", Mock(return_value=client))
    reaper = Mock(side_effect=AssertionError("Retained unknown custody cannot be reaped"))
    monkeypatch.setattr(manager, "_reap_host_record", reaper)
    assert not await manager._reattach_one(
        host, session, new_status=SessionStatus.STOPPED, retained_check=Mock(),
    )
    assert session.client is None and session.status == SessionStatus.STOPPED
    assert manager._host_index._path.read_bytes() == before
    reaper.assert_not_called()
    socket.close.assert_awaited()
    client.adopt_session.assert_not_called()
