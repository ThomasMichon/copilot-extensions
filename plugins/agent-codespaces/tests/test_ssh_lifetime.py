"""Long-lived ownership, serving probes, and connected settlement."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_codespaces import __main__ as cli
from agent_codespaces import connection_owner as owner
from agent_codespaces import coordination, lease, relay_readiness, ssh_lifetime


def test_refresh_preserves_only_own_tenant_beyond_owner_ttl(tmp_path, monkeypatch):
    for module, paths in (
        (owner, {"OWNER_FILE": "owner.json", "_LOCK_FILE": "owner.lock"}),
        (lease, {"LEASE_FILE": "leases.json", "_LOCK_FILE": "leases.lock"}),
    ):
        for key, value in paths.items():
            monkeypatch.setattr(module, key, tmp_path / value)
        monkeypatch.setattr(module, "RUNTIME_DIR", tmp_path)
        monkeypatch.setattr(module, "ensure_runtime_dir", lambda: None)
    clock = [1000.0]
    fake_time = SimpleNamespace(time=lambda: clock[0], monotonic=time.monotonic, sleep=time.sleep)
    monkeypatch.setattr(owner, "time", fake_time)
    monkeypatch.setattr(lease, "time", fake_time)
    lease.claim("example-space", "example-owner")
    owner.hold("example-space", "ssh:owned")
    owner.hold("example-space", "ssh:forgotten")
    for _ in range(16):
        clock[0] += 300
        ssh_lifetime.refresh_ownership("example-space", "example-owner", "ssh:owned")
    held = owner.get_hold("example-space")
    assert held is not None and set(held.tenants) == {"ssh:owned"}
    assert lease.get_lease("example-space").heartbeat_at == clock[0]
    owner.release("example-space", "ssh:owned")
    assert owner.get_hold("example-space") is None


def test_claim_refresh_failure_does_not_skip_owner_refresh(monkeypatch):
    def fail(*a, **k):
        raise OSError("unavailable")

    called = []
    monkeypatch.setattr(lease, "heartbeat", fail)
    monkeypatch.setattr(
        owner, "heartbeat",
        lambda name, tenant: called.append((name, tenant)) or SimpleNamespace(tenants={tenant: 1}),
    )
    ssh_lifetime.refresh_ownership("example-space", "example-owner", "ssh:owned")
    assert called == [("example-space", "ssh:owned")]


def test_interactive_keeps_probe_connection_and_renews_until_settlement(monkeypatch, ssh_runtime):
    events, manager = ssh_runtime
    connected = [False]
    renewals = []
    monkeypatch.setattr(ssh_lifetime, "HEARTBEAT_INTERVAL", .005)
    monkeypatch.setattr(coordination, "owner_ref", lambda **k: "example-host/example/worktree")
    monkeypatch.setattr(coordination, "journal_obligation", lambda *a: False)
    monkeypatch.setattr(owner, "should_defer_to_owner", lambda *a, **k: True)
    monkeypatch.setattr(owner, "hold", lambda *a: events.append("hold"))
    monkeypatch.setattr(owner, "release", lambda *a: events.append("release"))
    monkeypatch.setattr(owner, "await_owner_relay", AsyncMock(return_value=True))
    monkeypatch.setattr(owner, "owner_serves_relay", lambda _: True)
    monkeypatch.setattr(relay_readiness, "relay_ping", lambda _: True)
    monkeypatch.setattr(cli, "_provision_relay_helpers", AsyncMock(return_value=True))
    monkeypatch.setattr(lease, "heartbeat", lambda name, **k: renewals.append(k["owner"]) or True)
    monkeypatch.setattr(
        owner, "heartbeat", lambda name, tenant: SimpleNamespace(tenants={tenant: 1}),
    )

    async def connect(*a):
        connected[0] = True
        return SimpleNamespace(config=SimpleNamespace())

    async def disconnect(*a):
        events.append("disconnect")
        connected[0] = False

    async def exec_command(*a, **k):
        assert connected[0]
        return SimpleNamespace(exit_code=0)

    async def terminal(*a, **k):
        assert connected[0]
        for _ in range(100):
            if len(renewals) >= 2:
                return 23
            await asyncio.sleep(.01)
        pytest.fail("ownership heartbeat did not run")

    async def settle(*a):
        assert connected[0]
        assert renewals and set(renewals) == {"example-worktree"}
        events.append("settle")
        count = len(renewals)
        await asyncio.sleep(.02)
        assert len(renewals) == count  # joined before settlement/tenant release

    manager.ensure_connected.side_effect = connect
    manager.disconnect.side_effect = disconnect
    manager.exec_command = AsyncMock(side_effect=exec_command)
    monkeypatch.setattr(cli, "_interactive_ssh", terminal)
    monkeypatch.setattr(cli, "_settle_codespace_on_disconnect", settle)
    assert cli.main([
        "ssh", "example-space", "--interactive-command", "true", "--no-provision", "--require-relay",
    ]) == 23
    assert events[-4:] == ["settle", "release", "disconnect", "unlock"]
    assert not connected[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("result", ["good", "bad", "timeout", "ssh-error", "exception"])
async def test_monitor_distinguishes_bad_relay_from_unknown_transport(result):
    manager = SimpleNamespace(exec_command=AsyncMock())
    if result == "exception":
        manager.exec_command.side_effect = OSError("unavailable")
    else:
        manager.exec_command.return_value = SimpleNamespace(
            exit_code={"bad": 1, "ssh-error": 255}.get(result, 0),
            timed_out=result == "timeout",
        )
    assert await relay_readiness.remote_relay_ready(
        manager, "example-space", 9857, fail_open=True,
    ) is (result != "bad")
    assert await relay_readiness.remote_relay_ready(
        manager, "example-space", 9857,
    ) is (result == "good")


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_fails", [False, True])
async def test_owner_probe_and_cleanup_use_an_isolated_managed_channel(stop_fails):
    built = {}
    manager = SimpleNamespace(
        ensure_connected=AsyncMock(),
        exec_command=AsyncMock(return_value=SimpleNamespace(exit_code=0)),
        disconnect=AsyncMock(),
    )

    class Source:
        def __init__(self, name, **kwargs):
            self.name = name

        def get_ssh_config(self):
            return "example-ssh-config"

    class Relay:
        def __init__(self, config, port, **kwargs):
            built.update(kwargs)
            self.is_alive = True
            self.start = AsyncMock()
            self.stop = AsyncMock(side_effect=OSError("stop failed") if stop_fails else None)

    channel = owner.make_supervised_relay_factory(
        object(), relay_cls=Relay, config_source_cls=Source,
        port_resolver=lambda _: 9857, manager_factory=lambda: manager,
    )("example-space")
    await channel.start()
    assert channel.is_alive
    assert await built["serving_probe"]() is True
    manager.exec_command.return_value = SimpleNamespace(exit_code=1)
    assert await built["serving_probe"]() is False
    manager.ensure_connected.side_effect = OSError("transport unavailable")
    assert await built["serving_probe"]() is True
    assert manager.ensure_connected.call_args.args[0] == "relay-owner-example-space"
    if stop_fails:
        with pytest.raises(OSError, match="stop failed"):
            await channel.stop()
    else:
        await channel.stop()
    manager.disconnect.assert_awaited_once_with("relay-owner-example-space")


@pytest.mark.asyncio
async def test_direct_relay_receives_serving_probe(monkeypatch):
    import ssh_manager

    built = {}
    relay = SimpleNamespace(start=AsyncMock())

    def factory(*a, **kwargs):
        built.update(kwargs)
        return relay

    probe = AsyncMock(return_value=True)
    monkeypatch.setattr(ssh_manager, "SupervisedRelayForward", factory)
    assert await cli._start_supervised_relay(
        "example-space", object(), 9857, context="test", serving_probe=probe,
    ) is relay
    assert built["serving_probe"] is probe
    relay.start.assert_awaited_once()
