"""Focused declaration/projection controls and contained real Bridge witnesses."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
from contextlib import asynccontextmanager, nullcontext
from collections.abc import Callable
from typing import Any, cast
from collections.abc import AsyncIterator

import pytest

from agent_bridge.db import Database
from agent_bridge.models import ServiceConfig
from agent_bridge.session_status import SessionStatus
from agent_bridge.session_runtime import session_manager_from_config
from process_telemetry import SinkSlot
from process_telemetry.bridge_projection import offered_fields, project
from process_telemetry.command_config import CAPABILITY, select, supports


def declaration(root: Path, value: dict[str, Any]) -> None:
    directory = root / ".agent-telemetry"
    directory.mkdir(exist_ok=True)
    (directory / "config.json").write_text(json.dumps(value), encoding="utf-8")


def configured(root: Path, argv: list[str]) -> None:
    declaration(root, {"schema_version": 1, "mode": "command", "command": {"argv": argv}})


@pytest.mark.parametrize("value", [
    {"schema_version": True, "mode": "disabled"},
    {"schema_version": 2, "mode": "disabled"},
    {"schema_version": 1, "mode": "disabled", "command": {}},
    {"schema_version": 1, "mode": "command", "command": {"argv": []}},
    {"schema_version": 1, "mode": "command", "command": {"argv": ["x\0"]}},
    {"schema_version": 1, "mode": "command", "command": {"argv": ["x" * 513]}},
    {"schema_version": 1, "mode": "disabled", "unknown": None},
])
def test_invalid_declarations_never_start_a_writer(tmp_path: Path, value: dict[str, Any]) -> None:
    declaration(tmp_path, value)
    owner = SinkSlot().configure_commands()
    binding = owner.admit(str(tmp_path))
    binding.offer({"kind": "event"})
    assert binding.state == "invalid_config"
    assert owner.writer is None
    owner.close()


def test_opened_bounds_duplicates_and_local_precedence(tmp_path: Path) -> None:
    directory = tmp_path / ".agent-telemetry"
    directory.mkdir()
    shared = directory / "config.json"
    local = directory / "config.local.json"
    shared.write_bytes(b'{"schema_version":1,"schema_version":1,"mode":"disabled"}')
    assert select(tmp_path).state == "invalid_config"
    shared.write_bytes(b" " * 16385)
    assert select(tmp_path).state == "invalid_config"
    local.write_text('{"schema_version":1,"mode":"disabled"}', encoding="utf-8")
    assert select(tmp_path).state == "disabled"
    local.unlink()
    configured(tmp_path, [sys.executable, "-c", "pass"])
    local.write_text(json.dumps({
        "schema_version": 1, "mode": "command", "command": {"argv": [sys.executable]},
    }), encoding="utf-8")
    assert select(tmp_path).state == "override_refused"
    shared_value = json.loads(shared.read_text())
    shared_value["allow_local_override"] = True
    shared.write_text(json.dumps(shared_value))
    assert select(tmp_path).argv == (sys.executable,)
    local.write_bytes(b"{")
    assert select(tmp_path).state == "invalid_config"


def test_projection_rebuild_privacy_and_strict_values() -> None:
    record: dict[str, Any] = {
        "kind": "event", "name": "event_log", "event": "log_rebuilt",
        "from": "private-epoch", "to": "private-epoch", "session_id": "private-id",
        "event_count": 0, "session_state": None, "conversation_state": None,
    }
    assert project(record) == {
        "kind": "event", "name": "event_log", "event": "log_rebuilt", "event_count": 0,
    }
    record.update(event_count=2, session_state="idle", conversation_state="end-turn")
    assert project(record)["session_state"] == "idle"
    record["session_state"] = "arbitrary-sensitive-status"
    with pytest.raises(ValueError):
        project(record)
    record["session_state"] = None
    record["event_count"] = True
    with pytest.raises(ValueError):
        project(record)


def test_executable_path_and_environment_are_bound_not_ambient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "admitted"
    root.mkdir()
    binaries = root / "bin"
    binaries.mkdir()
    product = tmp_path / "product"
    product.mkdir()
    name = "receiver.exe" if os.name == "nt" else "receiver"
    for directory in (binaries, product):
        binary = directory / name
        binary.write_bytes(b"MZfixture")
        binary.chmod(0o700)
    configured(root, [name])
    monkeypatch.chdir(product)
    selection = select(root, environment={
        "PATH": "bin", "systemroot": "os-prerequisite", "TEST_SECRET": "excluded",
    })
    assert selection.state == "active"
    assert selection.argv[0] == str((binaries / name).resolve())
    env = dict(selection.environment)
    assert "TEST_SECRET" not in env
    assert env.get("SystemRoot") == ("os-prerequisite" if os.name == "nt" else None)


def test_unknown_values_and_rebuild_epochs_are_not_snapshotted() -> None:
    from process_telemetry.buffer import snapshot

    record: dict[str, Any] = {
        "kind": "event", "name": "event_log", "event": "log_rebuilt",
        "event_count": 1000, "from": object(), "to": object(),
        "active_tool_call_ids": ["private-id"] * 10000,
        "unknown": object(),
    }
    copied = snapshot(offered_fields(record))
    assert project(copied) == {
        "kind": "event", "name": "event_log", "event": "log_rebuilt", "event_count": 1000,
    }


def test_executable_symlink_preserves_receiver_environment_context(
    tmp_path: Path, native_file_symlink: Callable[..., None],
) -> None:
    target = tmp_path / "base.exe"
    target.write_bytes(b"MZfixture")
    target.chmod(0o700)
    invoked = tmp_path / "environment.exe"
    native_file_symlink(invoked, target)
    configured(tmp_path, [str(invoked)])
    selection = select(tmp_path)
    assert selection.state == "active"
    assert selection.argv[0] == str(invoked)


def test_receiver_creation_failure_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_procutil.command_pipe import CommandPipe

    calls: list[int] = []

    def refused(
        self: CommandPipe, argv: list[str], cwd: str, env: dict[str, str],
    ) -> None:
        calls.append(threading.get_ident())
        raise OSError("fixture creation refusal")

    monkeypatch.setattr(CommandPipe, "start", refused)
    configured(tmp_path, [sys.executable, "-I", "-c", "pass"])
    slot = SinkSlot()
    owner = slot.configure_commands()
    binding = owner.admit(str(tmp_path))
    event = {"kind": "event", "name": "session_health", "event": "context_warning"}
    try:
        deadline = time.monotonic() + 2
        while owner.stats()["start_failed"] == 0 and time.monotonic() < deadline:
            slot.emit(event, binding=binding)
            time.sleep(.01)
        for _ in range(10):
            slot.emit(event, binding=binding)
        assert binding.state == "start_failed"
        assert owner.stats()["start_failed"] == 1
        assert len(calls) == 1 and calls[0] != threading.get_ident()
    finally:
        owner.close()


def test_native_command_disappearing_after_admission_never_retargets(tmp_path: Path) -> None:
    executable = tmp_path / "vanishing.exe"
    executable.write_bytes(b"MZfixture")
    executable.chmod(0o700)
    configured(tmp_path, [str(executable)])
    slot = SinkSlot()
    legacy: list[dict[str, Any]] = []
    slot.set(legacy.append)
    owner = slot.configure_commands()
    binding = owner.admit(str(tmp_path))
    assert binding.state == "active"
    executable.unlink()
    event = {"kind": "event", "name": "session_health", "event": "context_warning"}
    try:
        deadline = time.monotonic() + 4
        while binding.state == "active" and time.monotonic() < deadline:
            slot.emit(event, binding=binding)
            time.sleep(.025)
        assert binding.state in ("start_failed", "write_failed", "deadline_exceeded")
        for _ in range(10):
            slot.emit(event, binding=binding)
        assert legacy == []
        stats = owner.stats()
        assert stats["start_failed"] + stats["write_failed"] + stats["receiver_exited"] == 1
    finally:
        owner.close()
    assert owner.writer is not None and owner.writer.join(0)
    assert binding.pipe is not None and binding.pipe.closed


def test_bound_disabled_does_not_inherit_legacy(tmp_path: Path) -> None:
    slot = SinkSlot()
    legacy: list[dict[str, Any]] = []
    slot.set(legacy.append)
    owner = slot.configure_commands()
    missing = owner.admit(str(tmp_path))
    slot.emit({"event": "legacy"})
    slot.emit({"event": "bound"}, binding=missing)
    slot.emit({"event": "unknown"}, binding=None)
    assert legacy == [{"event": "legacy"}]
    assert missing.state == "unconfigured"
    assert owner.writer is None
    assert owner.stats()["dropped_route"] == 1
    owner.close()


def test_factory_marks_origin_unavailable_before_constructor_events(
    tmp_path: Path,
) -> None:
    from agent_bridge import telemetry
    from agent_bridge.consumer_telemetry import close
    from agent_bridge.events import EventLog

    legacy: list[dict[str, Any]] = []
    telemetry.set_telemetry_sink(legacy.append)
    db = Database(tmp_path / "initialization.db")
    db.create_session(
        session_id="recovering-fixture", name="fixture", agent_name="fixture-agent",
        caller_id="fixture-caller", target_dir=str(tmp_path), target_type="local",
        status="running", now=time.time(),
    )
    manager = session_manager_from_config(
        db, ServiceConfig(consumer_telemetry_harnesses={}),
        session_host_state_dir=str(tmp_path / "hosts"),
    )
    try:
        assert legacy == []
        EventLog(db=db).append("session_state_changed", {"status": "idle"})
        assert legacy == []
        assert manager._consumer_telemetry.owner is not None
        assert manager._consumer_telemetry.owner.stats()["dropped_route"] > 0
        telemetry.emit({"legacy": True})
        assert legacy == [{"legacy": True}]
    finally:
        close(manager)
        telemetry.clear_telemetry_sink()
        db.close()


def test_constructor_failure_closes_optional_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge import session_manager as core

    configured(tmp_path, [sys.executable, "-I", "-c", "pass"])

    def refused(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("fixture construction refusal")

    monkeypatch.setattr(core, "SessionManager", refused)
    db = Database(tmp_path / "failed.db")
    try:
        with pytest.raises(RuntimeError, match="fixture construction refusal"):
            session_manager_from_config(db, ServiceConfig(
                consumer_telemetry_harnesses={"fixture-caller": str(tmp_path)},
            ))
        consumer = getattr(db, "_consumer_telemetry_owner")
        assert consumer.owner.closed
        assert consumer.owner.writer.join(0)
        assert all(binding.pipe is None for binding in consumer.owner.bindings)
    finally:
        db.close()


@pytest.mark.parametrize("malformed", [True, False])
def test_nonstrict_service_loading_preserves_bound_intent(
    tmp_path: Path, malformed: bool,
) -> None:
    import yaml
    from agent_bridge import telemetry
    from agent_bridge.config import config_dir, load_config
    from agent_bridge.consumer_telemetry import close
    from agent_bridge.events import EventLog

    data = {
        "consumer_telemetry_harnesses": [] if malformed else {"fixture-caller": str(tmp_path)},
        "port": 0 if malformed else "invalid-port",
    }
    (config_dir() / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    cfg = load_config()
    assert cfg.consumer_telemetry_harnesses == {}
    assert getattr(cfg, "_consumer_telemetry_invalid")
    legacy: list[dict[str, Any]] = []
    telemetry.set_telemetry_sink(legacy.append)
    db = Database(tmp_path / "fallback.db")
    manager = session_manager_from_config(
        db, cfg, session_host_state_dir=str(tmp_path / "hosts"),
    )
    try:
        EventLog(db=db).append("session_state_changed", {"status": "idle"})
        assert legacy == []
        assert manager._consumer_telemetry.status()["refusal"] == "invalid_config"
    finally:
        close(manager)
        telemetry.clear_telemetry_sink()
        db.close()


def test_represented_and_restored_logs_preserve_explicit_owner_mode(tmp_path: Path) -> None:
    from agent_bridge import telemetry
    from agent_bridge.app_routes import configure_serving_app
    from agent_bridge.live_representation import LiveEventStore
    from agent_bridge.live_runtime import capture_live_events, restore_live_events

    legacy: list[dict[str, Any]] = []
    telemetry.set_telemetry_sink(legacy.append)
    try:
        app = configure_serving_app(
            ServiceConfig(consumer_telemetry_harnesses={}), "fixture", lifespan=None,
        )
        store = app.state.live_event_store
        store.get_or_create("represented-fixture").append(
            "session_state_changed", {"status": "idle"},
        )
        restored = restore_live_events(capture_live_events(store), telemetry_legacy=False)
        log = restored.get("represented-fixture")
        assert log is not None
        log.append("session_state_changed", {"status": "stopped"})
        assert legacy == []
        LiveEventStore().get_or_create("legacy-fixture").append(
            "session_state_changed", {"status": "idle"},
        )
        assert legacy and legacy[0]["session_id"] == "legacy-fixture"
    finally:
        telemetry.clear_telemetry_sink()


@pytest.mark.asyncio
async def test_cancelled_telemetry_cleanup_still_closes_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from fastapi import FastAPI
    from agent_bridge import app as application, app_lifecycle, app_agent_roster

    order: list[str] = []

    @asynccontextmanager
    async def serving(_app: Any) -> AsyncIterator[None]:
        yield

    async def cancelled(*args: Any, **kwargs: Any) -> None:
        order.append("telemetry")
        raise asyncio.CancelledError

    async def database(_db: Any) -> None:
        order.append("database")

    monkeypatch.setattr(app_lifecycle, "admit_daemon_instance", lambda *a, **k: nullcontext())
    monkeypatch.setattr(application, "_lifespan", serving)
    monkeypatch.setattr(app_agent_roster, "stop_agent_roster_cache", AsyncMock())
    monkeypatch.setattr(app_lifecycle, "settled_thread_call", cancelled)
    monkeypatch.setattr(app_lifecycle, "close_database", database)
    app = SimpleNamespace(state=SimpleNamespace(session_manager=object(), db=object()))
    with pytest.raises(asyncio.CancelledError):
        async with app_lifecycle.lifespan(cast(FastAPI, app)):
            pass
    assert order == ["telemetry", "database"]


def test_route_cap_does_not_scan_an_extra_root(monkeypatch: pytest.MonkeyPatch) -> None:
    from process_telemetry import command
    from process_telemetry.command_config import Selection

    scans: list[str] = []

    def selected(root: str) -> Selection:
        scans.append(root)
        return Selection("disabled")

    monkeypatch.setattr(command, "select", selected)
    owner = SinkSlot().configure_commands()
    assert owner.admit(None).state == "origin_unavailable"
    for index in range(4):
        assert owner.admit(str(index)).state == "disabled"
    assert owner.admit("extra").state == "route_limit"
    assert scans == ["0", "1", "2", "3"]
    assert owner.writer is None
    owner.close()


def test_closed_owner_cannot_replace_unsettled_native_custody(tmp_path: Path) -> None:
    from types import SimpleNamespace

    declaration(tmp_path, {"schema_version": 1, "mode": "disabled"})
    slot = SinkSlot()
    owner = slot.configure_commands()
    binding = owner.admit(str(tmp_path))
    binding.pipe = SimpleNamespace(closed=False)
    binding.state = "write_failed"
    owner.closed = True
    with pytest.raises(RuntimeError, match="custody"):
        slot.configure_commands()
    binding.pipe = None
    owner.close()
    replacement = slot.configure_commands()
    replacement.close()


def test_explicit_drain_seals_offers_but_close_still_abandons() -> None:
    from process_telemetry.buffer import BufferedSink

    entered, release = threading.Event(), threading.Event()

    def callback(_record: dict[str, Any]) -> None:
        entered.set()
        assert release.wait(2)

    writer = BufferedSink(callback).start()
    try:
        deadline = time.monotonic() + 1
        while not entered.is_set() and time.monotonic() < deadline:
            writer({"event": "first"})
            entered.wait(.01)
        assert entered.is_set()
        for _ in range(129):
            writer({"event": "queued"})
        assert writer.stats()["full"] > 0
        assert writer.drain(.001) is False
        assert writer.offer({"event": "after-seal"}) == "inactive"
        writer.close()
    finally:
        release.set()
        writer.close()
        assert writer.join(1)
    assert writer.stats()["abandoned"] >= 128


def test_selected_metadata_is_not_payload_or_version_inference(tmp_path: Path) -> None:
    from agent_bridge.consumer_telemetry import metadata_root

    assert supports(metadata_root())
    assert not supports(tmp_path)
    path = tmp_path / "consumer-telemetry-contract.json"
    path.write_text(json.dumps({**CAPABILITY, "schema_version": True}))
    assert not supports(tmp_path)
    path.write_text(json.dumps({**CAPABILITY, "extension": "ignored"}))
    assert supports(tmp_path)
    path.write_bytes(b" " * 4097)
    assert not supports(tmp_path)


_RECEIVER = r'''
import json,os,sys,uuid
os.write(1,b"receiver-output-must-be-discarded\n"*4096)
os.write(2,b"receiver-error-must-be-discarded\n"*4096)
while True:
    line=sys.stdin.buffer.readline(8193)
    if not line:
        break
    assert len(line)<=8192 and line.endswith(b"\n")
    record=json.loads(line)
    assert set(record)=={
        "contract","version","source","source_schema","source_instance",
        "route_generation","sequence","observed_at","event"
    }
    assert record["contract"]=="consumer-telemetry-command"
    assert type(record["version"]) is int and record["version"]==1
    assert record["source"]=="agent-bridge"
    assert record["source_schema"]=="bridge.lifecycle.v1"
    assert type(record["sequence"]) is int and 0<record["sequence"]<=2**53-1
    for key in ("source_instance","route_generation"):
        assert str(uuid.UUID(record[key]))==record[key]
    with open("received.jsonl","ab",buffering=0) as output:
        output.write((json.dumps({
            "pid":os.getpid(),"cwd":os.getcwd(),"literal":sys.argv[1],
            "secret_inherited":"TEST_RECEIVER_SECRET" in os.environ,
            "environment_keys":list(os.environ),
            "record":record
        },ensure_ascii=False)+"\n").encode())
        os.fsync(output.fileno())
'''


@pytest.mark.asyncio
@pytest.mark.parametrize("configured_state", ["command", "disabled", "invalid"])
async def test_real_bridge_stop_reaches_one_neutral_receiver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    record_property: Callable[[str, object], None],
    configured_state: str,
) -> None:
    from fastapi.testclient import TestClient
    from agent_bridge import telemetry
    from agent_bridge.app_routes import configure_serving_app
    from agent_bridge.config import load_config, save_config
    from agent_bridge.consumer_telemetry import close
    from agent_procutil._job_scopes import ENVIRONMENT_KEY
    from process_telemetry.command_config import ENVIRONMENT_KEYS

    root = tmp_path / "harness with spaces"
    root.mkdir()
    (root / "receiver.py").write_text(_RECEIVER, encoding="utf-8")
    literal = "$not-expanded; & literal \u2603"
    configured(root, [sys.executable, "-I", "-B", "receiver.py", literal])
    if configured_state == "disabled":
        declaration(root, {"schema_version": 1, "mode": "disabled"})
    elif configured_state == "invalid":
        (root / ".agent-telemetry" / "config.json").write_bytes(b"{invalid")
    monkeypatch.setenv("TEST_RECEIVER_SECRET", "must-not-reach-child")
    db = Database(tmp_path / "sessions.db")
    for sid in ("fixture-one", "fixture-two"):
        db.create_session(
            session_id=sid, name="fixture", agent_name="fixture-agent",
            caller_id="fixture-caller",
            target_dir=str(tmp_path), target_type="local", status="stopped", now=time.time(),
        )
    db.create_session(
        session_id="fixture-unlisted", name="fixture", agent_name="fixture-agent",
        caller_id="unlisted-caller",
        target_dir=str(tmp_path), target_type="local", status="stopped", now=time.time(),
    )
    legacy: list[dict[str, Any]] = []
    telemetry.set_telemetry_sink(legacy.append)
    cfg = ServiceConfig(
        db_path=str(db.db_path),
        consumer_telemetry_harnesses={"fixture-caller": str(root)},
    )
    save_config(cfg)
    cfg = load_config(strict=True)
    manager = session_manager_from_config(
        db, cfg, session_host_state_dir=str(tmp_path / "hosts"),
    )
    from agent_bridge import cold_outages

    session = manager.get_session("fixture-one")
    assert session is not None
    before_log = session.event_log
    outage = {"replacement_json": None}

    async def recovered(_manager: Any, _session_id: str) -> Any:
        return session

    with monkeypatch.context() as recovery:
        recovery.setattr(cold_outages, "latest", lambda *a, **k: outage)
        recovery.setattr(cold_outages, "_assert_recovery_current", lambda *a, **k: None)
        recovery.setattr(cold_outages, "operation", lambda *a, **k: nullcontext())
        recovery.setattr(cold_outages, "claim", lambda *a, **k: outage)
        await cold_outages.guarded_resume(recovered)(manager, "fixture-one")
    assert session.event_log is not before_log
    app = configure_serving_app(cfg, "fixture-bearer", lifespan=None)
    app.state.session_manager = manager
    app.state.db = db
    other = tmp_path / "unrelated product"
    other.mkdir()
    monkeypatch.chdir(other)
    try:
        # Enter ordinary authenticated HTTP operations, not a telemetry helper,
        # importer or callback. The real stop owner produces the transitions.
        with TestClient(app) as client:
            assert client.post("/api/v1/sessions/fixture-one/stop").status_code == 401
            for sid in ("fixture-one", "fixture-two", "fixture-unlisted"):
                response = client.post(
                    f"/api/v1/sessions/{sid}/stop",
                    headers={"Authorization": "Bearer fixture-bearer"},
                )
                assert response.status_code == 204
                assert response.content == b""
            assert client.get("/health").json()["consumer_telemetry_contract"] == CAPABILITY
        assert legacy == []
        if configured_state != "command":
            assert manager._consumer_telemetry.owner is not None
            assert manager._consumer_telemetry.owner.writer is None
            assert not (root / "received.jsonl").exists()
            return
        output = root / "received.jsonl"
        deadline = time.monotonic() + 6
        rows: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            if output.exists():
                rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            if len(rows) >= 4:
                break
            await asyncio.sleep(.025)
        status = manager._consumer_telemetry.status()
        assert len(rows) == 4, status
        assert len({row["pid"] for row in rows}) == 1
        assert all(Path(row["cwd"]).resolve() == root.resolve() for row in rows)
        assert all(row["literal"] == literal and not row["secret_inherited"] for row in rows)
        allowed = {key.upper() for key in ENVIRONMENT_KEYS} | {"LC_CTYPE"}
        for row in rows:
            keys = {key.upper() for key in row["environment_keys"]}
            assert keys <= allowed
            assert ENVIRONMENT_KEY.upper() not in keys
        records = [row["record"] for row in rows]
        assert [row["sequence"] for row in records] == [1, 2, 3, 4]
        assert [row["event"]["name"] for row in records] == [
            "session", "conversation", "session", "conversation",
        ]
        assert len({row["source_instance"] for row in records}) == 1
        assert len({row["route_generation"] for row in records}) == 1
        assert all(row["source"] == "agent-bridge" for row in records)
        assert all(row["event"]["to"] == "stopped" for row in records)
        assert all("session_id" not in row["event"] for row in records)
        assert legacy == []
        assert status["counters"]["dropped_route"] == 2
        for sid in ("fixture-one", "fixture-two"):
            session = manager.get_session(sid)
            assert session is not None and session.status is SessionStatus.STOPPED
        record_property("receiver_readback", json.dumps({
            "contract": records[0]["contract"], "version": records[0]["version"],
            "source": records[0]["source"], "source_schema": records[0]["source_schema"],
            "records": len(records), "receiver_children": 1,
            "sequences": [row["sequence"] for row in records],
            "readback_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "cwd_preserved": True, "literal_argv_preserved": True,
            "environment_isolated": True, "receiver_fsync": True,
            "ordinary_http_status": 204, "service_config_loaded": True,
            "running_capability": CAPABILITY,
        }, sort_keys=True))
    finally:
        before = time.monotonic()
        close(manager)
        telemetry.clear_telemetry_sink()
        assert time.monotonic() - before < 2.0
        db.close()
    owner = manager._consumer_telemetry.owner
    assert owner is not None and owner.writer is not None
    assert owner.writer.join(0)
    assert all(binding.pipe is None or binding.pipe.closed for binding in owner.bindings)
    assert owner.stats()["pipe_writes"] == 4


@pytest.mark.parametrize("behavior", [
    "time.sleep(30)", "raise SystemExit(7)", "descendant", "idle-exit",
])
def test_nonreading_receiver_has_finite_writer_and_shutdown(
    tmp_path: Path, behavior: str,
) -> None:
    slot = SinkSlot()
    code = "import pathlib,time,sys,subprocess;"
    idle_exit = behavior == "idle-exit"
    if idle_exit:
        code += "sys.stdin.buffer.readline();"
    if behavior in ("descendant", "idle-exit"):
        code += (
            "subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(30)'],"
            "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
        )
        behavior = "raise SystemExit(7)" if idle_exit else "time.sleep(30)"
    code += "pathlib.Path('started').touch();" + behavior
    configured(tmp_path, [sys.executable, "-I", "-c", code])
    owner = slot.configure_commands()
    binding = owner.admit(str(tmp_path))
    event = {"kind": "state_transition", "name": "session",
             "event": "session_state_changed", "to": "idle"}
    try:
        deadline = time.monotonic() + 4
        while owner.stats()["enqueued"] == 0 and time.monotonic() < deadline:
            slot.emit(event, binding=binding)
            time.sleep(.01)
        while not (tmp_path / "started").exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert (tmp_path / "started").exists(), (binding.state, owner.stats())
        if idle_exit:
            while (
                (binding.pipe is None or not binding.pipe.closed)
                and time.monotonic() < deadline
            ):
                time.sleep(.01)
            assert binding.pipe is not None and binding.pipe.closed
            assert owner.stats()["receiver_exited"] == 1
        for _ in range(10 if idle_exit else 5000):
            slot.emit(event, binding=binding)
        if not idle_exit:
            pressure_deadline = time.monotonic() + 3
            while (
                owner.stats()["write_failed"] + owner.stats()["receiver_exited"] == 0
                and time.monotonic() < pressure_deadline
            ):
                for _ in range(32):
                    slot.emit(event, binding=binding)
                time.sleep(.005)
    finally:
        before = time.monotonic()
        owner.close()
        elapsed = time.monotonic() - before
    assert elapsed < 2.0
    assert owner.writer is not None
    assert owner.writer.join(0)
    assert binding.state != "cleanup_unconfirmed"
    assert binding.pipe is None or binding.pipe.closed
    stats = owner.stats()
    assert stats["dropped_full"] + stats["abandoned"] + stats["dropped_route"] > 0
    assert stats["write_failed"] + stats["abandoned"] + stats["receiver_exited"] > 0
