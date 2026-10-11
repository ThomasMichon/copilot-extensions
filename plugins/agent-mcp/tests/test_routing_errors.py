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
