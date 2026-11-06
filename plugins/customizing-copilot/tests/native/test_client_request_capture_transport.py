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
assert "--no-auto-update" in sys.argv
assert "--no-remote" not in sys.argv
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

MULTI_CLIENT = r'''
import http.client
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

mode = os.environ["SYNTHETIC_CAPTURE_MODE"]
endpoint = urlsplit(os.environ["COPILOT_PROVIDER_BASE_URL"])
assert [sys.argv[index + 1] for index, value in enumerate(sys.argv) if value == "--allow-tool"] == ["glob", "view"]
assert "--allow-all" not in sys.argv and "--add-dir" not in sys.argv
payload = {
    "model": "synthetic-model", "stream": mode == "stream", "messages": [],
    "tools": [
        {"type": "function", "function": {"name": name, "parameters": {
            "type": "object", "properties": properties, "required": list(properties),
        }}}
        for name, properties in [
            ("glob", {"pattern": {"type": "string"}, "paths": {"type": "string"}}),
            ("view", {"path": {"type": "string"}, "forceReadLargeFiles": {"type": "boolean"}}),
        ]
    ],
}
scheme = "Bear" + "er "
headers = {"Authorization": scheme + os.environ["COPILOT_PROVIDER_API_KEY"]}
for round_number in range(1, 4):
    connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=5)
    connection.request("POST", "/v1/chat/completions", json.dumps(payload).encode(), headers)
    response = connection.getresponse()
    body = response.read()
    assert response.status == 200
    connection.close()
    if payload["stream"]:
        assert body.endswith(b"data: [DONE]\n\n")
        choice = json.loads(body.split(b"\n\n")[0][6:])["choices"][0]
        message = choice["delta"]
        for index, call in enumerate(message.get("tool_calls", [])):
            assert call.pop("index") == index
    else:
        choice = json.loads(body)["choices"][0]
        message = choice["message"]
    if round_number == 3:
        assert choice["finish_reason"] == "stop"
        assert message["content"] == "LOCAL_REQUEST_CAPTURE_COMPLETE"
        break
    assert choice["finish_reason"] == "tool_calls"
    payload["messages"].append(message)
    for call in message["tool_calls"]:
        arguments = json.loads(call["function"]["arguments"])
        if round_number == 1:
            assert call["function"]["name"] == "glob"
            content = "\n".join(str(path) for path in sorted(Path(arguments["paths"]).glob(arguments["pattern"])))
        else:
            assert call["function"]["name"] == "view" and arguments["forceReadLargeFiles"] is True
            content = Path(arguments["path"]).read_text(encoding="utf-8")
        payload["messages"].append({"role": "tool", "tool_call_id": call["id"], "content": content})
    if mode == "stall":
        time.sleep(20)
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


@pytest.mark.parametrize("mode", ["valid", "stream", "stall"])
def test_three_round_transport_and_stalled_round_cleanup(tmp_path, monkeypatch, mode):
    session_id = "11111111-2222-4333-8444-555555555555"
    home = tmp_path / "home"
    root = home / "session-state" / session_id / "instructions"
    root.mkdir(parents=True)
    for name in ("one", "two"):
        (root / f"{name}.md").write_text(f"SYNTHETIC_LATE_{name}\n", encoding="utf-8")
    monkeypatch.setenv("COPILOT_HOME", str(home))
    monkeypatch.setenv("SYNTHETIC_CAPTURE_MODE", mode)
    client = tmp_path / "synthetic_client.py"
    client.write_text(MULTI_CLIENT, encoding="utf-8")
    target = tmp_path / "request.json"
    before = set(threading.enumerate())
    result = capture.capture(
        [sys.executable, str(client)], tmp_path, target,
        prompt="synthetic", model="synthetic-model", timeout=10,
        session_id=session_id, load_session_guidance=True, round_timeout=2,
    )
    assert set(threading.enumerate()) == before
    assert target.exists() and b"SYNTHETIC_LATE" not in target.read_bytes()
    if mode == "stall":
        assert result["status"] == "failed" and result["error"] == "round_timeout"
        assert result["multi_round"]["status"] == "incomplete"
        assert len(result["multi_round"]["rounds"]) == 1
        assert not (tmp_path / "request.round-2.json").exists()
        assert not (tmp_path / "request.round-3.json").exists()
    else:
        assert result["status"] == "captured", result
        assert result["multi_round"]["status"] == "complete"
        assert len(result["multi_round"]["rounds"]) == 3
        final = (tmp_path / "request.round-3.json").read_bytes()
        assert b"SYNTHETIC_LATE_one" in final and b"SYNTHETIC_LATE_two" in final
        assert result["captured_bytes"] == sum(row["wire_bytes"] for row in result["multi_round"]["rounds"])


@pytest.mark.parametrize("multi_round", [False, True])
def test_selected_native_cli_captures_synthetic_harness_only(tmp_path, monkeypatch, capsys, multi_round):
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
    extra = []
    if multi_round:
        session_id = "11111111-2222-4333-8444-555555555555"
        monkeypatch.setattr(capture, "uuid4", lambda: session_id)
        instructions = home / "session-state" / session_id / "instructions" / "synthetic-plugin"
        instructions.mkdir(parents=True)
        (instructions / "session-guidance.instructions.md").write_text(
            "SYNTHETIC_LATE_GUIDANCE_MARKER\nA second line of synthetic guidance.\n", encoding="utf-8",
        )
        extra.append("--load-session-guidance")
    code = capture.main([
        "--copilot-command", executable,
        "--working-directory", str(workspace), "--output", str(target),
        "--prompt", "Acknowledge this synthetic fixture without using tools.",
        "--timeout-seconds", "45",
        *extra,
    ])
    metadata = json.loads(capsys.readouterr().out)
    assert code == 0, metadata
    raw = target.read_bytes()
    present = marker.encode() in raw
    assert present, "the native client's request omitted the synthetic harness instruction"
    if multi_round:
        final = (tmp_path / "request.round-3.json").read_bytes()
        assert b"SYNTHETIC_LATE_GUIDANCE_MARKER" not in raw
        assert b"SYNTHETIC_LATE_GUIDANCE_MARKER" in final
        assert metadata["multi_round"]["status"] == "complete"
        assert len(metadata["multi_round"]["rounds"]) == 3
