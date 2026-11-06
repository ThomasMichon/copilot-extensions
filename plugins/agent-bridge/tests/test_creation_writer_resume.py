"""Required exact-session resume survives independently updated providers."""

from __future__ import annotations

import io
import json
import threading
from typing import Any
from urllib.parse import urlsplit

import pytest

from agent_bridge import __main__ as core, client_protocol_floor, creation_worktree_writer
from agent_bridge.client import BridgeClient, BridgeClientError, BridgeConnectionError
from agent_bridge.protocol import HTTP_PROTOCOL_VERSION, HTTP_PROTOCOL_MIN_SUPPORTED

pytestmark = pytest.mark.contract("agent_bridge.workspace_creation")

REQUIREMENT = {
    "version": 1,
    "identity": {"installation_root": "/original-owner", "runtime_generation": "a" * 32},
    "creation_nonce": "original-incarnation",
}


@pytest.mark.parametrize("cutover", [None, "initial", "refused", "draining"])
def test_required_resume_real_parser_client_rechecks_provider(
    monkeypatch, capsys, cutover,
):
    client = BridgeClient("http://fixture", token="fixture", connect_grace=1)
    calls = []
    supported = cutover != "initial"
    response = {
        "session_id": "original-session", "acp_session_id": "original-native",
        "durable_session_id": "original-native", "status": "idle",
    }

    def request(method, path, body=None, **kwargs):
        nonlocal supported
        calls.append((method, path, body))
        assert kwargs["allow_retry"] is False
        if path == "/health":
            return {
                "protocol_version": HTTP_PROTOCOL_VERSION,
                "min_protocol_version": HTTP_PROTOCOL_MIN_SUPPORTED,
                "capabilities": {
                    "repository_agent_hosting": 1,
                    creation_worktree_writer.CAPABILITY: 1,
                    **({creation_worktree_writer.RESUME_CAPABILITY: 1} if supported else {}),
                },
            }
        assert (method, path) == ("POST", "/api/v1/sessions/original-session/resume")
        assert body == {"worktree_writer": REQUIREMENT}
        if cutover == "refused":
            supported = False
            failure = BridgeConnectionError("retired provider")
            failure.__cause__ = ConnectionRefusedError("not admitted")
            raise failure
        if cutover == "draining":
            supported = False
            raise BridgeClientError(503, "daemon draining")
        return response

    monkeypatch.setattr(client, "_request", request)
    monkeypatch.setattr(client_protocol_floor.time, "sleep", lambda _: None)
    monkeypatch.setattr(core, "_get_client", lambda: client)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(REQUIREMENT)))
    args = core.build_parser().parse_args([
        "--json", "resume", "original-session", "--strict", "--require-agent-hosting",
        "--worktree-writer-file", "-",
    ])
    if cutover is None:
        args.func(args)
        assert json.loads(capsys.readouterr().out) == {**response, "verb": "resumed"}
    else:
        with pytest.raises(SystemExit) as failure:
            args.func(args)
        assert failure.value.code == 1
        assert "error" in json.loads(capsys.readouterr().out)
    assert len([item for item in calls if item[0] == "POST"]) == (0 if cutover == "initial" else 1)
    assert len([item for item in calls if item[0] == "GET"]) == (
        2 if cutover in ("refused", "draining") else 1
    )


@pytest.mark.parametrize("options,text", [
    ([], "{}"),
    (["--strict", "--cli"], "{}"),
    (["--strict", "--force"], "{}"),
    (["--strict"], "not json"),
    (["--strict"], "{}"),
])
def test_required_resume_invalid_form_refuses_before_discovery(monkeypatch, options, text):
    monkeypatch.setattr(core, "_get_client", lambda: pytest.fail("invalid resume must not discover"))
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    args = core.build_parser().parse_args([
        "resume", "original-session", *options, "--worktree-writer-file", "-",
    ])
    with pytest.raises(SystemExit) as failure:
        args.func(args)
    assert failure.value.code == 2


def test_required_resume_worktree_selector_refuses_before_discovery(monkeypatch):
    monkeypatch.setattr(core, "_get_client", lambda: pytest.fail("must not discover"))
    args = core.build_parser().parse_args([
        "resume", "--worktree-id", "original-worktree", "--strict",
        "--worktree-writer-file", "-",
    ])
    with pytest.raises(SystemExit) as failure:
        args.func(args)
    assert failure.value.code == 2


@pytest.mark.parametrize("response", [404, {"session_id": "replacement"}, {
    "session_id": "original-session", "acp_session_id": "native", "durable_session_id": "other",
}])
def test_required_resume_cannot_fallback_or_accept_replacement(monkeypatch, capsys, response):
    client = BridgeClient("http://fixture", token="fixture", connect_grace=0)
    calls = []

    def request(method, path, body=None, **kwargs):
        calls.append(path)
        if path == "/health":
            return {
                "protocol_version": HTTP_PROTOCOL_VERSION, "min_protocol_version": 1,
                "capabilities": {creation_worktree_writer.RESUME_CAPABILITY: 1},
            }
        assert path == "/api/v1/sessions/original-session/resume"
        if response == 404:
            raise BridgeClientError(404, "not found")
        return response

    monkeypatch.setattr(client, "_request", request)
    monkeypatch.setattr(core, "_get_client", lambda: client)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(REQUIREMENT)))
    args = core.build_parser().parse_args([
        "--json", "resume", "original-session", "--strict", "--worktree-writer-file", "-",
    ])
    with pytest.raises(SystemExit) as failure:
        args.func(args)
    assert failure.value.code == 1
    assert "error" in json.loads(capsys.readouterr().out)
    assert calls == ["/health", "/api/v1/sessions/original-session/resume"]


def test_required_resume_pins_capability_peer_during_observer_cutover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_procutil.startup import StartupTimeouts
    from agent_bridge import startup_observation

    original, replacement = "http://original.invalid", "http://replacement.invalid"
    checking, moved, finished = (threading.Event() for _ in range(3))
    client = BridgeClient(original, "fixture", connect_grace=0, reresolve=lambda: replacement)
    posts: list[str] = []
    refresh = client.refresh_endpoint

    def refresh_endpoint() -> bool:
        changed = refresh()
        moved.set()
        return changed

    class Response(io.BytesIO):
        status = 200

    class Stream:
        def __iter__(self) -> Any:
            assert checking.wait(2)
            if not moved.is_set():
                return
            while not finished.wait(.01):
                yield {"event": "_heartbeat", "data": {}}

        def close(self) -> None:
            pass

    def transport(request: Any, **kwargs: Any) -> Response:
        path = urlsplit(request.full_url).path
        if path.endswith("/cursor"):
            result = {"head_id": 0}
        elif path == "/health":
            assert request.full_url == original + path
            checking.set()
            assert moved.wait(2)
            result = {
                "protocol_version": HTTP_PROTOCOL_VERSION, "min_protocol_version": 1,
                "capabilities": {creation_worktree_writer.RESUME_CAPABILITY: 1},
            }
        else:
            assert path == "/api/v1/sessions/original-session/resume"
            posts.append(request.full_url)
            assert json.loads(request.data) == {"worktree_writer": REQUIREMENT}
            result = {"session_id": "original-session", "status": "idle"}
            finished.set()
        return Response(json.dumps(result).encode())

    monkeypatch.setattr("urllib.request.urlopen", transport)
    monkeypatch.setattr(client, "stream_events", lambda *args, **kwargs: Stream())
    monkeypatch.setattr(client, "refresh_endpoint", refresh_endpoint)
    observer = startup_observation.StartupObservation(StartupTimeouts(3, 5, 1))
    token = startup_observation._active.set(observer)
    try:
        assert client.resume_session(
            "original-session", worktree_writer=REQUIREMENT,
        ) == {"session_id": "original-session", "status": "idle"}
    finally:
        finished.set()
        startup_observation._active.reset(token)
    assert moved.is_set() and client._base == replacement
    assert posts == [original + "/api/v1/sessions/original-session/resume"]
