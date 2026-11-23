"""One real native SDK delivery through the legacy Bridge consumer."""

from __future__ import annotations

from collections.abc import Callable
import importlib
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
import uvicorn
from agent_procutil.owned_process import run_caller_owned
from zdd.diagnostics import process_start_time

from agent_bridge.app_routes import configure_serving_app
from agent_bridge.db import Database
from agent_bridge.loop_governance import LoopGovernance
from agent_bridge.models import ServiceConfig
from agent_remote_driver.bridge_extension import extension_sources
from agent_remote_driver.session_host.osutil import pid_alive

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "plugins/agent-remote-driver/tests/native"))
from test_headed_extension import _OfflineCli, offline_cli as offline_cli  # noqa: E402


def _headers(value: str) -> dict[str, str]:
    scheme = "Bear" + "er "
    return {"Authorization": scheme + value}


def test_native_ack_fixture_preserves_the_actual_header_value() -> None:
    value = "fixture-nonempty-value"
    header = _headers(value)["Authorization"]
    assert header.endswith(value)
    assert header.removesuffix(value) == "Bear" + "er "


def _status(directory: Path) -> dict[str, Any]:
    paths = list(directory.glob("status-*.json"))
    if not paths:
        startup = directory / "startup.json"
        return cast(dict[str, Any], json.loads(startup.read_text(encoding="utf-8"))) if startup.is_file() else {}
    return cast(dict[str, Any], json.loads(max(paths).read_text(encoding="utf-8")))


def _wait(
    condition: Callable[[], bool], *, deadline: float, directory: Path,
    alive: Callable[[], bool] | None = None,
) -> None:
    while not condition():
        state = _status(directory)
        assert not state.get("error"), state
        assert alive is None or alive(), f"Original native fixture pane exited: {state}"
        assert time.monotonic() < deadline, state
        time.sleep(.03)


def _native_events(home: Path, session_id: str) -> list[dict[str, Any]]:
    try:
        content = (home / "session-state" / session_id / "events.jsonl").read_bytes()
    except FileNotFoundError:
        return []
    assert len(content) <= 1_048_576, "Native fixture transcript exceeded its bound"
    try:
        return [json.loads(line) for line in content.splitlines() if line]
    except json.JSONDecodeError:
        return []


def _stage_plugin(directory: Path) -> Path:
    plugin = directory / "plugin"
    extension = plugin / "extensions/consumption-ack"
    assets = extension / "core"
    assets.mkdir(parents=True)
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "native-consumption-ack-fixture", "version": "1.0.0"}),
        encoding="utf-8",
    )
    for name, content in extension_sources():
        (assets / name).write_bytes(content)
    entry = Path(__file__).parents[1] / "fixtures/native-consumption-ack.mjs"
    (extension / "extension.mjs").write_bytes(entry.read_bytes())
    return plugin


@pytest.mark.parametrize("operation", ["join", "metadata"])
def test_native_ack_fixture_retains_sdk_startup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    directory = tmp_path / "consumer"
    directory.mkdir()
    plugin = _stage_plugin(directory)
    entry = plugin / "extensions/consumption-ack/extension.mjs"
    sdk = entry.parent / "node_modules/@microsoft/copilot-sdk"
    sdk.mkdir(parents=True)
    (sdk / "package.json").write_text(json.dumps({
        "name": "@microsoft/copilot-sdk", "version": "1.0.0", "type": "module",
        "exports": {".": "./index.mjs", "./extension": "./index.mjs"},
    }), encoding="utf-8")
    (sdk / "index.mjs").write_text(
        "export const joinSession = async () => { throw Error('fixture join refused'); };\n"
        if operation == "join" else (
            "export const joinSession = async () => ({rpc: {metadata: {snapshot: async () => {\n"
            "throw Error('fixture metadata refused');\n"
            "}}}});\n"
        ),
        encoding="utf-8",
    )
    (directory / "consumer.json").write_text(json.dumps({
        "base": "http://127.0.0.1:12345", "sessionId": "fixture",
        "token": "synthetic-secret",
    }), encoding="utf-8")
    monkeypatch.setenv("BRIDGE_ACK_FIXTURE_DIRECTORY", str(directory))
    node = shutil.which("node")
    assert node is not None
    result = run_caller_owned(
        [node, str(entry)], cwd=tmp_path, env=dict(os.environ), timeout=10,
        max_stdout_bytes=4_096, max_stderr_bytes=4_096,
    )
    assert result.returncode != 0
    state = _status(directory)
    phase = "joining-session" if operation == "join" else "reading-metadata"
    assert state.get("startup_phase") == phase, state
    assert f"fixture {operation} refused" in str(state.get("error")), state


def test_native_legacy_sdk_ack_waits_for_observed_consumption(
    tmp_path: Path, tmp_db: Database, offline_cli: _OfflineCli,
    monkeypatch: pytest.MonkeyPatch,
    record_testsuite_property: Callable[[str, object], None],
) -> None:
    binary = os.environ.get("PSMUX_TEST_BIN")
    if os.name != "nt" or not binary or os.environ.get("COPILOT_DRIVER_HEADED_MUX_E2E") != "1":
        pytest.skip("Select the existing isolated Windows native owner")
    assert Path(binary).is_absolute() and Path(binary).is_file()
    assert shutil.which("node") is not None
    monkeypatch.syspath_prepend(str(ROOT / "plugins/agent-remote-driver/tests"))
    FixtureMux = importlib.import_module("headed_mux_fixture").FixtureMux
    directory = tmp_path / "native-ack"
    directory.mkdir()
    plugin = _stage_plugin(directory)
    native_id = str(uuid4())
    token = "fixture-secret"
    app = configure_serving_app(
        ServiceConfig(db_path=str(tmp_db.db_path), bind="127.0.0.1"), token, lifespan=None,
    )
    app.state.db = tmp_db
    app.state.ready = True
    app.state.loop_governance = LoopGovernance()
    app.state.session_manager = SimpleNamespace(is_draining=False)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    (directory / "consumer.json").write_text(json.dumps({
        "base": f"http://127.0.0.1:{port}", "sessionId": native_id, "token": token,
    }), encoding="utf-8")
    (offline_cli.home / "config.json").write_text(
        json.dumps({"trustedFolders": [str(offline_cli.workspace)]}), encoding="utf-8",
    )
    environment = {
        **offline_cli.env, "HOME": str(offline_cli.home), "USERPROFILE": str(offline_cli.home),
        "COPILOT_PLUGIN_DIR_ONLY": "true", "SESSION_ID": native_id,
        "BRIDGE_ACK_FIXTURE_DIRECTORY": str(directory),
        "PSMUX_NO_WARM": "1", "PSMUX_CONFIG_FILE": os.devnull,
        "PSMUX_DATA_DIR": str(offline_cli.home / ".psmux"), "PSMUX_SERVER_DEBUG": "1",
    }
    mux = FixtureMux(binary, offline_cli.home, environment)
    started = False
    pane_identity: str | None = None
    births: dict[int, str] = {}
    deadline = time.monotonic() + 90
    try:
        _wait(lambda: server.started, deadline=deadline, directory=directory)
        launched = mux.query(
            "new-session", "-d", "-s", mux.target, "-c", str(offline_cli.workspace), "--",
            offline_cli.executable, "--session-id", native_id,
            "--experimental", "--plugin-dir", str(plugin), "--no-auto-update",
            "--disable-builtin-mcps", "--no-custom-instructions", "--no-color",
            "--log-dir", str(offline_cli.logs),
        )
        assert launched.returncode == 0, launched.stderr
        started = True
        pane = mux.query(
            "display-message", "-p", "-t", mux.target,
            "#{session_id}:#{session_created}|#{pane_id}|#{pane_pid}",
        )
        assert pane.returncode == 0, pane.stdout + pane.stderr
        pane_identity, pane_pid = pane.stdout.strip().rsplit("|", 1)
        pid = int(pane_pid)
        birth = process_start_time(pid)
        assert birth is not None and pid_alive(pid), pane.stdout
        births[pid] = birth
        _wait(lambda: _status(directory).get("session_id") == native_id,
              deadline=deadline, directory=directory,
              alive=lambda: pid_alive(pid) and process_start_time(pid) == birth)
        ready = _status(directory)
        for key in ("native_pid", "extension_pid"):
            pid = int(ready[key])
            birth = process_start_time(pid)
            assert birth is not None and pid_alive(pid), ready
            births[pid] = birth
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", headers=_headers(token),
            timeout=3, trust_env=False,
        ) as client:
            registered = client.post("/api/v1/live-sessions", json={
                "session_id": native_id, "machine": "fixture",
                "pid": ready["extension_pid"], "process_started_at": ready["extension_started_at"],
            })
            assert registered.status_code == 200, registered.text
            sent = client.post(f"/api/v1/live-sessions/{native_id}/messages", json={
                "sender": "fixture-sender", "body": 'one native input\n"literal"',
                "idempotency_key": uuid4().hex, "expected_session_id": native_id, "delivery": "steer",
            })
            assert sent.status_code == 200, sent.text
            message_id = sent.json()["message_id"]
            (directory / "start").write_text("start\n", encoding="utf-8")
            _wait(lambda: _status(directory).get("accepted") == 1
                  and _status(directory).get("native_event") is not None,
                  deadline=deadline, directory=directory)
            held = _status(directory)
            observed = held["native_event"]
            assert observed["type"] == "user.message"
            content = observed["data"].get("transformedContent", observed["data"].get("content", ""))
            assert f'msg-id="{message_id}"' in content and 'one native input\n"literal"' in content
            assert observed["data"].get("source") in (None, "agent-bridge")
            _wait(lambda: _status(directory)["polls"] >= held["polls"] + 2,
                  deadline=deadline, directory=directory)
            assert _status(directory)["sends"] == 1
            assert _status(directory)["acknowledgements"] == 0
            row = tmp_db.execute_read("SELECT delivered_at FROM live_messages WHERE id=?", (message_id,))[0]
            assert row["delivered_at"] is None, "SDK acceptance was acknowledged before observation"
            (directory / "release").write_text("release\n", encoding="utf-8")
            _wait(lambda: _status(directory).get("finished") is True,
                  deadline=deadline, directory=directory)
            result = _status(directory)
            assert result["sends"] == result["accepted"] == 1
            assert result["acknowledged_ids"] == [message_id]
            assert result["released_event_id"] == observed["id"]
            assert result["session_id"] == native_id
            assert tmp_db.execute_read(
                "SELECT delivered_at FROM live_messages WHERE id=?", (message_id,),
            )[0]["delivered_at"] is not None
            _wait(lambda: len(offline_cli.requests) == 1 and any(
                event["type"] == "assistant.message"
                for event in _native_events(offline_cli.home, native_id)
            ), deadline=deadline, directory=directory)
            inputs = [
                event for event in _native_events(offline_cli.home, native_id)
                if event["type"] == "user.message"
            ]
            assert len(inputs) == 1 and inputs[0]["id"] == observed["id"]
            record_testsuite_property("native_consumption_ack", json.dumps({
                "native_session_id": native_id, "message_id": message_id,
                "native_event_id": observed["id"],
                "same_native_session": True, "sends": 1, "acknowledgements": 1,
                "observed_event_id_preserved": True, "sdk_acceptance_alone_not_acknowledged": True,
            }))
    except Exception:
        if started:
            panel = (
                mux.capture_original_pane(pane_identity) if pane_identity is not None
                else mux.query("capture-pane", "-p", "-t", mux.target)
            )
            print("Native ACK fixture pane:\n" + (panel.stdout + panel.stderr)[-8_192:], flush=True)
        for root in (offline_cli.logs, offline_cli.home / ".psmux"):
            for log in sorted(root.rglob("*.log"))[:4]:
                try:
                    with log.open("rb") as stream:
                        stream.seek(max(0, log.stat().st_size - 4_096))
                        print(stream.read().decode("utf-8", errors="replace"), flush=True)
                except OSError:
                    pass
        raise
    finally:
        try:
            if started and mux.query("has-session", "-t", mux.target).returncode == 0:
                if pane_identity is not None:
                    original = mux.query(
                        "display-message", "-p", "-t", mux.target,
                        "#{session_id}:#{session_created}|#{pane_id}",
                    )
                    assert original.returncode == 0 and original.stdout.strip() == pane_identity
                assert mux.query("kill-session", "-t", mux.target).returncode == 0
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            sock.close()
            cleanup_deadline = time.monotonic() + 5
            while any(pid_alive(pid) and process_start_time(pid) == birth for pid, birth in births.items()):
                assert time.monotonic() < cleanup_deadline, births
                time.sleep(.03)
            assert not thread.is_alive()
