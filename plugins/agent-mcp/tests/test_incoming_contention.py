"""Installed-branch consumers with a real private OS lock and stdio upstream.

Physical-slot discovery and peer readiness are controlled, not installation proof.
"""

import asyncio
import io
import json
import sys

import pytest
from zdd import CutoverLock, CutoverLockedError
from zdd.routing import ActivePublicationRefused

from agent_mcp import config, cutover, forward, incoming, ipc
from .test_forward import _write_bridge_config


@pytest.fixture
def installed_client(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "current-version").write_text("incoming", encoding="utf-8")
    layout = ("incoming", root / "python", root)
    home = tmp_path / "socket"
    home.mkdir()
    handle = home / "serve.sock"

    def selected_slot(*, require_selected=True):
        if require_selected:
            cutover._require_selected(layout)
        return layout

    monkeypatch.setattr(incoming, "launch_generation", lambda: layout[0])
    monkeypatch.setattr(cutover, "_installed_slot", selected_slot)
    monkeypatch.setattr(incoming, "_ping", lambda _handle: {
        "version": "outgoing", "runtime_root": str(root),
        "fresh_admission": incoming.CONTRACT, "ready": True, "draining": False,
    })
    for name in (
        "AGENT_MCP_NO_MULTIPLEX", "AGENT_MCP_NO_SERVE",
        "AGENT_MCP_NO_ENSURE_SERVE", "AGENT_MCP_NO_CUTOVER",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_MCP_HOME", str(home))
    monkeypatch.setenv("AGENT_MCP_PARENT_WATCHDOG", "0")
    monkeypatch.setattr(cutover, "pick_free_port", lambda: pytest.fail("contender must not spawn"))
    return layout, home, handle, _write_bridge_config(tmp_path)


@pytest.mark.parametrize("advertised", [False, True], ids=["cold", "stale-advertised"])
def test_contended_startup_keeps_real_direct_initialize(
    installed_client, monkeypatch, capsys, advertised,
):
    _, home, handle, bridge = installed_client
    monkeypatch.setattr(forward.sockio, "serve_socket_if_available", lambda _path: (
        str(handle) if advertised else None
    ))
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "private-fixture", "version": "1"},
        }},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "echo", "arguments": {"contended": True}}},
    ]
    monkeypatch.setattr(sys, "stdin", io.StringIO(
        "".join(json.dumps(request) + "\n" for request in requests),
    ))
    with CutoverLock(home):
        original = (home / "zdd-cutover.lock").read_bytes()
        assert forward.run(str(bridge), socket_path=str(handle)) == 0
        assert (home / "zdd-cutover.lock").read_bytes() == original
        with pytest.raises(CutoverLockedError):
            with CutoverLock(home):
                pytest.fail("the original owner must still hold its lease")
    replies = {
        reply["id"]: reply for line in capsys.readouterr().out.splitlines()
        if (reply := json.loads(line)).get("id") in {1, 2}
    }
    assert "result" in replies[1]
    assert json.loads(replies[2]["result"]["content"][0]["text"]) == {"contended": True}


def test_selection_change_during_contention_refuses_direct_work(
    installed_client, monkeypatch, capsys,
):
    layout, home, handle, bridge = installed_client
    startup = cutover._run_slot_startup

    def racing_startup(*args, **kwargs):
        try:
            return startup(*args, **kwargs)
        finally:
            (layout[2] / "current-version").write_text("replacement", encoding="utf-8")

    monkeypatch.setattr(cutover, "_run_slot_startup", racing_startup)
    monkeypatch.setattr(forward.sockio, "serve_socket_if_available", lambda _path: None)
    monkeypatch.setattr(config, "load_config", lambda _bridge: pytest.fail("no direct work admitted"))
    with CutoverLock(home):
        assert forward.run(str(bridge), socket_path=str(handle)) == 1
    output = capsys.readouterr()
    assert not output.out
    assert "direct execution refused" in output.err


def test_ownership_refusal_is_not_normalized_as_lock_contention(installed_client, monkeypatch):
    _, _, handle, bridge = installed_client

    def refused(*args, **kwargs):
        raise ActivePublicationRefused("foreign owner")

    monkeypatch.setattr(cutover, "_run_slot_startup", refused)
    monkeypatch.setattr(forward.sockio, "serve_socket_if_available", lambda _path: None)
    with pytest.raises(ActivePublicationRefused, match="foreign owner"):
        forward.run(str(bridge), socket_path=str(handle))


@pytest.mark.parametrize("operation", ["call", "list"])
def test_ipc_contention_is_unavailability_before_application_bytes(
    installed_client, monkeypatch, operation,
):
    _, home, handle, bridge = installed_client

    async def connected(_handle):
        pytest.fail("no application connection may follow failed startup admission")

    monkeypatch.setattr(ipc, "_connect", connected)
    with CutoverLock(home), pytest.raises(OSError, match="startup is busy") as unavailable:
        asyncio.run(ipc.request_via_socket(handle, {"op": operation, "bridge": str(bridge)}))
    assert isinstance(unavailable.value.__cause__, CutoverLockedError)
