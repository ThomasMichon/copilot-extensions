"""Exercise Bridge's real resume parser, client and CLI response boundary."""

from __future__ import annotations

import argparse
import ast
import http.client
import importlib.util
import io
import json
import socket
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import CodeType, ModuleType, SimpleNamespace
from typing import Any, cast

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


@pytest.fixture(scope="session")
def source_code() -> Callable[[Path], CodeType]:
    compiled: dict[Path, CodeType] = {}

    def load(path: Path) -> CodeType:
        if path not in compiled:
            compiled[path] = compile(path.read_bytes(), str(path), "exec")
        return compiled[path]

    return load


@pytest.fixture
def boundary(
    monkeypatch: pytest.MonkeyPatch, source_code: Callable[[Path], CodeType],
) -> ResumeBoundary:
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
    exec(source_code(ROOT / "libs/agent-procutil/src/agent_procutil/startup.py"), startup.__dict__)
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
        exec(source_code(root / f"{child}.py"), result.__dict__)
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

    def get_client(**kwargs: Any) -> Any:
        assert kwargs in ({}, {"ensure": False}, {"ensure": False, "propagate_errors": True})
        discovered.append(True)
        return client

    core = module(
        f"{PACKAGE}.__main__",
        _get_client=get_client,
        _json_out=lambda result: print(json.dumps(result)),
        _match_agents=unavailable,
        _startup_request_timeout=lambda **kwargs: 17.0,
        _add_stream_args=lambda parser: None,
        _cmd_session_status=service._cmd_session_status,
        _sender_repo=lambda: "example",
    )
    monkeypatch.setattr(package, "__main__", core, raising=False)
    return ResumeBoundary(
        lifecycle, service, resume, contracts, client, errors,
        calls, response, health, failures, discovered,
    )


@pytest.fixture
def bridge_resume(
    boundary: ResumeBoundary, monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    module = ModuleType(f"{PACKAGE}.remote_errors")
    module.__dict__["RemoteBridgeError"] = RuntimeError
    monkeypatch.setitem(sys.modules, module.__name__, module)
    boundary.health.update(
        protocol_version=52, capabilities={"bridge_inventory": 2, "worktree_exact_resume": 1},
    )
    report: dict[str, Any] = {
        "version": 1, "complete": True, "sources": {},
        "bridges": [{
            "handle": "worktree-123", "worktree_id": "worktree-123",
            "session_id": "native-123", "managed_session_id": "owned-123",
            "status": "dormant", "control": "unavailable",
        }],
    }

    def request(method: str, path: str, *args: Any, **kwargs: Any) -> Any:
        boundary.calls.append((method, path, {**kwargs, "body": args}))
        if method == "GET":
            if path == "/health":
                return boundary.health.copy()
            assert path == "/api/v1/bridges"
            return report
        assert path.endswith("/exact")
        if boundary.failures:
            raise boundary.failures.pop(0)
        return boundary.response.copy()

    monkeypatch.setattr(boundary.client, "_request", request)
    for name in ("resume_session", "resume_worktree", "list_agents"):
        monkeypatch.setattr(boundary.client, name, unavailable)
    return report


@pytest.mark.parametrize("cli", [False, True], ids=["acp", "cli"])
@pytest.mark.parametrize("target", ["worktree", "native", "managed", "singleton"])
def test_bridge_resume_pins_the_selected_native_without_legacy_fallback(
    boundary: ResumeBoundary, bridge_resume: dict[str, Any], capsys: Any,
    cli: bool, target: str,
) -> None:
    row = bridge_resume["bridges"][0]
    if target == "singleton":
        row.update(handle="example@anchor", worktree_id="example@anchor")
        boundary.response["worktree_id"] = "example@anchor"
    if cli:
        boundary.response.update(mode="cli", session_id="native-123")
    identity = {
        "worktree": "worktree-123", "native": "native-123",
        "managed": "owned-123", "singleton": "example@anchor",
    }[target]
    boundary.invoke("--json", "resume", identity, "--bridge", *(["--cli"] if cli else []))
    assert json.loads(capsys.readouterr().out) == {**boundary.response, "verb": "loaded"}
    effects = [call for call in boundary.calls if call[0] == "POST"]
    assert len(effects) == 1
    assert effects[0][1] == (
        f"/api/v1/worktrees/{'example%40anchor' if target == 'singleton' else 'worktree-123'}"
        f"/resume/{'cli' if cli else 'acp'}/exact"
    )
    assert effects[0][2]["body"] == ({"native_session_id": "native-123"},)
    assert effects[0][2]["allow_retry"] is False
    assert effects[0][2]["request_timeout"] == 17.0
    assert effects[0][2]["_endpoint"] == "http://example.invalid"
    selection = next(call for call in boundary.calls if call[1] == "/api/v1/bridges")
    assert selection[2]["params"] == {"project": "example", "local_only": "true"}


def test_bridge_worktree_selector_and_explicit_scope_reach_common_owner(
    boundary: ResumeBoundary, bridge_resume: dict[str, Any], capsys: Any,
) -> None:
    boundary.invoke(
        "--json", "resume", "--worktree-id", "worktree-123", "--bridge",
        "--all-projects", "--machine", "machine-a", "--namespace", "container:example",
    )
    assert json.loads(capsys.readouterr().out)["worktree_id"] == "worktree-123"
    selection = next(call for call in boundary.calls if call[1] == "/api/v1/bridges")
    assert selection[2]["params"] == {
        "machine": "machine-a", "namespace": "container:example", "local_only": "true",
    }


@pytest.mark.parametrize("options", [
    ["--force"], ["--reclaim"], ["--worktree-writer-file", "unread.json"],
    ["--retained-recovery-file", "-"],
], ids=["force", "reclaim", "writer", "recovery"])
def test_bridge_resume_rejects_specialized_inputs_before_file_or_client_io(
    boundary: ResumeBoundary, monkeypatch: pytest.MonkeyPatch, options: list[str],
) -> None:
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(read=unavailable))
    with pytest.raises(SystemExit) as refused:
        boundary.invoke("resume", "worktree-123", "--bridge", *options)
    assert refused.value.code == 2
    assert boundary.discovered == [] and boundary.calls == []


@pytest.mark.parametrize("options", [
    ["--all-projects"], ["--machine", "machine-a"], ["--namespace", "container:example"],
], ids=["all-projects", "machine", "namespace"])
def test_legacy_resume_cannot_silently_ignore_bridge_scope(
    boundary: ResumeBoundary, options: list[str],
) -> None:
    with pytest.raises(SystemExit) as refused:
        boundary.invoke("resume", "worktree-123", *options)
    assert refused.value.code == 2
    assert boundary.discovered == [] and boundary.calls == []


@pytest.mark.parametrize("fault", ["absent", "ambiguous", "incomplete", "standalone", "no-native"])
def test_bridge_resume_unavailable_selection_has_no_lifecycle_effect(
    boundary: ResumeBoundary, bridge_resume: dict[str, Any], capsys: Any, fault: str,
) -> None:
    if fault in {"absent", "incomplete"}:
        bridge_resume["bridges"] = []
        bridge_resume["complete"] = fault != "incomplete"
    elif fault == "ambiguous":
        bridge_resume["bridges"] *= 2
    else:
        bridge_resume["bridges"][0]["worktree_id" if fault == "standalone" else "session_id"] = None
    with pytest.raises(SystemExit) as refused:
        boundary.invoke("--json", "resume", "worktree-123", "--bridge")
    assert refused.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert result["reason"] == {
        "absent": "bridge_not_found", "ambiguous": "ambiguous_bridge_selector",
        "incomplete": "bridge_inventory_incomplete", "standalone": "bridge_resume_unavailable",
        "no-native": "bridge_resume_unavailable",
    }[fault]
    assert all(call[0] == "GET" for call in boundary.calls)


@pytest.mark.parametrize("capability", [None, False, 0, "1"])
def test_bridge_resume_prior_provider_refuses_before_mutation(
    boundary: ResumeBoundary, bridge_resume: dict[str, Any], capsys: Any, capability: Any,
) -> None:
    boundary.health["capabilities"]["worktree_exact_resume"] = capability
    with pytest.raises(SystemExit) as refused:
        boundary.invoke("--json", "resume", "worktree-123", "--bridge")
    assert refused.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert result["reason"] == "bridge_resume_unavailable"
    assert "required capability worktree_exact_resume" in result["error"]
    assert all(call[0] == "GET" for call in boundary.calls)


@pytest.mark.parametrize("fault", ["head-changed", "rollback", "connection", "invalid-native"])
def test_bridge_resume_retains_original_identity_after_refusal_or_unknown(
    boundary: ResumeBoundary, bridge_resume: dict[str, Any], capsys: Any, fault: str,
) -> None:
    if fault == "invalid-native":
        boundary.response["acp_session_id"] = "other"
    elif fault == "connection":
        boundary.failures.append(boundary.errors.BridgeConnectionError("response lost"))
    else:
        boundary.failures.append(boundary.errors.BridgeClientError(
            409 if fault == "head-changed" else 404,
            {"reason": "native_identity_changed" if fault == "head-changed" else "route_rolled_back"},
        ))
    with pytest.raises(SystemExit) as refused:
        boundary.invoke("--json", "resume", "worktree-123", "--bridge")
    assert refused.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert result["worktree_id"] == "worktree-123" and result["native_session_id"] == "native-123"
    assert result["reason"] == {
        "head-changed": "native_identity_changed", "rollback": "route_rolled_back",
        "connection": "native_resume_unconfirmed", "invalid-native": "native_resume_unconfirmed",
    }[fault]
    assert len([call for call in boundary.calls if call[0] == "POST"]) == 1


@pytest.mark.parametrize("stage", [1, 2], ids=["selection", "exact-resume"])
def test_bridge_resume_health_deadline_remains_structured_without_post_or_retry(
    boundary: ResumeBoundary, bridge_resume: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch, capsys: Any, stage: int,
) -> None:
    deadline = importlib.import_module(f"{PACKAGE}.client_read_budget").ReadDeadlineExpired
    original = boundary.client._request
    health_checks = 0

    def request(method: str, path: str, *args: Any, **kwargs: Any) -> Any:
        nonlocal health_checks
        if path == "/health":
            health_checks += 1
            if health_checks == stage:
                raise deadline("health negotiation deadline expired")
        return original(method, path, *args, **kwargs)

    monkeypatch.setattr(boundary.client, "_request", request)
    with pytest.raises(SystemExit) as refused:
        boundary.invoke("--json", "resume", "worktree-123", "--bridge")
    assert refused.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert result["reason"] == "bridge_resume_unavailable"
    assert result["requested_target"] == "worktree-123"
    assert "health negotiation deadline expired" in result["error"]
    if stage == 2:
        assert result["worktree_id"] == "worktree-123"
        assert result["native_session_id"] == "native-123"
    assert health_checks == stage
    assert all(call[0] == "GET" for call in boundary.calls)


@pytest.mark.parametrize("stage", ["health", "inventory", "exact-health"])
@pytest.mark.parametrize("fault", ["timeout", "truncated"])
def test_bridge_resume_real_response_read_failure_is_json_without_resume_or_retry(
    boundary: ResumeBoundary, bridge_resume: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch, capsys: Any, stage: str, fault: str,
) -> None:
    path = ROOT / "plugins/agent-bridge/src/agent_bridge/__main__.py"
    node = next(
        node for node in ast.parse(path.read_bytes()).body
        if isinstance(node, ast.FunctionDef) and node.name == "_get_client"
    )
    scope: dict[str, Any] = {
        "__package__": PACKAGE, "BridgeClient": boundary.errors.BridgeClient,
        "_ensure_daemon": unavailable,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), scope)
    monkeypatch.setattr(sys.modules[f"{PACKAGE}.__main__"], "_get_client", scope["_get_client"])
    monkeypatch.setattr(
        boundary.errors.BridgeClient, "from_config",
        classmethod(lambda cls: boundary.client),
    )
    requests: list[tuple[str, str]] = []
    failed_reads = 0
    health_checks = 0

    def urlopen(request: Any, *, timeout: float) -> io.BytesIO:
        nonlocal health_checks
        inventory = "/api/v1/bridges" in request.full_url
        if not inventory:
            health_checks += 1
        requests.append((request.get_method(), request.full_url))
        assert request.get_method() == "GET"
        value = bridge_resume if inventory else boundary.health
        stalled = (
            stage == "health" and health_checks == 1
            or stage == "inventory" and inventory
            or stage == "exact-health" and not inventory and health_checks == 3
        )

        class Response(io.BytesIO):
            status = 200

            def read(self, size: Any = -1) -> bytes:
                nonlocal failed_reads
                if stalled:
                    failed_reads += 1
                    if fault == "timeout":
                        raise TimeoutError("response body timed out")
                    raise http.client.IncompleteRead(b"partial", 12)
                return super().read(size)

        return Response(json.dumps(value).encode())

    monkeypatch.setattr(boundary.errors.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(
        boundary.errors.urllib.request, "build_opener",
        lambda *args, **kwargs: SimpleNamespace(open=urlopen),
    )
    monkeypatch.setattr(
        boundary.client, "_request",
        boundary.errors.BridgeClient._request.__get__(boundary.client),
    )
    with pytest.raises(SystemExit) as refused:
        boundary.invoke("--json", "resume", "worktree-123", "--bridge")
    assert refused.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert result["reason"] == "bridge_resume_unavailable"
    assert result["requested_target"] == "worktree-123"
    expected_error = (
        "protocol check" if fault == "timeout" and stage == "exact-health"
        else "response body timed out" if fault == "timeout" else "IncompleteRead"
    )
    assert expected_error in result["error"]
    if stage == "exact-health":
        assert result["native_session_id"] == "native-123"
        assert result["worktree_id"] == "worktree-123"
    else:
        assert "native_session_id" not in result
    assert failed_reads == 1
    assert len(requests) == {"health": 1, "inventory": 3, "exact-health": 4}[stage]


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
            "agent-bridge.send": {
                "version": 1, "capabilities": ["native-send-settlement"],
            },
            "agent-bridge.create": {
                "version": 1, "capabilities": ["keyed-worktree-writer", "keyed-dispatch-origin"],
            },
            "agent-bridge.acquire": {
                "version": 1, "capabilities": ["dispatch-acp-session-origin", "dispatch-cli-session-origin"],
            },
            "agent-bridge.status": {
                "version": 1, "capabilities": [
                    "health-json", "exact-session-worktree-writer", "retained-native-recovery",
                    "exact-session-absence",
                ],
            },
            "agent-bridge.resume": {
                "version": 1, "capabilities": [
                    "exact-worktree-target", "keyed-worktree-writer", "retained-native-recovery",
                    "exact-bridge-workspace-resume",
                ],
            },
            "bridge-session-current-requests-v1": {
                "version": 1, "capabilities": ["observe-session-current-requests"],
            },
            "bridge-session-tool-statistics-v1": {
                "version": 1, "capabilities": ["observe-session-tool-statistics"],
            },
        },
    }
    assert boundary.calls == []
    assert boundary.discovered == []
    boundary.invoke("--json", "resume", "--worktree-id", "worktree-123")
    assert json.loads(capsys.readouterr().out)["worktree_id"] == "worktree-123"


@pytest.mark.parametrize("ready", [True, False], ids=["ready", "unready"])
def test_health_json_preserves_actual_response_without_ensuring(
    boundary: ResumeBoundary, capsys: Any, ready: bool,
) -> None:
    boundary.health.update(
        ready=ready, draining=not ready,
        worktree_record_writer={"runtime_generation": "original-build"},
        future_metadata={"opaque": [None, False, 0]},
    )
    boundary.invoke("--json", "status")
    assert json.loads(capsys.readouterr().out) == boundary.health
    assert boundary.calls == [("GET", "/health", {})]
    assert boundary.discovered == [True]


def test_human_health_output_keeps_legacy_format(boundary: ResumeBoundary, capsys: Any) -> None:
    boundary.invoke("status")
    assert capsys.readouterr().out == (
        "[OK] agent-bridge is running -- agent-bridge (http://example.invalid)\n"
    )
    assert boundary.calls == [("GET", "/health", {})]


@pytest.mark.parametrize("status", [404, 409, 503])
def test_full_json_status_distinguishes_absence_from_other_failures(
    boundary: ResumeBoundary, capsys: Any, status: int,
) -> None:
    boundary.failures.append(boundary.errors.BridgeClientError(status, "unavailable"))
    with pytest.raises(SystemExit) as error:
        boundary.invoke("--json", "status", "owned-123", "--full")
    assert error.value.code == 1
    captured = capsys.readouterr()
    if status == 404:
        assert json.loads(captured.out) == {
            "outcome": "refused_unavailable", "reason": "native_session_unavailable",
            "target": "owned-123",
        }
        assert not captured.err
    else:
        assert not captured.out and captured.err
    assert boundary.calls == [("GET", "/api/v1/sessions/owned-123", {})]


def test_health_json_keeps_legacy_provider_support_unknown(
    boundary: ResumeBoundary, capsys: Any,
) -> None:
    boundary.health.clear()
    boundary.health["service"] = "legacy"
    boundary.invoke("--json", "status")
    assert json.loads(capsys.readouterr().out) == {"service": "legacy"}
    assert boundary.calls == [("GET", "/health", {})]


def test_health_json_failure_emits_no_success_document(
    boundary: ResumeBoundary, monkeypatch: pytest.MonkeyPatch, capsys: Any,
) -> None:
    monkeypatch.setattr(
        boundary.client, "health",
        lambda: (_ for _ in ()).throw(boundary.errors.BridgeClientError(503, "draining")),
    )
    with pytest.raises(SystemExit) as raised:
        boundary.invoke("--json", "status")
    captured = capsys.readouterr()
    assert raised.value.code == 1 and not captured.out and "draining" in captured.err
    assert boundary.calls == []


@pytest.mark.parametrize("argv", [
    ["status", "--worktree-writer"],
    ["status", "owned-123", "--worktree-writer"],
    ["status", "--full", "--worktree-writer"],
], ids=["no-session", "compact", "no-session-full"])
def test_writer_status_rejects_incomplete_form_before_discovery(
    boundary: ResumeBoundary, capsys: Any, argv: list[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        boundary.invoke(*argv)
    assert raised.value.code == 2 and "--full" in capsys.readouterr().err
    assert not boundary.discovered and not boundary.calls


def test_full_session_read_keeps_unpinned_legacy_transport(
    boundary: ResumeBoundary, capsys: Any,
) -> None:
    boundary.invoke("status", "owned-123", "--full")
    assert json.loads(capsys.readouterr().out) == boundary.response
    assert boundary.calls == [("GET", "/api/v1/sessions/owned-123", {})]


def test_required_writer_status_forwards_exact_optional_read(
    boundary: ResumeBoundary, monkeypatch: pytest.MonkeyPatch, capsys: Any,
) -> None:
    reads = []
    result = {**boundary.response, "worktree_writer_observation": {"original": True}}

    def get_session(session_id: str, *, worktree_writer: bool = False) -> dict[str, Any]:
        reads.append((session_id, worktree_writer))
        return result

    monkeypatch.setattr(boundary.client, "get_session", get_session)
    boundary.invoke("status", "owned-123", "--full", "--worktree-writer")
    assert json.loads(capsys.readouterr().out) == result
    assert reads == [("owned-123", True)]
    assert boundary.calls == []


def test_writer_status_keeps_unknown_argument_rejection(
    boundary: ResumeBoundary, capsys: Any,
) -> None:
    with pytest.raises(SystemExit) as raised:
        boundary.invoke("status", "owned-123", "--full", "--worktree-writr")
    assert raised.value.code == 2 and "unrecognized arguments" in capsys.readouterr().err
    assert boundary.calls == [] and boundary.discovered == []


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
            cast(socket.socket, SimpleNamespace(makefile=lambda mode: io.BytesIO(wire))),
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
