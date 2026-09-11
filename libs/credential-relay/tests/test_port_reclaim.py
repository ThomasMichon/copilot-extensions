"""A port collision never authorizes terminating another installation."""

import errno
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from credential_relay import server as module


@pytest.mark.parametrize("code,expected", [(errno.EADDRINUSE, True), (10048, True), (errno.EACCES, False)])
def test_address_in_use_classification(code, expected):
    assert module._addr_in_use(OSError(code, "synthetic bind result")) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("global_version", ["older", "newer"])
async def test_foreign_listener_is_never_classified_by_pid_or_version(tmp_path, monkeypatch, global_version):
    global_root = tmp_path / "global"
    isolated_root = tmp_path / "isolated"
    global_root.mkdir()
    isolated_root.mkdir()
    marker = global_root / "active.json"
    marker.write_text(json.dumps({"pid": 1234, "version": global_version, "port": 45123}))
    before = marker.read_bytes()
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(isolated_root))
    monkeypatch.setattr(module, "_pid_on_port", lambda port: 1234, raising=False)
    monkeypatch.setattr(
        module, "_terminate_pid", lambda pid: pytest.fail("foreign process was terminated"),
        raising=False,
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("unexpected process operation"))
    monkeypatch.setattr(module.os, "kill", lambda *a, **k: pytest.fail("unexpected process signal"))
    listener = SimpleNamespace(
        sockets=[SimpleNamespace(getsockname=lambda: ("127.0.0.1", 45124))],
        is_serving=lambda: True, close=lambda: None, wait_closed=AsyncMock(),
    )
    bind = AsyncMock(side_effect=[OSError(errno.EADDRINUSE, "occupied"), listener])
    monkeypatch.setattr(module.asyncio, "start_server", bind)
    relay = module.CredentialRelayServer(port=45123)
    await relay.start()
    assert [call.kwargs["port"] for call in bind.await_args_list] == [45123, 0]
    assert relay.port == 45124 and relay.running
    assert marker.read_bytes() == before
    await relay.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("requested", [0, 45123])
async def test_free_or_owner_released_port_binds_without_process_operations(monkeypatch, requested):
    listener = SimpleNamespace(
        sockets=[SimpleNamespace(getsockname=lambda: ("127.0.0.1", 45123))],
        is_serving=lambda: True, close=lambda: None, wait_closed=AsyncMock(),
    )
    bind = AsyncMock(return_value=listener)
    monkeypatch.setattr(module.asyncio, "start_server", bind)
    monkeypatch.setattr(module, "_pid_on_port", lambda _: pytest.fail("port ownership was guessed"), raising=False)
    monkeypatch.setattr(module, "_terminate_pid", lambda _: pytest.fail("a process was stopped"), raising=False)
    relay = module.CredentialRelayServer(port=requested)
    await relay.start()
    assert bind.await_count == 1 and bind.call_args.kwargs["port"] == requested
    assert relay.port == 45123
    await relay.stop()


@pytest.mark.asyncio
async def test_non_collision_bind_error_is_not_hidden(monkeypatch):
    bind = AsyncMock(side_effect=OSError(errno.EACCES, "denied"))
    monkeypatch.setattr(module.asyncio, "start_server", bind)
    with pytest.raises(OSError, match="denied"):
        await module.CredentialRelayServer(port=45123).start()
    assert bind.await_count == 1
