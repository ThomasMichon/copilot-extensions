"""Exercise production client factories without local service or tunnel activation."""

import httpx
import pytest

from agent_dispatch import __main__ as core
from agent_dispatch.client import DispatchClient, DispatchUpgradeRequired
from agent_dispatch.mcp_server import DispatchTools


@pytest.mark.parametrize("surface", ["cli", "stdio"])
@pytest.mark.parametrize("available", [False, True])
def test_default_diagnostic_factories_never_activate_a_missing_coordinator(
    monkeypatch, surface, available,
):
    for name in (
        "AGENT_DISPATCH_TASK_TOKEN", "AGENT_DISPATCH_TASK_ID",
        "AGENT_DISPATCH_TASK_URL", "AGENT_DISPATCH_TASK_WORKER_ID",
        "AGENT_DISPATCH_TASK_TOKEN_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "local")
    calls = []

    def forbidden(*args, **kwargs):
        pytest.fail("diagnostic activated coordinator, SSH failover or control discovery")

    def target(args, *, read_only=False):
        assert read_only
        return "http://local.invalid", None

    def transport(request):
        calls.append((request.method, request.url.path))
        if not available:
            raise httpx.ConnectError("synthetic absent coordinator")
        if request.url.path == "/health":
            return httpx.Response(200, json={
                "task_recovery": {"protocol": "task_recovery/v1", "operations": []},
            })
        return httpx.Response(200, json={"fixture": True})

    def client(url, **kwargs):
        assert kwargs["control_token"] is None
        return DispatchClient(url, transport=httpx.MockTransport(transport), **kwargs)

    monkeypatch.setattr(core, "_ensure_local_coordinator", forbidden)
    monkeypatch.setattr(core, "_should_ssh_failover", forbidden)
    monkeypatch.setattr(core, "client_control_token", forbidden)
    monkeypatch.setattr(core, "_resolve_client_target", target)
    monkeypatch.setattr(core, "DispatchClient", client)
    if surface == "cli":
        assert core.main(["task-diagnostics", "task-a"]) == (0 if available else 1)
    elif available:
        assert DispatchTools().task_diagnostics("task-a") == {"fixture": True}
    else:
        with pytest.raises(DispatchUpgradeRequired, match="cannot confirm"):
            DispatchTools().task_diagnostics("task-a")
    if available:
        assert calls == [("GET", "/health"), ("GET", "/tasks/task-a/diagnostics")]
    else:
        assert calls and all(call == ("GET", "/health") for call in calls)
