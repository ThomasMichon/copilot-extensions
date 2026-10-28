"""Opt-in real CLI startup/resume semantics against an offline synthetic provider."""

from __future__ import annotations

import http.server
import json
import os
from pathlib import Path
import threading
from uuid import uuid4

import pytest
from agent_procutil.owned_process import run_caller_owned

from agent_worktrees import embody_resume

pytestmark = pytest.mark.guard


def test_native_interactive_resume_preserves_literal_prompt_and_history(tmp_path):
    executable = os.environ.get("COPILOT_NATIVE_TEST_EXECUTABLE")
    if not executable:
        pytest.skip("Set COPILOT_NATIVE_TEST_EXECUTABLE for isolated offline CLI qualification")
    assert Path(executable).is_absolute() and Path(executable).is_file()
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            assert request.get("stream") is True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for delta, finish in (
                ({"role": "assistant", "content": "SYNTHETIC_NATIVE_REPLY"}, None),
                ({}, "stop"),
            ):
                body = {
                    "id": "fixture", "object": "chat.completion.chunk", "model": "gpt-4.1",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                }
                self.wfile.write(("data: " + json.dumps(body) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    home, cwd = tmp_path / "cli-home", tmp_path / "empty-workspace"
    home.mkdir()
    cwd.mkdir()
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COPILOT_", "AGENT_BRIDGE", "AGENT_WORKTREES", "WORKTREE_", "OTEL_"))
        and key not in {
            "GH_TOKEN", "GITHUB_TOKEN", "GITHUB_API_TOKEN", "TMUX", "TMUX_PANE", "PSMUX_PANE",
            "WT_SESSION", "TERM_PROGRAM",
        }
    }
    environment.update(
        COPILOT_HOME=str(home), COPILOT_OFFLINE="true",
        COPILOT_PROVIDER_BASE_URL=f"http://127.0.0.1:{server.server_port}",
        COPILOT_PROVIDER_TYPE="openai", COPILOT_PROVIDER_MODEL_ID="gpt-4.1",
        COPILOT_MODEL="gpt-4.1", COPILOT_ALLOW_ALL="true", USE_TGREP="false",
    )
    session = str(uuid4())
    prompts = ['--resume=literal\nFIRST_NATIVE "quote" \u03a9',
               "SECOND_NATIVE preserve the first turn\n" + "L" * 12000]
    try:
        for index, prompt in enumerate(prompts):
            command = [
                executable, "--no-auto-update", "--disable-builtin-mcps",
                "--no-custom-instructions", "--no-remote", "--no-color", "--no-ask-user",
                "--model", "gpt-4.1", "--log-dir", str(tmp_path / "logs"),
            ]
            command = (
                [*command, f"--session-id={session}"] if index == 0
                else embody_resume.with_resume(command, session)
            )
            result = run_caller_owned(
                embody_resume.with_seed(command, prompt), cwd=str(cwd), env=environment,
                timeout=30, max_stdout_bytes=65536, max_stderr_bytes=65536,
            )
            assert result.returncode == 0, result.stdout + result.stderr
        if os.name == "nt":
            with pytest.raises(OSError) as failure:
                run_caller_owned(
                    embody_resume.with_seed(command, "X" * 40000), cwd=str(cwd), env=environment,
                    timeout=30, max_stdout_bytes=65536, max_stderr_bytes=65536,
                )
            assert failure.value.winerror == 206
        files = list((home / "session-state").glob("*/events.jsonl"))
        assert [path.parent.name for path in files] == [session]
        events = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
        assert [event["data"]["content"] for event in events if event["type"] == "user.message"] == prompts
        assert sum(event["type"] == "assistant.turn_end" for event in events) == 2
        assert sum(event["type"] == "session.resume" for event in events) == 1
        assert len(requests) == 2
        messages = requests[1]["messages"]
        assert [message["role"] for message in messages] == ["system", "user", "assistant", "user"]
        assert messages[1]["content"].endswith(prompts[0])
        assert messages[2]["content"] == "SYNTHETIC_NATIVE_REPLY"
        assert messages[3]["content"].endswith(prompts[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
