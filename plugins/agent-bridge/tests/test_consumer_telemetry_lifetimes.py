"""Routing and cleanup regressions through existing Bridge ownership seams."""

from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager, nullcontext
import io
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any, cast

from fastapi import FastAPI
import pytest
from zdd.private_protocol import Deadline, PrivateIPCError, Refusal

from agent_bridge import consumer_telemetry, telemetry
from agent_bridge.agent_registry_resolver import AgentResolver
from agent_bridge.db import Database
from agent_bridge.effective_runtime import RuntimeOrigins, ServingRuntimeCapture
from agent_bridge.events import EventLog
from agent_bridge.models import ServiceConfig
from agent_bridge.runtime_restore import RestoredRuntime, restore_runtime
from agent_bridge.session_runtime import session_manager_from_config
from process_telemetry import SinkSlot, command
from process_telemetry.bridge_projection import offered_fields


def declare(root: Path, *, active: bool = False) -> None:
    directory = root / ".agent-telemetry"
    directory.mkdir()
    value: dict[str, Any] = {"schema_version": 1, "mode": "disabled"}
    if active:
        value.update(mode="command", command={"argv": [
            sys.executable, "-I", "-c", "import sys; sys.stdin.buffer.read()",
        ]})
    (directory / "config.json").write_text(json.dumps(value), encoding="utf-8")


@pytest.mark.parametrize("mode", ["legacy", "bound-empty", "command"])
def test_elevated_seed_does_not_inherit_ordinary_command_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    from agent_bridge import elevated
    from agent_bridge.config import load_config, save_config

    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path / "primary"))
    admitted = None if mode == "legacy" else {} if mode == "bound-empty" else {
        "caller": str(tmp_path),
    }
    cfg = ServiceConfig(consumer_telemetry_harnesses=admitted)
    save_config(cfg)
    directory = elevated._seed_config(elevated.ELEVATED_PORT)
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(directory))
    restored = load_config(strict=True)
    assert restored.consumer_telemetry_harnesses == (None if admitted is None else {})
    assert cfg.consumer_telemetry_harnesses == admitted


def test_receiver_can_persist_final_batch_after_stdin_eof(tmp_path: Path) -> None:
    directory = tmp_path / ".agent-telemetry"
    directory.mkdir()
    code = (
        "import os,pathlib,sys,time;"
        "data=sys.stdin.buffer.readline();pathlib.Path('ready').touch();"
        "data+=sys.stdin.buffer.read();time.sleep(.05);"
        "output=open('final.jsonl','wb');output.write(data);output.flush();"
        "os.fsync(output.fileno());output.close()"
    )
    (directory / "config.json").write_text(json.dumps({
        "schema_version": 1, "mode": "command",
        "command": {"argv": [sys.executable, "-I", "-c", code]},
    }), encoding="utf-8")
    owner = SinkSlot().configure_commands()
    binding = owner.admit(str(tmp_path))
    try:
        deadline = time.monotonic() + 4
        while owner.stats()["enqueued"] == 0 and time.monotonic() < deadline:
            binding.offer({
                "kind": "event", "name": "session_health", "event": "context_warning",
            })
            time.sleep(.005)
        while not (tmp_path / "ready").exists() and time.monotonic() < deadline:
            time.sleep(.005)
        assert (tmp_path / "ready").exists(), (binding.state, owner.stats())
    finally:
        owner.close()
    record = json.loads((tmp_path / "final.jsonl").read_text(encoding="utf-8"))
    assert record["source_schema"] == "bridge.lifecycle.v1"
    assert record["sequence"] > 0
    assert binding.pipe is not None and binding.pipe.closed
    assert owner.writer is not None and owner.writer.join(0)


def test_retained_windows_startup_failure_closes_its_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_procutil import _windows_caller_owned, command_pipe
    from agent_procutil.owned_process import OwnedCleanupError

    stream = io.BytesIO()
    retired: list[bool] = []
    process = SimpleNamespace(
        stdin=stream, wait=lambda **kwargs: 0,
        close=lambda: retired.append(stream.closed),
    )
    failure = OwnedCleanupError(cast(Any, process))

    def refused(*args: Any, **kwargs: Any) -> Any:
        raise failure

    monkeypatch.setattr(_windows_caller_owned, "spawn", refused)
    monkeypatch.setattr(command_pipe, "os", SimpleNamespace(name="nt"))
    pipe = command_pipe.CommandPipe()
    with pytest.raises(OwnedCleanupError):
        pipe.start(["fixture"], "fixture", {})
    assert pipe.failure is failure and not stream.closed
    pipe.close(time.monotonic() + 1)
    assert pipe.closed and stream.closed
    assert retired == [True]


def test_windows_member_exit_confirmation_retains_custody_until_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_procutil import _windows_caller_owned, command_pipe

    retired: list[bool] = []
    job = object()
    process = SimpleNamespace(
        wait=lambda **kwargs: 0, close=lambda: retired.append(True),
    )
    attempts: list[float] = []

    def retire(exact_job: Any, timeout: float) -> None:
        assert exact_job is job and 0 < timeout <= 1
        attempts.append(timeout)
        if len(attempts) == 1:
            raise TimeoutError("fixture descendant still owns a process handle")

    monkeypatch.setattr(_windows_caller_owned, "_retire_job", retire)
    monkeypatch.setattr(command_pipe, "os", SimpleNamespace(name="nt"))
    pipe = command_pipe.CommandPipe()
    pipe.process, pipe.job = process, job
    with pytest.raises(TimeoutError):
        pipe.close(time.monotonic() + 1)
    assert pipe.job is job and pipe.process is process and not pipe.closed
    assert retired == []
    pipe.close(time.monotonic() + 1)
    assert pipe.closed and pipe.job is None and retired == [True]
    assert len(attempts) == 2


def test_concurrent_offer_is_nonblocking_and_preserves_wire_sequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = threading.Event(), threading.Event()
    records: list[dict[str, Any]] = []
    first = True

    def filter_event(event: dict[str, Any]) -> dict[str, Any]:
        nonlocal first
        if first:
            first = False
            entered.set()
            assert release.wait(2)
        return offered_fields(event)

    monkeypatch.setattr(command.CommandRoutes, "_write", lambda self, row: records.append(row))
    declare(tmp_path, active=True)
    owner = SinkSlot().configure_commands()
    monkeypatch.setattr(owner, "_offered_fields", filter_event)
    binding = owner.admit(str(tmp_path))
    assert owner.writer is not None
    writer_ready, writer_release = threading.Event(), threading.Event()

    def idle_writer() -> None:
        writer_ready.set()
        assert writer_release.wait(2)

    # Isolate producer contention from the buffer's independent dequeue/drop seam.
    monkeypatch.setattr(owner.writer, "_on_iteration", idle_writer)
    event = {"kind": "event", "name": "session_health", "event": "context_warning"}
    producer = threading.Thread(target=binding.offer, args=(event,))
    try:
        assert writer_ready.wait(2)
        producer.start()
        assert entered.wait(2)
        before = time.monotonic()
        binding.offer(event)
        assert time.monotonic() - before < .25
        release.set()
        producer.join(2)
        assert not producer.is_alive()
        binding.offer(event)
        writer_release.set()
        deadline = time.monotonic() + 2
        while len(records) < 2 and time.monotonic() < deadline:
            time.sleep(.005)
    finally:
        release.set()
        writer_release.set()
        if producer.ident is not None:
            producer.join(2)
        owner.close()
    assert [row["sequence"] for row in records] == [1, 3]
    assert owner.stats()["dropped_full"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("arrival", ["rehydration", "remote"])
async def test_later_accepted_session_keeps_admitted_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arrival: str,
) -> None:
    from agent_bridge import remote_acp_sessions
    from agent_bridge.remote_acp_config import RemoteAcpTarget

    declare(tmp_path)
    db = Database(tmp_path / "later.db")
    target = RemoteAcpTarget.model_validate({
        "endpoint": "https://example.com/acp", "transport": "streamable-http",
        "auth": {"source": "git-credential", "identity": "example"},
    })
    manager = session_manager_from_config(
        db, ServiceConfig(
            consumer_telemetry_harnesses={"caller": str(tmp_path)},
            remote_acp={"example": target},
        ),
        session_host_state_dir=str(tmp_path / "hosts"),
    )
    try:
        if arrival == "rehydration":
            db.create_session(
                session_id="later", name="fixture", agent_name="fixture", caller_id="caller",
                target_dir=str(tmp_path), target_type="local", status="stopped", now=time.time(),
            )
            early_log = EventLog(db=db, session_id="later")
            early_log.append("session_state_changed", {"status": "starting"})
            assert not hasattr(early_log, "_consumer_telemetry_binding")
            manager._rehydrate(merge=True)
            session = manager.get_session("later")
            assert session is not None
            session.event_log = early_log
        else:
            async def connected(_manager: Any, session: Any, *args: Any) -> Any:
                session.event_log.append("session_state_changed", {"status": "idle"})
                return session

            monkeypatch.setattr(remote_acp_sessions, "_connect_remote_client", connected)
            session = await remote_acp_sessions.connect_remote_session(
                manager, "example", cwd=str(tmp_path), caller_id="caller",
            )
        assert session.event_log is not None
        session.event_log.append("session_state_changed", {"status": "idle"})
        admitted = manager._consumer_telemetry.bindings["caller"]
        assert getattr(session, "_consumer_telemetry_binding") is admitted
        assert session.event_log._consumer_telemetry_binding is admitted
        session.caller_id = "unlisted"
        consumer_telemetry.bind(manager, session)
        session.event_log = EventLog(db=db, session_id=session.session_id)
        session.event_log.append("session_state_changed", {"status": "stopped"})
        assert session.event_log._consumer_telemetry_binding is admitted
    finally:
        consumer_telemetry.close(manager)
        db.close()


def test_writable_activation_preserves_bound_cold_log_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge import runtime_transfer

    legacy: list[dict[str, Any]] = []
    telemetry.set_telemetry_sink(legacy.append)
    old = Database(tmp_path / "old.db")
    writable = Database(tmp_path / "writable.db")
    manager = session_manager_from_config(
        old, ServiceConfig(consumer_telemetry_harnesses={}),
        session_host_state_dir=str(tmp_path / "hosts"),
    )
    app = FastAPI()
    app.state.db, app.state.session_manager = old, manager
    pending = SimpleNamespace(
        prepared=True, verify_candidate=lambda app: None, _event_logs=(),
    )
    monkeypatch.setattr(runtime_transfer, "open_existing_writable", lambda source: writable)
    try:
        runtime_transfer.PendingRuntime.activate_resources(cast(Any, pending), app, Deadline(1))
        assert app.state.db is writable
        assert manager._db is writable
        EventLog(db=writable, session_id="cold").append(
            "session_state_changed", {"status": "idle"},
        )
        assert legacy == []
        owner = manager._consumer_telemetry.owner
        assert owner is not None and owner.stats()["dropped_route"] > 0
    finally:
        consumer_telemetry.close(manager)
        telemetry.clear_telemetry_sink()
        writable.close()
        old.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["normal", "build-failure", "cancelled"])
async def test_candidate_lifetime_settles_optional_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    from agent_bridge.restored_app import create_restored_app

    declare(tmp_path, active=True)
    db = Database(tmp_path / "candidate.db")
    cfg = ServiceConfig(
        db_path=str(db.db_path), enable_credential_relay=False,
        consumer_telemetry_harnesses={"caller": str(tmp_path)},
    )
    manager = session_manager_from_config(
        db, cfg, session_host_state_dir=str(tmp_path / "hosts"),
    )
    resolver = AgentResolver({}, {})
    manager.set_resolver(resolver)
    original_app = FastAPI()
    original_app.state.config, original_app.state.db = cfg, db
    original_app.state.session_manager, original_app.state.resolver = manager, resolver
    original_app.state.auth_token = "fixture-auth"
    original_app.state.ready = original_app.state.topology_ready = True
    original_app.state.credential_relay = None
    origins = RuntimeOrigins(
        tmp_path / "config", tmp_path / "install", Path(__file__).absolute(), False, 0, (),
    )
    runtime = restore_runtime(ServingRuntimeCapture(
        original_app, origins, include_resolver=True, include_relay=True,
    ).capture().private_payload())
    consumer_telemetry.close(manager)
    db.close()
    owners: list[Any] = []
    original_build = RestoredRuntime.build_manager

    def build(self: RestoredRuntime, database: Database) -> Any:
        result = original_build(self, database)
        owner = result._consumer_telemetry.owner
        owners.append(owner)
        result._consumer_telemetry.bindings["caller"].offer({
            "kind": "event", "name": "session_health", "event": "context_warning",
        })
        if outcome == "build-failure":
            raise PrivateIPCError(Refusal.AUTHORITY)
        return result

    monkeypatch.setattr(RestoredRuntime, "build_manager", build)
    app = create_restored_app(runtime)

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            if outcome == "cancelled":
                task = asyncio.current_task()
                assert task is not None
                task.cancel()
                await asyncio.sleep(0)

    expectation: AbstractContextManager[Any] = (
        pytest.raises(PrivateIPCError) if outcome == "build-failure"
        else pytest.raises(asyncio.CancelledError) if outcome == "cancelled"
        else nullcontext()
    )
    with expectation:
        await asyncio.create_task(exercise())
    assert len(owners) == 1
    owner = owners[0]
    assert owner.closed and owner.writer.join(0)
    assert all(binding.pipe is None or binding.pipe.closed for binding in owner.bindings)
