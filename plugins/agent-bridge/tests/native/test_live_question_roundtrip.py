"""Opt-in real interactive ask_user, offline model, private mux and bridge."""

from __future__ import annotations

import difflib
import http.server
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import uvicorn
from agent_procutil import no_window_kwargs

from agent_bridge.app_routes import configure_serving_app
from agent_bridge.models import ServiceConfig


@pytest.mark.parametrize("transport", ["native", "mux-option", "mux-text"])
def test_real_interactive_question_resumes_without_prompt_or_abort(tmp_path, tmp_db, monkeypatch, transport):
    executable = os.environ.get("COPILOT_NATIVE_TEST_EXECUTABLE")
    mux_binary = os.environ.get("PSMUX_TEST_BIN" if os.name == "nt" else "TMUX_TEST_BIN")
    if not executable or not mux_binary:
        pytest.skip("Select native Copilot and the platform mux explicitly for the private fixture")
    assert Path(executable).is_absolute() and Path(executable).is_file()
    assert Path(mux_binary).is_absolute() and Path(mux_binary).is_file()
    root = Path(__file__).resolve().parents[4]
    monkeypatch.syspath_prepend(str(root / "plugins/agent-remote-driver/tests"))
    from headed_mux_fixture import FixtureMux

    calls = []

    class Provider(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(body)
            tools = [item["function"]["name"] for item in body.get("tools", [])]
            question_tool = next((name for name in tools if name.endswith("ask_user")), None)
            answered = any(
                item.get("role") == "tool" and "blue" in str(item.get("content"))
                for item in body.get("messages", [])
            )
            if question_tool and not answered:
                delta = {"role": "assistant", "tool_calls": [{
                    "index": 0, "id": "fixture-question", "type": "function",
                    "function": {"name": question_tool, "arguments": json.dumps({
                        "message": "Choose a fixture color",
                        "requestedSchema": {
                            "type": "object", "properties": {"color": {
                                "type": "string",
                                **({"enum": ["red", "blue"]} if transport != "native" else {}),
                            }},
                        },
                    })},
                }]}
                finish = "tool_calls"
            else:
                delta, finish = {"role": "assistant", "content": "FIXTURE_COMPLETE"}, "stop"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for value, reason in ((delta, None), ({}, finish)):
                chunk = {
                    "id": "fixture", "object": "chat.completion.chunk", "model": "gpt-4.1",
                    "choices": [{"index": 0, "delta": value, "finish_reason": reason}],
                }
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    provider = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    provider.daemon_threads = True
    provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
    provider_thread.start()
    home, workspace, config = (tmp_path / name for name in ("home", "workspace", "bridge"))
    for path in (home, workspace, config):
        path.mkdir()
    cli_home = home / "copilot"
    cli_home.mkdir()
    (cli_home / "config.json").write_text(
        json.dumps({"trustedFolders": [str(workspace)]}), encoding="utf-8",
    )
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "live-question-fixture", "version": "1.0.0"}), encoding="utf-8",
    )
    shutil.copytree(
        root / "plugins/agent-bridge/extensions", plugin / "extensions",
    )
    native_id = str(uuid4())
    environment = {
        key: value for key, value in os.environ.items()
        if (key.startswith("COPILOT_EXTENSIONS_")
            or not key.startswith(("COPILOT_", "AGENT_", "WORKTREE_", "OTEL_")))
        and key not in {
            "GH_TOKEN", "GITHUB_TOKEN", "GITHUB_API_TOKEN", "TMUX", "TMUX_PANE",
            "PSMUX_PANE", "WT_SESSION", "TERM_PROGRAM",
        }
    }
    environment.update(
        HOME=str(home), USERPROFILE=str(home), COPILOT_HOME=str(cli_home),
        COPILOT_OFFLINE="true", COPILOT_PLUGIN_DIR_ONLY="true",
        COPILOT_PROVIDER_BASE_URL=f"http://127.0.0.1:{provider.server_port}",
        COPILOT_PROVIDER_TYPE="openai", COPILOT_PROVIDER_MODEL_ID="gpt-4.1",
        COPILOT_MODEL="gpt-4.1", USE_TGREP="false", PSMUX_NO_WARM="1",
        PSMUX_CONFIG_FILE=os.devnull, PSMUX_DATA_DIR=str(tmp_path / "psmux-data"),
        AGENT_BRIDGE_CONFIG_DIR=str(config), AGENT_BRIDGE_RECEIVING_PLUGIN_ROOT=str(plugin),
        AGENT_BRIDGE_SCOPE_ID="question-fixture", AGENT_BRIDGE_NATIVE_SESSION_ID=native_id,
        SESSION_ID=native_id,
    )
    app = configure_serving_app(
        ServiceConfig(db_path=str(tmp_db.db_path), bind="127.0.0.1"),
        "synthetic-secret", lifespan=None,
    )
    app.state.db = tmp_db
    app.state.ready = True
    app.state.session_manager = SimpleNamespace(is_draining=False)
    monkeypatch.setattr("agent_bridge.routes.health.observe_worktree_writer", lambda: {})
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    server_thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True,
    )
    server_thread.start()
    (config / "active.json").write_text(
        json.dumps({"active": {"port": port}}), encoding="utf-8",
    )
    (config / "auth.yaml").write_text("token: synthetic-secret\n", encoding="utf-8")
    scheme = "Bear" + "er "
    headers = {"Authorization": scheme + "synthetic-secret"}
    assert headers["Authorization"].endswith("synthetic-secret")
    mux = FixtureMux(mux_binary, home, environment)
    started = False
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", headers=headers, timeout=5,
        ) as client:
            deadline = time.monotonic() + 22
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.025)
            assert client.get("/health").json()["capabilities"]["live_question_answers"] == 1
            launched = mux.query(
                "new-session", "-d", "-s", mux.target, "-c", str(workspace), "--",
                executable, "--interactive", "ISOLATED_QUESTION_FIXTURE",
                "--session-id", native_id, "--experimental",
                "--plugin-dir", str(plugin), "--no-auto-update", "--disable-builtin-mcps",
                "--no-custom-instructions", "--no-color", "--log-dir", str(tmp_path / "logs"),
            )
            assert launched.returncode == 0, launched.stderr
            started = True
            base = f"/api/v1/live-sessions/{native_id}/questions"
            question = None
            while time.monotonic() < deadline:
                result = client.get(base)
                data = result.json()
                if result.status_code == 200 and data.get("requests"):
                    question = data
                    break
                time.sleep(0.05)
            panel = mux.query("capture-pane", "-p", "-t", mux.target)
            diagnostics = "\n".join(
                path.read_text(encoding="utf-8", errors="replace")[-6000:]
                for path in (tmp_path / "logs").rglob("*.log")
            )
            assert question is not None, (
                panel.stdout + panel.stderr + f"\nprovider calls: {len(calls)}\n"
                + "\n" + diagnostics
            )
            pending = question["requests"][0]
            assert pending["answerable"], pending
            assert pending["family"] == "elicitation"
            # The real native UI is parked before any answer is admitted.
            assert "Choose a fixture color" in panel.stdout
            if transport == "native":
                result = client.post(base + "/answers", json={
                    "generation": question["generation"], "request_id": pending["request_id"],
                    "family": pending["family"], "sender": "isolated-operator",
                    "content": {"color": "blue"}, "wait_timeout": 5,
                })
            else:
                from agent_bridge.mux_question_backend import LocalMux
                from plugin_test_command_safety import validate_private_mux_args

                captures = []

                class PrivateBackend(LocalMux):
                    def run(self, argv):
                        validate_private_mux_args(mux.mux_args, sandbox=mux.sandbox, cwd=mux.root)
                        assert argv[1] in {"capture-pane", "send-keys"}
                        assert argv[argv.index("-t") + 1] == self.target
                        completed = subprocess.run(
                            [mux.binary, *mux.mux_args, *argv[1:]], env=mux.environment,
                            cwd=mux.root, capture_output=True, text=True, encoding="utf-8",
                            timeout=5, **no_window_kwargs(),
                        )
                        assert completed.returncode == 0, completed.stderr
                        if argv[1] == "capture-pane":
                            captures.append(completed.stdout)
                        return completed.stdout

                    def binding(self, session_id, row):
                        # The native fixture owns the private IPC target; production
                        # session-binding is separately exercised by backend controls.
                        identity = mux.query(
                            "display-message", "-p", "-t", mux.target,
                            "#{pane_id}|#{pane_pid}|#{session_id}:#{session_created}|#{window_id}|#{pane_index}",
                        )
                        assert identity.returncode == 0, identity.stderr
                        self.pane, pid, incarnation, window, index = identity.stdout.strip().split("|")
                        self.target = f"{mux.target}:{window}.{index}"
                        return {"binary": mux.binary, "mux_session": mux.target,
                                "pane_id": self.pane, "pid": pid, "target": self.target,
                                "incarnation": incarnation, "session_id": session_id}

                app.state.mux_questions.backend = PrivateBackend()
                monkeypatch.setattr(app.state.live_questions, "current", lambda *_: None)
                # Exercise the native completion's elapsed-time annotation.
                time.sleep(6)
                inspection = client.get(base + "/mux")
                assert inspection.status_code == 200, panel.stdout + "\n" + inspection.text
                selected = inspection.json()
                time.sleep(1.2)
                choice = next(
                    item["number"] for item in selected["options"] if item["label"] == "blue"
                )
                result = client.post(base + "/mux", json={
                    "inspection_id": selected["inspection_id"], "sender": "isolated-operator",
                    **({"option": choice} if transport == "mux-option" else {"text": "blue"}),
                })
            assert result.status_code == 200, result.text
            assert result.json()["accepted"] is True, (
                result.text + "\n" + mux.query("capture-pane", "-p", "-t", mux.target).stdout
                + ("\n".join(difflib.unified_diff(
                    captures[0].splitlines(), captures[1].splitlines(),
                )) if transport != "native" and len(captures) > 1 else "")
            )
            while time.monotonic() < deadline:
                path = cli_home / "session-state" / native_id / "events.jsonl"
                events = [
                    json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                ] if path.exists() else []
                if any(
                    event["type"] == "assistant.message"
                    and "FIXTURE_COMPLETE" in event.get("data", {}).get("content", "")
                    for event in events
                ):
                    break
                time.sleep(0.05)
            else:
                pytest.fail("native question did not resume the same conversation")
            assert sum(event["type"] == "user.message" for event in events) == 1
            assert not any(event["type"] == "abort" for event in events)
            assert any(
                event["type"] == "tool.execution_complete"
                and event["data"].get("toolCallId") == "fixture-question"
                and event["data"].get("success") is True
                for event in events
            )
            assert any(
                message.get("role") == "tool" and "blue" in str(message.get("content"))
                for call in calls for message in call.get("messages", [])
            )
            while transport == "native" and time.monotonic() < deadline:
                log = app.state.live_event_store.get(native_id)
                represented = log.snapshot_history()[1] if log else []
                if not client.get(base).json()["requests"] and any(
                    event.event == "ask_user_resolved"
                    and event.data.get("request_id") == pending["request_id"]
                    for event in represented
                ):
                    break
                time.sleep(0.05)
            else:
                if transport == "native":
                    pytest.fail("native completion did not clear represented question state")
            panel = mux.query("capture-pane", "-p", "-t", mux.target)
            assert "FIXTURE_COMPLETE" in panel.stdout
            assert "Asked user Choose a fixture color" in panel.stdout
            assert "color: blue" in panel.stdout
            if transport == "native":
                assert client.post(base + "/answers", json={
                    "generation": question["generation"], "request_id": pending["request_id"],
                    "family": pending["family"], "sender": "isolated-operator",
                    "content": {"color": "blue"}, "wait_timeout": 0,
                }).status_code == 409
    finally:
        if started and mux.query("has-session", "-t", mux.target).returncode == 0:
            assert mux.query("kill-session", "-t", mux.target).returncode == 0
        server.should_exit = True
        server_thread.join(timeout=3)
        sock.close()
        provider.shutdown()
        provider.server_close()
        provider_thread.join(timeout=3)
    assert not server_thread.is_alive() and not provider_thread.is_alive()
