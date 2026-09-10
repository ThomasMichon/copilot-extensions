"""Generation-bound stop is independent of application forwarding and activation."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_bridge.native_manager import NativeManager, ProviderTransport
from agent_bridge.native_store import NativeError


def request(tmp_path):
    return {
        "requestId": "request-one", "codespace": "example-space", "owner": str(tmp_path),
        "cwd": "/workspaces/example", "command": "exec copilot",
        "localForward": ["4321:4321"], "reverseForward": ["9000:9001"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_gate_failure", [False, True])
async def test_stop_reconnects_only_retirement_control_when_application_ports_fail(tmp_path, monkeypatch, initial_gate_failure):
    calls = []
    broken = initial_gate_failure
    activated = False

    class Transport:
        def __init__(self, prefix, row, on_reserved):
            self.on_reserved = on_reserved

        async def start(self, *, resume, retirement_only=False):
            self.retirement_only = retirement_only
            self.on_reserved()
            calls.append(("connect", resume, retirement_only))

        async def request(self, method, params=None):
            nonlocal activated
            calls.append(method)
            if method == "stop":
                return {"state": "stopped", "retired": True, "exitCode": 7, "recovery": {"ok": True}}
            if broken:
                raise NativeError("provider_operation_failed", "application listener unavailable")
            if method == "activate":
                activated = True
            return {
                "state": "ready", "represented": True, "sessionId": "real-session",
                "host": {"nonce": "host-nonce", "child_pid": 1234, "port": 10000},
                "localPort": 10001, "activated": activated, "retired": False,
            }

        async def close(self):
            calls.append("close")

    first = NativeManager(tmp_path / "controller", lambda: ["provider"], transport_factory=Transport)
    monkeypatch.setattr(first, "_prove_endpoint", AsyncMock())
    initial = await first.start(request(tmp_path))
    await asyncio.gather(*list(first.tasks.values()))
    assert calls.count("launch") == 1
    assert activated is not initial_gate_failure
    await first.shutdown()
    broken = True
    calls.clear()
    second = NativeManager(tmp_path / "controller", lambda: ["provider"], transport_factory=Transport)
    monkeypatch.setattr(second, "_retirement_receipt", AsyncMock(return_value=None))
    monkeypatch.setattr(second, "_prove_endpoint", AsyncMock(side_effect=AssertionError("stop must not prove application forwards")))
    with pytest.raises(NativeError):
        await second.stop(initial["executionId"], "stale-generation")
    assert calls == []
    stopped = await second.stop(initial["executionId"], initial["generation"])
    assert stopped["state"] == "stopped" and stopped["exitCode"] == 7
    assert calls == [("connect", True, True), "stop", "close"]
    assert await second.stop(initial["executionId"], initial["generation"]) == stopped
    second.assert_acp_allowed("example-space")
    assert activated is not initial_gate_failure
    await second.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["control-unavailable", "no-proof"])
async def test_unconfirmed_retirement_stays_stopping_and_never_activates(tmp_path, monkeypatch, failure):
    calls = []

    class Transport:
        def __init__(self, prefix, row, on_reserved):
            pass

        async def start(self, *, resume, retirement_only=False):
            calls.append(("connect", resume, retirement_only))
            if failure == "control-unavailable":
                raise OSError("control unavailable")

        async def request(self, method, params=None):
            calls.append(method)
            assert method == "stop"
            return {"state": "unrepresented", "retired": False, "host": {"port": 10000}}

        async def close(self):
            calls.append("close")

    manager = NativeManager(tmp_path / "controller", lambda: ["provider"], transport_factory=Transport)
    store = manager.store(create=True)
    row, _ = store.reserve(
        "execution", "generation", "request", "example-space", str(tmp_path),
        "signature", {"spec": request(tmp_path), "launchRequested": True},
    )
    monkeypatch.setattr(manager, "_retirement_receipt", AsyncMock(return_value=None))
    with pytest.raises(NativeError, match="ownership retained|not verified"):
        await manager.stop(row["id"], row["generation"])
    before = list(calls)
    current = await manager.status(row["id"], row["generation"])
    await manager._prepare(row["id"], row["generation"])
    assert calls == before
    assert current["state"] == "stopping" and not current["ready"]
    with pytest.raises(NativeError, match="blocks ACP"):
        manager.assert_acp_allowed("example-space")
    await manager.shutdown()


@pytest.mark.asyncio
async def test_provider_retirement_argv_retains_identity_and_relay_but_not_ports(tmp_path, monkeypatch):
    stream = asyncio.StreamReader()
    stream.feed_data(b'{"event":"reserved"}\n{"event":"ready","capability":"codespace-native-transport-v1"}\n')
    stream.feed_eof()
    process = SimpleNamespace(
        stdout=stream, stderr=SimpleNamespace(read=AsyncMock(return_value=b"")),
        returncode=0,
    )
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    reserved = []
    row = {
        "id": "execution", "generation": "generation", "owner": str(tmp_path),
        "codespace": "example-space", "data": {"spec": request(tmp_path)},
    }
    transport = ProviderTransport(["provider"], row, lambda: reserved.append(True))
    await transport.start(resume=True, retirement_only=True)
    assert spawn.call_args.args == (
        "provider", "native-transport", "example-space",
        "--owner", str(tmp_path), "--execution-id", "execution",
        "--generation", "generation", "--no-plugin-staging", "--require-relay",
        "--resume-infrastructure", "--retirement-only",
    )
    assert reserved == [True]
    await transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("proof", [None, {"retired": True}, {"retired": True, "noLaunch": True}])
async def test_no_launch_stop_requires_durable_retirement_proof(tmp_path, monkeypatch, proof):
    manager = NativeManager(tmp_path / "controller", lambda: ["provider"])
    store = manager.store(create=True)
    store.reserve(
        "execution", "generation", "request", "example-space", str(tmp_path),
        "signature", {"spec": request(tmp_path)},
    )
    process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b'{"released":false}', b"")))
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(manager, "_retirement_receipt", AsyncMock(return_value=proof))
    if proof and proof.get("noLaunch"):
        assert (await manager.stop("execution", "generation"))["state"] == "stopped"
    else:
        with pytest.raises(NativeError, match="receipt is unavailable"):
            await manager.stop("execution", "generation")
        assert store.get("execution", "generation")["state"] == "stopping"
    assert spawn.call_args.args == (
        "provider", "native-abort", "example-space", "--owner", str(tmp_path),
        "--execution-id", "execution", "--generation", "generation",
    )


def test_late_status_does_not_undo_retirement_intent(tmp_path):
    manager = NativeManager(tmp_path / "controller", lambda: ["provider"])
    store = manager.store(create=True)
    store.reserve("execution", "generation", "request", "example-space", "owner", "signature", {})
    store.update("execution", "generation", state="stopping", represented=False)
    manager._save_result(store, "execution", "generation", {
        "state": "ready", "represented": True, "sessionId": "real-session",
    })
    row = store.get("execution", "generation")
    assert row["state"] == "stopping"
    assert row["data"]["represented"] is False
    assert row["data"]["phase"] == "stopping"
