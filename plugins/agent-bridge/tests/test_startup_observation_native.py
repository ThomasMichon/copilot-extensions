"""Real CLI/HTTP/SSE boundaries against a disposable synthetic target."""

import contextlib
import json
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from agent_procutil.startup import PROGRESS_PREFIX
from agent_bridge.provider_process import run_captured


class Backend:
    def __init__(
        self, *, updates=True, disconnect=False, lost_admission=False,
        ack_failure=None, protocol=46, delayed_preflight=None, resume_ack_failure=None,
    ):
        from agent_bridge.routes.sessions import _controlled_cursor_key
        from fastapi import HTTPException

        self.updates = updates
        self.disconnect = disconnect
        self.lost_admission = lost_admission
        self.ack_failure = ack_failure
        self.stopping = threading.Event()
        self.resumed = threading.Event()
        self.condition = threading.Condition()
        self.events = []
        self.state = "stopped"
        self.resume_calls = 0
        self.turns = []
        self.cursors = []
        self.audiences = []
        self.preflight_requests = []
        backend = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, payload):
                data = json.dumps(payload).encode()
                with contextlib.suppress(BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == delayed_preflight:
                    backend.preflight_requests.append(parsed.path)
                    backend.stopping.wait(3)
                if parsed.path == "/health":
                    self.reply({"protocol_version": protocol, "min_protocol_version": 1})
                elif parsed.path.endswith("/live-sessions/resolve"):
                    self.reply({})
                elif parsed.path.endswith("/original/cursor"):
                    self.reply({
                        "last_acked_id": 0, "head_id": len(backend.events),
                        "continuity_id": "same-lineage",
                    })
                elif parsed.path.endswith("/original/events"):
                    parameters = urllib.parse.parse_qs(parsed.query)
                    try:
                        caller = _controlled_cursor_key(parameters.get("caller_id", [None])[0])
                    except HTTPException as exc:
                        self.send_error(exc.status_code)
                        return
                    if (
                        "after" not in parameters
                        or parameters.get("continuity_id") != ["same-lineage"]
                    ):
                        self.send_error(422)
                        return
                    cursor = int(parameters.get("after", ["0"])[0])
                    with backend.condition:
                        backend.cursors.append(cursor)
                        backend.audiences.append(caller)
                        close_first = backend.disconnect and len(backend.cursors) == 1
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    self.wfile.flush()
                    while not backend.stopping.is_set():
                        with backend.condition:
                            pending = list(enumerate(backend.events[cursor:], start=cursor + 1))
                            if not pending:
                                backend.condition.wait(.03)
                        for event_id, (kind, data) in pending:
                            payload = json.dumps({
                                "event": kind, "data": data, "continuity_id": "same-lineage",
                            })
                            frame = f"id: {event_id}\nevent: {kind}\ndata: {payload}\n\n"
                            try:
                                self.wfile.write(frame.encode())
                                self.wfile.flush()
                            except (BrokenPipeError, ConnectionResetError):
                                return
                            cursor = event_id
                            if close_first:
                                return
                elif parsed.path in {
                    "/api/v1/sessions/original", "/api/v1/sessions/native-acp",
                }:
                    self.reply({
                        "session_id": "original", "status": backend.state,
                        "turn_count": 0, "target_type": "local",
                    })
                else:
                    self.send_error(404)

            def do_POST(self):
                size = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(size) or b"{}")
                path = urllib.parse.urlparse(self.path).path
                if path == "/api/v1/sessions/original/resume":
                    backend.resume_calls += 1
                    backend.state = "starting"
                    deadline = time.monotonic() + 3.0
                    while time.monotonic() < deadline and not backend.stopping.wait(.03):
                        if backend.updates:
                            with backend.condition:
                                backend.events.append((
                                    "future-startup-event-v100", {"opaque": "never-forward-this"},
                                ))
                                backend.events.append((
                                    "startup_progress", {"phase": "Git", "state": "waiting"},
                                ))
                                backend.condition.notify_all()
                    backend.state = "idle"
                    backend.resumed.set()
                    if resume_ack_failure == "no-content":
                        self.send_response(204)
                        self.end_headers()
                        return
                    if resume_ack_failure == "empty":
                        self.reply({})
                        return
                    if resume_ack_failure == "wrong-session":
                        self.reply({"session_id": "other", "status": "idle"})
                        return
                    if resume_ack_failure == "starting":
                        self.reply({"session_id": "original", "status": "starting"})
                        return
                    self.reply({"session_id": "original", "status": "idle"})
                elif path == "/api/v1/sessions/original/turns":
                    backend.turns.append(body)
                    if backend.ack_failure in {"empty-status", "invalid-status"}:
                        self.reply({
                            "session_id": "original", "turn_index": 0,
                            "status": "" if backend.ack_failure == "empty-status" else "not-a-lifecycle-status",
                        })
                        return
                    if backend.lost_admission:
                        backend.stopping.wait(4)
                    if backend.ack_failure is not None:
                        data = b'{"turn_index":' if backend.ack_failure == "truncated" else b"not JSON"
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header(
                            "Content-Length",
                            str(len(data) + (50 if backend.ack_failure == "truncated" else 0)),
                        )
                        self.end_headers()
                        self.wfile.write(data)
                        self.wfile.flush()
                        self.close_connection = True
                        return
                    self.reply({"turn_index": 1, "status": "running", "session_id": "original"})
                else:
                    self.send_error(404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stopping.set()
        with self.condition:
            self.condition.notify_all()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        assert not self.thread.is_alive()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}"


def launch_prefix(backend):
    # Client construction and optional caller discovery are fixture-only.
    # The real CLI, HTTP requests, event parsing and send framing remain intact.
    script = (
        "import sys; from agent_bridge import __main__ as cli; "
        "from agent_bridge.client import BridgeClient; "
        "base=sys.argv.pop(1); "
        "cli._get_client=lambda **kw: BridgeClient(base, 'fixture-token'); "
        "cli._worktrees_get=lambda key: 'fixture-controller'; "
        "cli.main(sys.argv[1:])"
    )
    return [sys.executable, "-c", script, backend.url, "--json"]


def invoke(backend, *, communication=2, process=8, reference="original"):
    argv = [
        *launch_prefix(backend), "send", reference,
        "--prompt-file", "-", "--no-wait",
        "--startup-communication-timeout", str(communication),
        "--startup-process-timeout", str(process),
    ]
    started = time.monotonic()
    result = run_captured(argv, timeout=60, input_bytes="one exact prompt\nline two".encode())
    return result, time.monotonic() - started


def frames(result):
    return [
        json.loads(line.removeprefix(PROGRESS_PREFIX))
        for line in result.stderr.splitlines() if line.startswith(PROGRESS_PREFIX)
    ]


def test_real_cli_forwards_unknown_progress_and_submits_once_with_clean_json():
    with Backend() as backend:
        result, elapsed = invoke(backend)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["outcome"] == "delivered"
        assert backend.resume_calls == 1
        assert len(backend.turns) == 1
        assert backend.turns[0]["prompt"] == "one exact prompt\nline two"
        assert backend.turns[0]["no_resume"] is True
        progress = frames(result)
        assert any(item.get("target_event") == "future-startup-event-v100" for item in progress)
        assert any(item["event"] == "ready" for item in progress)
        assert "never-forward-this" not in result.stderr
        assert elapsed < 60, f"end-to-end CLI startup took {elapsed:.2f}s"


@pytest.mark.parametrize("updates,communication,process,clock", [
    (False, 2, 8, "communication"),
    (True, 2, 2.5, "process"),
])
def test_real_timeout_preserves_target_and_does_not_replay(
    updates, communication, process, clock,
):
    with Backend(updates=updates) as backend:
        result, _ = invoke(backend, communication=communication, process=process)
        assert result.returncode == 70, result.stderr
        payload = json.loads(result.stdout)
        assert payload["outcome"] == "unknown"
        assert payload["reason"] == f"startup_{clock}_timeout"
        assert payload["retryable"] is False
        assert backend.resume_calls == 1
        assert backend.turns == []
        assert backend.resumed.wait(5), "the target must retain the original operation"
        assert backend.state == "idle"


def test_real_event_reconnect_retains_cursor_and_never_resends_resume():
    with Backend(disconnect=True) as backend:
        result, _ = invoke(backend)
        assert result.returncode == 0, result.stderr
        assert backend.resume_calls == 1
        assert len(backend.turns) == 1
        assert len(backend.cursors) >= 2
        assert backend.cursors[0] == 0
        assert backend.cursors[1] >= 1
        assert len(set(backend.audiences)) == 1


def test_lost_prompt_acceptance_is_unknown_without_a_second_submission():
    with Backend(lost_admission=True) as backend:
        result, _ = invoke(backend)
        assert result.returncode == 70, result.stderr
        payload = json.loads(result.stdout)
        assert payload["outcome"] == "unknown"
        assert payload["reason"] == "admission_unknown"
        assert payload["retryable"] is False
        assert backend.resume_calls == 1
        assert len(backend.turns) == 1
        assert backend.turns[0]["no_resume"] is True


@pytest.mark.parametrize("failure", [
    "truncated", "malformed", "empty-status", "invalid-status",
])
def test_real_accepted_prompt_with_bad_ack_is_not_a_delivery_failure(failure):
    with Backend(ack_failure=failure) as backend:
        result, _ = invoke(backend)
        assert result.returncode == 70, result.stderr
        assert json.loads(result.stdout)["reason"] == "admission_unknown"
        assert backend.resume_calls == 1
        assert len(backend.turns) == 1


def dispatch_consumer(monkeypatch):
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "plugins" / "agent-dispatch" / "src"))
    from agent_dispatch import bridge
    assert Path(bridge.__file__).resolve().is_relative_to(root)
    return bridge


def test_actual_dispatch_to_cli_to_http_consumer_starts_once_with_selected_defaults(monkeypatch):
    bridge = dispatch_consumer(monkeypatch)
    with Backend() as backend:
        monkeypatch.setattr(bridge, "_agent_bridge_launch_prefix", lambda: launch_prefix(backend))
        started = time.monotonic()
        assert bridge.resume_worker("original", "one parent continuation\nsecond line") is True
        elapsed = time.monotonic() - started
        assert elapsed < 60, f"composed startup took {elapsed:.2f}s"
        assert backend.resume_calls == 1
        assert len(backend.turns) == 1
        assert backend.turns[0]["prompt"] == "one parent continuation\nsecond line"
        assert backend.turns[0]["no_resume"] is True


def test_actual_dispatch_preserves_uncertain_admission_after_readiness(monkeypatch):
    bridge = dispatch_consumer(monkeypatch)
    with Backend(lost_admission=True) as backend:
        monkeypatch.setattr(bridge, "_agent_bridge_launch_prefix", lambda: launch_prefix(backend))
        with pytest.raises(bridge.StartupOutcomeUnknown) as raised:
            bridge.resume_worker("original", "retain original request", timeout=2)
        assert raised.value.retryable is False
        assert raised.value.phase == "prompt-admission"
        assert backend.resume_calls == 1
        assert len(backend.turns) == 1


@pytest.mark.parametrize("path", ["/health", "/api/v1/sessions/original"])
def test_delayed_preflight_expires_before_any_resume_or_prompt(path):
    with Backend(delayed_preflight=path) as backend:
        result, elapsed = invoke(backend, communication=2)
        assert result.returncode == 70, result.stderr
        payload = json.loads(result.stdout)
        assert payload["reason"] == "startup_communication_timeout"
        assert 2 <= payload["elapsed_s"] < 3
        assert elapsed < 60
        assert backend.preflight_requests == [path]
        assert backend.resume_calls == 0
        assert backend.turns == []


@pytest.mark.parametrize("as_json", [False, True])
def test_real_protocol_refusal_never_replaces_retained_dispatch_session(monkeypatch, as_json):
    bridge = dispatch_consumer(monkeypatch)
    with Backend(protocol=45) as backend:
        prefix = launch_prefix(backend)
        if not as_json:
            prefix = prefix[:-1]
        monkeypatch.setattr(bridge, "_agent_bridge_launch_prefix", lambda: prefix)
        monkeypatch.setattr(bridge, "spawn_worker", lambda *a, **kw: pytest.fail("replacement"))
        with pytest.raises(bridge.BridgeUnavailable, match="absence is unproven"):
            bridge.spawn_or_resume_worker(
                "retained-task", worker_id="worker", prompt="retain original request",
                prior_session_id="original", liveness_fn=lambda _: "gone",
            )
        assert backend.resume_calls == 0
        assert backend.turns == []


@pytest.mark.parametrize("failure", ["no-content", "empty", "wrong-session", "starting"])
def test_incomplete_resume_ack_never_admits_a_followup_prompt(failure):
    with Backend(resume_ack_failure=failure) as backend:
        result, _ = invoke(backend)
        assert result.returncode == 70, result.stderr
        payload = json.loads(result.stdout)
        assert payload["reason"] == "resume_unknown"
        assert payload["retryable"] is False
        assert not any(item["event"] == "ready" for item in frames(result))
        assert backend.resume_calls == 1
        assert backend.resumed.is_set()
        assert backend.turns == []


def test_distinct_native_reference_uses_canonical_bridge_id_before_observing():
    with Backend() as backend:
        result, _ = invoke(backend, reference="native-acp")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["outcome"] == "delivered"
        assert backend.resume_calls == 1
        assert len(backend.turns) == 1
        assert backend.cursors == [0]
