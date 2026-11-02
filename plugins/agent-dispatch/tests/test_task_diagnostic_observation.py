"""Exercise production client factories without local service or tunnel activation."""

import httpx
import pytest

from agent_dispatch import __main__ as core
from agent_dispatch.client import DispatchClient, DispatchUpgradeRequired
from agent_dispatch.mcp_server import DispatchTools


@pytest.mark.parametrize("surface", [
    "cli-diagnostics", "stdio-diagnostics", "cli-detach", "stdio-detach",
    "cli-reset", "stdio-reset", "cli-reconcile", "stdio-reconcile",
])
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
    observational = surface.endswith("diagnostics")

    def forbidden(*args, **kwargs):
        pytest.fail("recovery tooling activated coordinator or SSH failover")

    def target(args, *, read_only=False):
        assert read_only == observational
        return "http://local.invalid", None

    def transport(request):
        calls.append((request.method, request.url.path))
        if not available:
            raise httpx.ConnectError("synthetic absent coordinator")
        if request.url.path == "/health":
            return httpx.Response(200, json={
                "task_recovery": {
                    "protocol": "task_recovery/v1",
                    "operations": ["detach_agent", "reset_queued", "reconcile_wake"],
                },
            })
        return httpx.Response(200, json={"fixture": True})

    def client(url, **kwargs):
        assert kwargs["control_token"] == (None if observational else "control-fixture")
        return DispatchClient(url, transport=httpx.MockTransport(transport), **kwargs)

    monkeypatch.setattr(core, "_ensure_local_coordinator", forbidden)
    monkeypatch.setattr(core, "_should_ssh_failover", forbidden)
    monkeypatch.setattr(
        core, "client_control_token", forbidden if observational else lambda: "control-fixture",
    )
    monkeypatch.setattr(core, "_resolve_client_target", target)
    monkeypatch.setattr(core, "DispatchClient", client)
    fields = {
        "expected_revision": "revision-1", "request_id": "request-1",
        "actor": "operator", "reason": "explicit recovery",
    }
    kind = surface.split("-", 1)[1]
    if surface.startswith("cli"):
        flags = [item for name, value in fields.items() for item in (
            "--" + name.replace("_", "-"), value,
        )]
        command = {
            "diagnostics": ["task-diagnostics", "task-a"],
            "detach": ["detach-agent", "task-a", *flags],
            "reset": ["reset", "task-a", "--to", "queued", *flags],
            "reconcile": [
                "reconcile-wake", "task-a", "--outbox", "wake_outbox",
                "--wake-id", "wake-1", "--expected-revision", fields["expected_revision"],
                "--actor", fields["actor"], "--reason", fields["reason"],
            ],
        }[kind]
        assert core.main(command) == (0 if available else 1)
    else:
        tools = DispatchTools()
        def invoke():
            if kind == "diagnostics":
                return tools.task_diagnostics("task-a")
            if kind == "detach":
                return tools.detach_agent("task-a", **fields)
            if kind == "reset":
                return tools.reset("task-a", to="queued", **fields)
            return tools.reconcile_wake(
                "task-a", outbox="wake_outbox", wake_id="wake-1",
                **{name: value for name, value in fields.items() if name != "request_id"},
            )
        if available:
            assert invoke() == {"fixture": True}
        else:
            with pytest.raises(DispatchUpgradeRequired, match="cannot confirm"):
                invoke()
    if available:
        route = {
            "diagnostics": "/tasks/task-a/diagnostics",
            "detach": "/tasks/task-a/recovery/detach_agent",
            "reset": "/tasks/task-a/recovery/reset_queued",
            "reconcile": "/tasks/task-a/wakes/reconcile",
        }[kind]
        assert calls == [("GET", "/health"), ("GET" if observational else "POST", route)]
    else:
        assert calls and all(call == ("GET", "/health") for call in calls)
