"""Exercise Bridge's real resume parser, client and CLI response boundary."""

from __future__ import annotations

import argparse
import http.client
import importlib.util
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "promotion_resume_bridge"


def unavailable(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("unexpected external I/O or alternative target selection")


@dataclass
class ResumeBoundary:
    lifecycle: ModuleType
    service: ModuleType
    resume: ModuleType
    contracts: ModuleType
    client: Any
    errors: ModuleType
    calls: list[tuple[str, str, dict[str, Any]]]
    response: dict[str, Any]
    health: dict[str, Any]
    failures: list[BaseException]
    discovered: list[bool]

    def invoke(self, *argv: str) -> None:
        parser = argparse.ArgumentParser(prog="agent-bridge")
        parser.add_argument("--json", action="store_true")
        sub = parser.add_subparsers(dest="command", required=True)
        self.lifecycle.register_session_lifecycle_commands(sub)
        self.service.register_service_start_commands(sub)
        args = parser.parse_args(list(argv))
        args.func(args)


@pytest.fixture
def boundary(monkeypatch: pytest.MonkeyPatch) -> ResumeBoundary:
    def module(name: str, **attributes: Any) -> ModuleType:
        value = ModuleType(name)
        value.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, value)
        return value

    root = ROOT / "plugins/agent-bridge/src/agent_bridge"
    package = module(PACKAGE, __path__=[str(root)], __version__="0.0.synthetic")
    module("uvicorn", run=unavailable)
    module("agent_procutil", __path__=[], no_window_kwargs=unavailable)
    module(
        "agent_procutil.daemon_instances",
        daemon_entrypoint=lambda *args, **kwargs: lambda function: function,
    )
    startup_spec = importlib.util.spec_from_file_location(
        "agent_procutil.startup", ROOT / "libs/agent-procutil/src/agent_procutil/startup.py",
    )
    assert startup_spec and startup_spec.loader
    startup = importlib.util.module_from_spec(startup_spec)
    monkeypatch.setitem(sys.modules, startup_spec.name, startup)
    startup_spec.loader.exec_module(startup)
    module(f"{PACKAGE}.diagnostic_logs", register_log_commands=lambda sub: None)
    module(
        f"{PACKAGE}.agent_registry",
        _agent_worktrees_bin=unavailable,
        _normalize_repo_basename=unavailable,
    )
    module(f"{PACKAGE}.provider_process", run_captured=unavailable)

    def load(child: str) -> ModuleType:
        name = f"{PACKAGE}.{child}"
        spec = importlib.util.spec_from_file_location(name, root / f"{child}.py")
        assert spec and spec.loader
        result = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, result)
        spec.loader.exec_module(result)
        monkeypatch.setattr(package, child, result, raising=False)
        return result

    for child in (
        "session_status", "caller_session", "protocol", "client_worktree_restart",
        "client_answers", "client_cli_mode", "client_protocol_floor", "client_live_results",
        "client_prompt", "client_session_start", "client_drain", "client_session_stop",
        "client_worktrees", "_peer_launch", "singleton_anchor", "restart_worktree_cli",
        "hosting_admission_cli", "startup_observation",
    ):
        load(child)
    errors = load("client")
    lifecycle = load("session_lifecycle_cli")
    service = load("service_start_cli")
    resume = load("resume_handoff_cli")
    contracts = load("resume_cli_contract")
    client = errors.BridgeClient("http://example.invalid", "synthetic", connect_grace=60)
    calls: list[tuple[str, str, dict[str, Any]]] = []
    response: dict[str, Any] = {
        "session_id": "owned-123",
        "acp_session_id": "native-123",
        "durable_session_id": "native-123",
        "worktree_id": "worktree-123",
        "mode": "acp",
        "status": "idle",
        "project": None,
        "target_dir": "/example/workspace",
        "read_only": False,
        "at_rest": False,
        "turn_count": 0,
        "liveness": None,
        "future_metadata": {"opaque": [None, False, 0]},
    }
    health: dict[str, Any] = {
        "protocol_version": 40,
        "min_protocol_version": 1,
        "capabilities": {"repository_agent_hosting": 1},
    }
    failures: list[BaseException] = []

    def request(method: str, path: str, *args: Any, **kwargs: Any) -> Any:
        calls.append((method, path, kwargs))
        if method == "GET" and path == "/health":
            return health.copy()
        if failures:
            raise failures.pop(0)
        return response.copy()

    monkeypatch.setattr(client, "_request", request)
    monkeypatch.setattr(resume, "find_singleton_repo", unavailable)
    discovered: list[bool] = []

    def get_client() -> Any:
        discovered.append(True)
        return client

    core = module(
        f"{PACKAGE}.__main__",
        _get_client=get_client,
        _json_out=lambda result: print(json.dumps(result)),
        _match_agents=unavailable,
        _startup_request_timeout=lambda **kwargs: 17.0,
        _add_stream_args=lambda parser: None,
    )
    monkeypatch.setattr(package, "__main__", core, raising=False)
    return ResumeBoundary(
        lifecycle, service, resume, contracts, client, errors,
        calls, response, health, failures, discovered,
    )


@pytest.mark.parametrize("cli", [False, True], ids=["acp", "cli"])
@pytest.mark.parametrize("force", [False, True], ids=["ordinary", "reclaim"])
def test_exact_selector_uses_only_the_actual_worktree_route(
    boundary: ResumeBoundary, capsys: Any, cli: bool, force: bool,
) -> None:
    if cli:
        boundary.response.update(
            session_id="native-123", mode="cli", status="starting", read_only=True,
        )
    args = ["--json", "resume", "--worktree-id", "worktree-123", "--strict"]
    args += ["--cli"] if cli else []
    args += ["--force"] if force else []
    boundary.invoke(*args)
    assert json.loads(capsys.readouterr().out) == {
        **boundary.response, "verb": "took over" if force else "loaded",
    }
    effects = [call for call in boundary.calls if call[0] != "GET"]
    assert effects == [(
        "POST", f"/api/v1/worktrees/worktree-123/resume/{'cli' if cli else 'acp'}",
        {
            "params": {"strict": "true", **({"reclaim": "true"} if force else {})},
            "request_timeout": 17.0, "allow_retry": False,
        },
    )]
    assert all(path == "/health" for method, path, _ in boundary.calls if method == "GET")


@pytest.mark.parametrize(
    "args",
    [
        [], ["alias", "--worktree-id", "worktree-123"],
        ["--worktree-id", ""], ["--worktree-id", " "],
        ["--worktree-id", "../worktree"], ["--worktree-id", "repo@anchor"],
        ["--worktree-id", "a/b"], ["--worktree-id", "a" * 201],
        ["--worktree-id", "worktree-123", "--unknown-option"],
    ],
    ids=["missing", "exclusive", "empty", "blank", "parent", "anchor", "path", "long", "unknown"],
)
def test_invalid_selector_refuses_before_client_discovery(
    boundary: ResumeBoundary, args: list[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        boundary.invoke("resume", *args)
    assert raised.value.code == 2
    assert boundary.calls == []
    assert boundary.discovered == []


@pytest.mark.parametrize("value", ["", " ", "../worktree"], ids=["empty", "blank", "path"])
def test_direct_handler_cannot_bypass_pre_io_validation(
    boundary: ResumeBoundary, value: str,
) -> None:
    args = argparse.Namespace(session_id=None, worktree_id=value)
    with pytest.raises(SystemExit) as raised:
        boundary.lifecycle._cmd_resume(args)
    assert raised.value.code == 2
    assert boundary.calls == []
    assert boundary.discovered == []


@pytest.mark.parametrize("status", [404, 409, 426, 503])
def test_exact_refusal_never_selects_an_alternative_or_replays(
    boundary: ResumeBoundary, capsys: Any, status: int,
) -> None:
    boundary.failures.append(boundary.errors.BridgeClientError(
        status, {"reason": "owner_refused", "detail": "synthetic refusal"},
    ))
    with pytest.raises(SystemExit) as raised:
        boundary.invoke("--json", "resume", "--worktree-id", "worktree-123", "--strict")
    assert raised.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert result["reason"] == "owner_refused"
    assert "synthetic refusal" in result["error"]
    assert len([call for call in boundary.calls if call[0] == "POST"]) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"worktree_id": "other"}, {"worktree_id": None}, {"session_id": ""},
        {"acp_session_id": None}, {"durable_session_id": "other"}, {"mode": "cli"},
    ],
    ids=["worktree", "missing-worktree", "session", "native", "durable", "mode"],
)
def test_unproven_response_is_unknown_not_replaced(
    boundary: ResumeBoundary, capsys: Any, change: dict[str, Any],
) -> None:
    boundary.response.update(change)
    with pytest.raises(SystemExit) as raised:
        boundary.invoke("--json", "resume", "--worktree-id", "worktree-123")
    assert raised.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert result["reason"] == "resume_response_unconfirmed"
    assert "may have started" in result["error"]
    assert len([call for call in boundary.calls if call[0] == "POST"]) == 1


def test_connection_loss_is_unconfirmed_and_never_replayed(
    boundary: ResumeBoundary, capsys: Any,
) -> None:
    boundary.failures.append(boundary.errors.BridgeConnectionError("response lost"))
    with pytest.raises(SystemExit):
        boundary.invoke("--json", "resume", "--worktree-id", "worktree-123")
    result = json.loads(capsys.readouterr().out)
    assert result["reason"] == "resume_outcome_unknown"
    assert "may have started" in result["error"]
    assert len([call for call in boundary.calls if call[0] == "POST"]) == 1


@pytest.mark.parametrize("version", [0, 39], ids=["unversioned", "prior-native-contract"])
def test_unsupported_daemon_refuses_before_effect(
    boundary: ResumeBoundary, capsys: Any, version: int,
) -> None:
    boundary.health["protocol_version"] = version
    boundary.invoke("version", "--contracts-json")
    assert "exact-worktree-target" in capsys.readouterr().out
    assert boundary.calls == []
    with pytest.raises(SystemExit):
        boundary.invoke("--json", "resume", "--worktree-id", "worktree-123")
    assert "cannot preserve an authoritative native head" in capsys.readouterr().out
    assert all(method == "GET" for method, _, _ in boundary.calls)


@pytest.mark.parametrize("range_kind", ["overlap", "disjoint", "invalid"])
def test_resume_requires_shared_protocol_range(
    boundary: ResumeBoundary, capsys: Any, range_kind: str,
) -> None:
    protocol = sys.modules[f"{PACKAGE}.protocol"]
    boundary.health.update(
        protocol_version=protocol.HTTP_PROTOCOL_VERSION + 2,
        min_protocol_version={
            "overlap": 1, "disjoint": protocol.HTTP_PROTOCOL_VERSION + 1, "invalid": -1,
        }[range_kind],
    )
    supported = range_kind == "overlap"
    assert boundary.client.daemon_supports(40) is supported
    if supported:
        boundary.client.assert_client_supported()
        boundary.invoke("--json", "resume", "--worktree-id", "worktree-123")
        assert json.loads(capsys.readouterr().out)["worktree_id"] == "worktree-123"
        assert len([call for call in boundary.calls if call[0] == "POST"]) == 1
    else:
        with pytest.raises(boundary.errors.BridgeClientError) as raised:
            boundary.client.assert_client_supported()
        assert raised.value.status == 426
        with pytest.raises(SystemExit):
            boundary.invoke("--json", "resume", "--worktree-id", "worktree-123")
        assert "error" in json.loads(capsys.readouterr().out)
        assert all(method == "GET" for method, _, _ in boundary.calls)


def test_effectful_retry_rechecks_both_supported_range_endpoints(
    boundary: ResumeBoundary, monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol = sys.modules[f"{PACKAGE}.protocol"]
    floor = sys.modules[f"{PACKAGE}.client_protocol_floor"]
    failure = boundary.errors.BridgeConnectionError("retired")
    failure.__cause__ = ConnectionRefusedError("not admitted")
    boundary.failures.append(failure)
    original = boundary.client._request

    def replacing_request(*args: Any, **kwargs: Any) -> Any:
        try:
            return original(*args, **kwargs)
        except boundary.errors.BridgeConnectionError:
            boundary.health.update(
                protocol_version=protocol.HTTP_PROTOCOL_VERSION + 2,
                min_protocol_version=protocol.HTTP_PROTOCOL_VERSION + 1,
            )
            raise

    monkeypatch.setattr(boundary.client, "_request", replacing_request)
    monkeypatch.setattr(floor.time, "sleep", lambda _: None)
    request = floor.protocol_floored_request(boundary.client, 40)
    with pytest.raises(boundary.errors.BridgeClientError) as raised:
        request("POST", "/api/v1/sessions", {"agent": "worker"})
    assert raised.value.status == 426
    assert "no compatible" in str(raised.value)
    assert [method for method, _, _ in boundary.calls] == ["GET", "POST", "GET"]


@pytest.mark.parametrize("failure", ["refused", "lost", "downgraded", "capability"])
def test_hosting_fence_survives_exact_selection_without_effect_retry(
    boundary: ResumeBoundary, capsys: Any, failure: str,
) -> None:
    if failure == "refused":
        boundary.failures.append(boundary.errors.BridgeClientError(503, "draining"))
    elif failure == "lost":
        boundary.failures.append(boundary.errors.BridgeConnectionError("response lost"))
    elif failure == "downgraded":
        boundary.health["protocol_version"] = 39
    else:
        boundary.health["capabilities"] = {}
    with pytest.raises(SystemExit):
        boundary.invoke(
            "--json", "resume", "--worktree-id", "worktree-123",
            "--require-agent-hosting", "--strict",
        )
    assert "error" in json.loads(capsys.readouterr().out)
    assert len([call for call in boundary.calls if call[0] == "POST"]) <= 1
    assert all(call[2]["allow_retry"] is False for call in boundary.calls)


def test_legacy_positional_owned_session_still_wins(
    boundary: ResumeBoundary, capsys: Any,
) -> None:
    boundary.invoke("--json", "resume", "alias")
    assert boundary.calls == [(
        "POST", "/api/v1/sessions/alias/resume", {"request_timeout": 17.0},
    )]
    assert json.loads(capsys.readouterr().out) == {**boundary.response, "verb": "resumed"}


def test_legacy_positional_singleton_still_canonicalizes(
    boundary: ResumeBoundary, monkeypatch: pytest.MonkeyPatch, capsys: Any,
) -> None:
    monkeypatch.setattr(
        boundary.resume, "find_singleton_repo",
        lambda value: SimpleNamespace(name="example") if value == "example" else None,
    )
    monkeypatch.setattr(boundary.client, "list_agents", lambda: [])
    boundary.failures.append(boundary.errors.BridgeClientError(404, "not owned"))
    boundary.invoke("--json", "resume", "example")
    effects = [call for call in boundary.calls if call[0] == "POST"]
    assert [call[1] for call in effects] == [
        "/api/v1/sessions/example/resume", "/api/v1/worktrees/example@anchor/resume/acp",
    ]
    assert effects[-1][2]["allow_retry"] is True
    result = json.loads(capsys.readouterr().out)
    assert result["requested_target"] == "example"
    assert result["worktree_id"] == boundary.response["worktree_id"]


@pytest.mark.parametrize("global_json", [False, True], ids=["plain", "global-json"])
def test_legacy_version_is_pure_and_unchanged(
    boundary: ResumeBoundary, capsys: Any, global_json: bool,
) -> None:
    boundary.invoke(*(["--json"] if global_json else []), "version")
    assert capsys.readouterr().out == "agent-bridge 0.0.synthetic\n"
    assert boundary.calls == []
    assert boundary.discovered == []


def test_executing_cli_disclosure_matches_actual_parser(
    boundary: ResumeBoundary, capsys: Any,
) -> None:
    boundary.invoke("version", "--contracts-json")
    assert json.loads(capsys.readouterr().out) == {
        "schema": "agent-bridge.cli-contracts", "version": 1,
        "contracts": {
            "agent-bridge.resume": {"version": 1, "capabilities": ["exact-worktree-target"]},
        },
    }
    assert boundary.calls == []
    assert boundary.discovered == []
    boundary.invoke("--json", "resume", "--worktree-id", "worktree-123")
    assert json.loads(capsys.readouterr().out)["worktree_id"] == "worktree-123"


@pytest.mark.parametrize("result", [None, [], "invalid", 1], ids=["empty", "list", "text", "number"])
def test_non_object_response_is_not_success(
    boundary: ResumeBoundary, monkeypatch: pytest.MonkeyPatch, capsys: Any, result: Any,
) -> None:
    def request(method: str, path: str, *args: Any, **kwargs: Any) -> Any:
        boundary.calls.append((method, path, kwargs))
        return boundary.health if method == "GET" else result

    monkeypatch.setattr(boundary.client, "_request", request)
    with pytest.raises(SystemExit):
        boundary.invoke("--json", "resume", "--worktree-id", "worktree-123")
    assert json.loads(capsys.readouterr().out)["reason"] == "resume_response_unconfirmed"
    assert len([call for call in boundary.calls if call[0] == "POST"]) == 1


def test_cli_response_cannot_substitute_an_escrow_identity(
    boundary: ResumeBoundary, capsys: Any,
) -> None:
    boundary.response["mode"] = "cli"
    with pytest.raises(SystemExit):
        boundary.invoke("--json", "resume", "--worktree-id", "worktree-123", "--cli")
    assert json.loads(capsys.readouterr().out)["reason"] == "resume_response_unconfirmed"
    assert len([call for call in boundary.calls if call[0] == "POST"]) == 1


def test_exact_hosting_success_keeps_the_receivers_capability_fence(
    boundary: ResumeBoundary, capsys: Any,
) -> None:
    boundary.invoke(
        "--json", "resume", "--worktree-id", "worktree-123", "--require-agent-hosting",
    )
    assert json.loads(capsys.readouterr().out) == {**boundary.response, "verb": "loaded"}
    assert len([call for call in boundary.calls if call[0] == "POST"]) == 1
    assert all(call[2]["allow_retry"] is False for call in boundary.calls)


def test_disclosure_keeps_unknown_argument_rejection(
    boundary: ResumeBoundary, capsys: Any,
) -> None:
    with pytest.raises(SystemExit) as raised:
        boundary.invoke("version", "--contracts-json", "--unknown-option")
    assert raised.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err
    assert boundary.calls == []
    assert boundary.discovered == []


@pytest.mark.parametrize(
    "argv", [
        ["resume", "--worktree-id", "worktree-123"],
        ["version", "--contracts-json"],
    ], ids=["exact-selector", "support-disclosure"],
)
def test_selected_cli_rollback_refuses_unsupported_syntax_before_dispatch(
    boundary: ResumeBoundary, monkeypatch: pytest.MonkeyPatch, capsys: Any, argv: list[str],
) -> None:
    boundary.invoke("version", "--contracts-json")
    assert "exact-worktree-target" in capsys.readouterr().out
    path = ROOT / "tools/promotion_unit_tests/fixtures/bridge_resume_parser_v0.py"
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.prior_cli", path)
    assert spec and spec.loader
    prior = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, prior)
    spec.loader.exec_module(prior)
    parser = argparse.ArgumentParser(prog="agent-bridge")
    prior.register(parser.add_subparsers(dest="command", required=True))
    with pytest.raises(SystemExit) as raised:
        parser.parse_args(argv)
    assert raised.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err
    assert boundary.calls == []
    assert boundary.discovered == []


@pytest.mark.parametrize("hosting", [False, True], ids=["ordinary", "hosting-fenced"])
def test_truncated_http_body_is_unconfirmed_without_replay(
    boundary: ResumeBoundary, monkeypatch: pytest.MonkeyPatch, capsys: Any, hosting: bool,
) -> None:
    requests: list[tuple[str, str]] = []

    def urlopen(request: Any, *, timeout: float) -> http.client.HTTPResponse:
        requests.append((request.get_method(), request.full_url))
        if request.get_method() == "POST":
            assert timeout == 17.0
            wire = b'HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n{"session'
        else:
            body = json.dumps(boundary.health).encode()
            wire = (
                b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode()
                + b"\r\n\r\n" + body
            )
        response = http.client.HTTPResponse(
            SimpleNamespace(makefile=lambda mode: io.BytesIO(wire)),
        )
        response.begin()
        return response

    monkeypatch.setattr(boundary.errors.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(
        boundary.client, "_request",
        boundary.errors.BridgeClient._request.__get__(boundary.client),
    )
    args = ["--json", "resume", "--worktree-id", "worktree-123", "--strict"]
    args += ["--require-agent-hosting"] if hosting else []
    with pytest.raises(SystemExit) as raised:
        boundary.invoke(*args)
    assert raised.value.code == 1
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["reason"] == "resume_outcome_unknown"
    assert "IncompleteRead" in result["error"]
    assert "may have started" in result["error"]
    assert "Traceback" not in output.err
    assert [url for method, url in requests if method == "POST"] == [
        "http://example.invalid/api/v1/worktrees/worktree-123/resume/acp?strict=true",
    ]
