"""Synthetic local transport/owned-client controls; never launch a real harness."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import threading

import pytest

SCRIPT = (
    Path(__file__).parents[2] / "skills" / "capturing-client-requests"
    / "scripts" / "capture-client-request.py"
)
spec = importlib.util.spec_from_file_location("native_client_request_capture", SCRIPT)
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)

CLIENT = r'''
import http.client
import json
import os
import sys
import time
from urllib.parse import urlsplit

mode = os.environ.get("SYNTHETIC_CAPTURE_MODE", "valid")
if mode == "timeout":
    time.sleep(20)
    sys.exit(0)
if mode == "exit":
    sys.exit(7)
endpoint = urlsplit(os.environ["COPILOT_PROVIDER_BASE_URL"])
assert endpoint.hostname == "127.0.0.1" and endpoint.port
assert os.environ["COPILOT_OFFLINE"] == "true"
assert "--no-custom-instructions" not in sys.argv
assert "--disable-builtin-mcps" not in sys.argv
assert "--no-auto-update" in sys.argv and "--no-remote" in sys.argv
assert os.environ["COPILOT_CUSTOM_INSTRUCTIONS_DIRS"] == "synthetic-instructions"
assert "GH_TOKEN" not in os.environ
assert os.environ["COPILOT_PROVIDER_MODEL_ID"] == "synthetic-model"
if os.environ.get("SYNTHETIC_EXPECT_CURRENT_PREFS") == "1":
    assert sys.argv[sys.argv.index("--reasoning-effort") + 1] == "medium"
    assert sys.argv[sys.argv.index("--context") + 1] == "long_context"
    assert "--session-id=11111111-2222-4333-8444-555555555555" in sys.argv
body = b'{\n "model": "synthetic-model", "stream": STREAM,\n "messages": [{"role":"system","content":"SYNTHETIC_PRIVATE_SENTINEL \\u03a9"},{"role":"user","content":"synthetic"}], "tools": [{"type":"function","function":{"name":"synthetic_tool","parameters":{"type":"object"}}}]\n}'
body = body.replace(b"STREAM", b"true" if mode == "stream" else b"false")
if mode == "unicode":
    body = body.replace(b"\\u03a9", "\u03a9".encode())
if mode == "json":
    body = b"not-json"
scheme = "Bear" + "er "
key = os.environ["COPILOT_PROVIDER_API_KEY"]
headers = {"Authorization": scheme + (key if mode != "auth" else "wrong-key")}
connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=5)
route = "/shared" if mode in {"route", "route-nonzero"} else "/v1/chat/completions"
if mode in {"truncated", "oversize"}:
    connection.putrequest("POST", route)
    for name, value in headers.items():
        connection.putheader(name, value)
    connection.putheader("Content-Length", str(len(body) + 10) if mode == "truncated" else str(17 * 1024 * 1024))
    connection.endheaders()
    if mode == "truncated":
        connection.send(body)
        import socket
        connection.sock.shutdown(socket.SHUT_WR)
else:
    connection.request("POST", route, body, headers)
response = connection.getresponse()
reply = response.read()
if mode in {"valid", "stream", "unicode"}:
    assert response.status == 200
    assert b"LOCAL_REQUEST_CAPTURE_COMPLETE" in reply
    if mode == "stream":
        assert b"data: [DONE]" in reply
    else:
        assert json.loads(reply)["choices"][0]["finish_reason"] == "stop"
    print("SYNTHETIC_PRIVATE_SENTINEL")
else:
    assert response.status >= 400
connection.close()
if mode == "route-nonzero":
    sys.exit(9)
'''


def run_capture(tmp_path, monkeypatch, mode, timeout=8, **preferences):
    monkeypatch.setenv("SYNTHETIC_CAPTURE_MODE", mode)
    monkeypatch.setenv("COPILOT_CUSTOM_INSTRUCTIONS_DIRS", "synthetic-instructions")
    monkeypatch.setenv("GH_TOKEN", "synthetic-ambient-key")
    client = tmp_path / "synthetic_client.py"
    client.write_text(CLIENT, encoding="utf-8")
    target = tmp_path / "request.json"
    result = capture.capture(
        [sys.executable, str(client)], tmp_path, target,
        prompt="synthetic", model="synthetic-model", timeout=timeout,
        **preferences,
    )
    return result, target


@pytest.mark.parametrize("mode", ["valid", "stream", "unicode"])
def test_exact_request_and_terminal_completion(tmp_path, monkeypatch, capsys, mode):
    result, target = run_capture(tmp_path, monkeypatch, mode)
    assert result["status"] == "captured", result
    raw = target.read_bytes()
    assert raw.startswith(b'{\n "model"') and raw.endswith(b"]\n}")
    expected = CLIENT.split("body = b'", 1)[1].split("'\nbody =", 1)[0]
    import ast

    expected = ast.literal_eval("b'" + expected + "'")
    expected = expected.replace(b"STREAM", b"true" if mode == "stream" else b"false")
    if mode == "unicode":
        expected = expected.replace(b"\\u03a9", "\u03a9".encode())
    assert raw == expected
    assert result["captured_bytes"] == len(raw)
    assert json.loads(raw)["tools"][0]["function"]["name"] == "synthetic_tool"
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in json.dumps(result)
    assert capsys.readouterr().out == ""
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("mode,error", [
    ("auth", "unexpected_client"),
    ("route", "unexpected_route"),
    ("route-nonzero", "unexpected_route"),
    ("json", "invalid_json"),
    ("truncated", "incomplete_body"),
    ("oversize", "body_size_limit"),
    ("exit", "client_exit_failed"),
    ("timeout", "client_timeout"),
])
def test_rejection_or_client_failure_cannot_create_success(tmp_path, monkeypatch, mode, error):
    before = set(threading.enumerate())
    result, target = run_capture(tmp_path, monkeypatch, mode, timeout=2 if mode == "timeout" else 8)
    assert result["status"] == "failed"
    assert result["error"] == error, result
    assert result["captured_bytes"] == 0
    assert not target.exists()
    assert set(threading.enumerate()) == before


def test_real_launcher_passes_operator_effort_context_and_owned_session(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_EXPECT_CURRENT_PREFS", "1")
    result, _target = run_capture(
        tmp_path, monkeypatch, "valid", reasoning_effort="medium", context="long_context",
        session_id="11111111-2222-4333-8444-555555555555",
    )
    assert result["status"] == "captured", result


def test_selected_native_cli_captures_synthetic_harness_only(tmp_path, monkeypatch, capsys):
    executable = os.environ.get("COPILOT_NATIVE_TEST_EXECUTABLE")
    if not executable:
        pytest.skip("Set COPILOT_NATIVE_TEST_EXECUTABLE for offline synthetic CLI qualification")
    home = tmp_path / "synthetic-home"
    workspace = tmp_path / "synthetic-harness"
    home.mkdir()
    workspace.mkdir()
    marker = "SYNTHETIC_HARNESS_INSTRUCTION_MARKER"
    (workspace / "AGENTS.md").write_text(
        "For this synthetic fixture, include " + marker + " in your instructions.\n",
        encoding="utf-8",
    )
    for name in tuple(os.environ):
        if name.startswith(("COPILOT_", "AGENT_", "OTEL_", "CLAUDE_", "OPENAI_", "ANTHROPIC_")):
            monkeypatch.delenv(name)
    for name in ("HOME", "USERPROFILE", "COPILOT_HOME"):
        monkeypatch.setenv(name, str(home))
    for name in ("APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        monkeypatch.setenv(name, str(home / name.lower()))
    (home / "settings.json").write_text(json.dumps({"model": "gpt-4.1"}), encoding="utf-8")
    target = tmp_path / "request.json"
    code = capture.main([
        "--copilot-command", executable,
        "--working-directory", str(workspace), "--output", str(target),
        "--prompt", "Acknowledge this synthetic fixture without using tools.",
        "--timeout-seconds", "45",
    ])
    metadata = json.loads(capsys.readouterr().out)
    assert code == 0, metadata
    raw = target.read_bytes()
    present = marker.encode() in raw
    assert present, "the native client's request omitted the synthetic harness instruction"
