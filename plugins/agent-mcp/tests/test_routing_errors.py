"""Expected routing failures use protocol/CLI errors, never traceback fallthrough."""

import asyncio
import json

import pytest

from agent_mcp.client import OneShotSession, UpstreamError
from agent_mcp.config import parse_config
from agent_mcp.routing import CompatibilityError, RoutedCore
from agent_mcp.routing_errors import RoutingError

from .test_routing import definition, upstreams as upstreams


@pytest.fixture(autouse=True)
def direct_cli_boundary(monkeypatch):
    monkeypatch.setenv("AGENT_MCP_NO_SERVE", "1")


@pytest.mark.parametrize("operation", ["call", "materialize"])
def test_real_cli_catalog_collision_is_error_not_traceback(upstreams, tmp_path, capsys, operation):
    from agent_mcp.__main__ import main

    upstreams["tools"]["alpha"][0]["inputSchema"]["properties"]["organization"] = {}
    cfg = tmp_path / "routed.json"
    cfg.write_text(json.dumps(definition()), encoding="utf-8")
    argv = ["call", str(cfg), "read", '{"organization":"alpha"}'] if operation == "call" else [
        "materialize", str(cfg), "--dest", str(tmp_path / "fleet")]
    assert main(argv) == 1
    output = capsys.readouterr()
    assert "compatibility error" in output.err
    assert "Traceback" not in output.err


@pytest.mark.parametrize("operation", ["call", "materialize"])
def test_real_cli_unavailable_identity_is_error_not_traceback(tmp_path, capsys, monkeypatch, operation):
    from agent_mcp.__main__ import main
    import agent_mcp.auth.identity as identity

    async def unavailable(*args, **kwargs):
        raise RoutingError("routing auth identity unavailable: fixture")

    monkeypatch.setattr(identity, "resolve_entra_identity", unavailable)
    data = definition()
    for target in data["routing"]["targets"].values():
        target["auth"] = {"kind": "entra", "resource": "resource"}
    cfg = tmp_path / "routed.json"
    cfg.write_text(json.dumps(data), encoding="utf-8")
    argv = ["call", str(cfg), "read", '{"organization":"alpha"}'] if operation == "call" else [
        "materialize", str(cfg), "--dest", str(tmp_path / "fleet")]
    assert main(argv) == 1
    output = capsys.readouterr()
    assert "auth identity unavailable: fixture" in output.err
    assert "Traceback" not in output.err


@pytest.mark.parametrize("failure", [
    RoutingError("identity unavailable", code=-32001, data={"retry": False}),
    UpstreamError("upstream rejected", code=-32003, data={"reason": "denied"}),
])
async def test_routed_handle_preserves_structured_error(failure, monkeypatch):
    core = RoutedCore(parse_config(definition()))

    async def fail(_msg):
        raise failure

    monkeypatch.setattr(core, "_execute", fail)
    for policy in core._policies.values():
        policy.core = fail
    response = await core.handle({"id": 41, "method": "tools/call", "params": {
        "name": "read", "arguments": {"organization": "alpha"}}})
    assert response == {"jsonrpc": "2.0", "id": 41, "error": {
        "code": failure.code, "message": str(failure), "data": failure.data}}
    await core.aclose()


@pytest.mark.parametrize("failure", [
    RoutingError("selected identity unavailable", code=-32001, data={"retry": False}),
    RoutingError("selected identity unavailable"),
])
async def test_cli_auth_preserves_structured_error_without_spawning(
    failure, tmp_path, monkeypatch,
):
    import sys
    import yaml
    from agent_mcp.transports.cli import CliTransport

    sidecar = tmp_path / "read.md"
    sidecar.write_text("---\n" + yaml.safe_dump({"mcp": {
        "name": "read", "inputSchema": {"type": "object", "properties": {}},
        "invoke": {"command": sys.executable, "args": ["-c", "print('unexpected')"]},
    }}) + "---\n", encoding="utf-8")
    cfg = parse_config({"server": {"type": "cli", "tools_from": [str(sidecar)]}})
    cfg.routed_target = True

    class Injector:
        async def child_env(self):
            raise failure

    async def unexpected_spawn(*args, **kwargs):
        pytest.fail("authentication failure must prevent spawning")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", unexpected_spawn)
    transport = CliTransport(cfg, Injector())
    with pytest.raises(RoutingError) as error:
        await transport._child_env()
    assert error.value is failure

    emitted = []

    async def capture(response):
        emitted.append(response)

    monkeypatch.setattr(transport, "_emit_message", capture)
    await transport.send({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                          "params": {"name": "read", "arguments": {}}})
    expected = {"code": failure.code, "message": str(failure)}
    if failure.data is not None:
        expected["data"] = failure.data
    assert emitted == [{"jsonrpc": "2.0", "id": 9, "error": expected}]


@pytest.mark.parametrize("checked", [False, True])
async def test_catalog_normalizes_typed_error_and_preserves_upstream_details(checked, monkeypatch):
    async with OneShotSession(parse_config(definition())) as session:
        async def fail():
            raise CompatibilityError("catalog mismatch", code=-32002, data={"contract": "tools"})

        monkeypatch.setattr(session._router, "catalog", fail)
        method = session.list_tools_checked if checked else session.list_tools
        with pytest.raises(UpstreamError) as error:
            await method()
        assert error.value.code == -32002
        assert error.value.data == {"contract": "tools"}
        original = UpstreamError("existing", code=-32004, data={"original": True})

        async def fail_upstream():
            raise original

        monkeypatch.setattr(session._router, "catalog", fail_upstream)
        with pytest.raises(UpstreamError) as error:
            await method()
        assert error.value is original


@pytest.mark.parametrize("failure", [asyncio.CancelledError(), RuntimeError("programming bug")])
async def test_boundary_does_not_swallow_cancellation_or_programming_errors(monkeypatch, failure):
    async with OneShotSession(parse_config(definition())) as session:
        async def fail():
            raise failure

        monkeypatch.setattr(session._router, "catalog", fail)
        with pytest.raises(type(failure)):
            await session.list_tools()
        with pytest.raises(type(failure)):
            await session._router.handle({"id": 1, "method": "tools/list"})


@pytest.mark.parametrize("method", [
    "tools/list", "tools/call", "initialize", "server/discover", "ping",
    "notifications/initialized", "resources/list",
])
@pytest.mark.parametrize("params", [[], ["value"], "", "value", 1, None])
async def test_malformed_params_are_invalid_request_parameters(method, params):
    core = RoutedCore(parse_config(definition()))
    request = {"jsonrpc": "2.0", "id": 7, "method": method, "params": params}
    assert await core.handle(request) == {"jsonrpc": "2.0", "id": 7, "error": {
        "code": -32602, "message": "routing params must be an object"}}
    request.pop("id")
    assert await core.handle(request) is None
    assert not core._sessions
    await core.aclose()


@pytest.mark.parametrize("method", [
    "tools/list", "initialize", "server/discover", "ping", "notifications/initialized",
])
async def test_valid_local_notifications_never_produce_responses_or_start_targets(method):
    core = RoutedCore(parse_config(definition()))
    assert await core.handle({"method": method, "params": {}}) is None
    assert not core._sessions
    await core.aclose()


@pytest.mark.parametrize("policy", [{"allow": ["action"]}, {"deny": ["read"]}])
async def test_name_authorization_denial_is_method_not_found_without_start(policy):
    data = definition()
    data["routing"]["targets"]["beta"]["tools"] = policy
    core = RoutedCore(parse_config(data))
    response = await core.handle({"id": 1, "method": "tools/call", "params": {
        "name": "read", "arguments": {"organization": "beta"}}})
    assert response["error"]["code"] == -32601
    assert "authorization" in response["error"]["message"]
    assert not core._sessions
    await core.aclose()


@pytest.mark.parametrize("failure_phase", ["policy", "session"])
@pytest.mark.parametrize("failure", [RuntimeError("close failed"), asyncio.CancelledError()])
async def test_cleanup_drains_every_target_and_preserves_failure(failure_phase, failure):
    core = RoutedCore(parse_config(definition()))
    closed = []

    class Policy:
        def __init__(self, name):
            self.name = name

        async def aclose(self):
            closed.append(("policy", self.name))
            if failure_phase == "policy" and self.name == "alpha":
                raise failure

    class Session:
        def __init__(self, name):
            self.name = name

        async def __aexit__(self, *args):
            closed.append(("session", self.name))
            if failure_phase == "session" and self.name == "alpha":
                raise failure

    core._policies = {key: Policy(key) for key in ("alpha", "beta")}
    core._sessions = {key: Session(key) for key in ("alpha", "beta")}
    core._session_configs = {"alpha": object(), "beta": object()}
    with pytest.raises(type(failure)) as error:
        await core.aclose()
    if not isinstance(failure, asyncio.CancelledError):
        assert error.value is failure
    assert set(closed) == {
        ("policy", "alpha"), ("policy", "beta"), ("session", "alpha"), ("session", "beta")}
    assert not core._sessions and not core._session_configs


async def test_caller_cancellation_still_drains_sessions_then_propagates():
    core = RoutedCore(parse_config(definition()))
    entered, release = asyncio.Event(), asyncio.Event()
    closed = []

    class Policy:
        async def aclose(self):
            entered.set()
            await release.wait()

    class Session:
        async def __aexit__(self, *args):
            closed.append("session")

    core._policies = {"alpha": Policy()}
    core._sessions = {"alpha": Session()}
    core._session_configs = {"alpha": object()}
    task = asyncio.create_task(core.aclose())
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed == ["session"]
    assert not core._sessions and not core._session_configs
