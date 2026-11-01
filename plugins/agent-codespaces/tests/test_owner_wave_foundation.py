"""Explicit foundation contracts; production continues using its legacy probes."""
from __future__ import annotations

import asyncio
import json
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent_codespaces import connection_owner as owner
from agent_codespaces import lifecycle, owner_inventory as inv
from agent_codespaces import owner_mux_probe as mux
from agent_codespaces import session_forwards as sf


SCOPE = inv.InventoryScope("source", "config", ("a", "b", "c"), ("x", "y", "z"))


def inventory(wave=1, *, rows=None, observed=0.0):
    rows = rows if rows is not None else (inv.InventoryRow("cs", "Available", "a"),)
    return inv.OwnerInventory(wave, SCOPE, (
        inv.AccountListing("a", rows, observed, True),
        inv.AccountListing("b", (), observed, True),
        inv.AccountListing("c", (), observed, True),
    ))


def lookup(value):
    return value.lookup("cs", wave=1, scope=SCOPE, now=1, max_age=30)


@pytest.mark.parametrize("mutation", [
    lambda x: replace(x, wave=2),
    lambda x: replace(x, scope=replace(SCOPE, source="other")),
    lambda x: replace(x, scope=replace(SCOPE, configuration="changed")),
    lambda x: replace(x, scope=replace(SCOPE, credentials=("new",))),
    lambda x: replace(x, listings=(replace(x.listings[0], complete=False),)),
    lambda x: replace(x, listings=(replace(x.listings[0], error="failed"),)),
    lambda x: replace(x, listings=(replace(x.listings[0], observed_at=-40),)),
    lambda x: replace(x, listings=x.listings * 2),
    lambda x: replace(x, listings=()),
])
def test_inventory_refuses_unqualified_authority(mutation):
    assert lookup(mutation(inventory())) is None


def test_inventory_immutable_and_absence_unknown():
    assert lookup(inventory()).account == "a"
    assert lookup(inventory(rows=())) is None
    with pytest.raises(AttributeError):
        inventory().wave = 2


@pytest.mark.parametrize("stdout,code", [
    ("", 0), ("ACMUX1\t0\tgone\n", 0),
    ("ACMUX1\t0\tgone\nACMUX1\tDONE\t1\n", 127),
    ("ACMUX1\t0\tgone\nACMUX1\t0\tgone\nACMUX1\tDONE\t1\n", 0),
    ("ACMUX2\t0\tgone\nACMUX1\tDONE\t1\n", 0),
    ("ACMUX1\t0\twrong\nACMUX1\tDONE\t1\n", 0),
])
def test_batch_requires_complete_versioned_output(stdout, code):
    assert mux.parse_batch(["quoted'; $target"], stdout, code) == {"quoted'; $target": None}


def test_batch_unique_exact_targets_and_safe_quoting():
    import shlex

    target = "x'; echo BAD; #"
    command = mux.batch_command([target, target, "other"])
    commands = [line for line in command.splitlines() if line.startswith("tmux has-session")]
    assert len(commands) == 2
    assert shlex.split(commands[0])[3] == "=" + target
    assert command.startswith("command -v tmux")
    assert mux.parse_batch(
        [target, target, "other"],
        "ACMUX1\t0\tpresent\nACMUX1\t1\tgone\nACMUX1\tDONE\t2\n", 0,
    ) == {target: True, "other": False}


@pytest.mark.parametrize("payload,complete,error", [
    ("[]", True, None), ("broken", False, "RuntimeError"),
    ("{}", False, "RuntimeError"), ('[{"name":"cs"}]', False, "RuntimeError"),
    (json.dumps([{"name": f"cs-{i}", "state": "Available"} for i in range(50)]), False, None),
])
def test_explicit_qualified_listing_reports_errors_and_limit(monkeypatch, payload, complete, error):
    monkeypatch.setattr(inv, "credential", lambda account: "fake-credential")
    monkeypatch.setattr(lifecycle, "validate_context", lambda: None)
    monkeypatch.setattr(lifecycle.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=payload, stderr="",
    ))
    result = lifecycle.qualified_account_listing("a", clock=lambda: 1)
    assert (result.complete, result.error) == (complete, error)


@pytest.mark.parametrize("account", [None, "a"])
def test_direct_qualified_listing_refuses_context_before_credentials_or_listing(monkeypatch, account):
    from agent_codespaces.worktrees import ContextRefused

    def refused():
        raise ContextRefused("synthetic refused context")

    def forbidden(*args, **kwargs):
        pytest.fail("refused context must not resolve credentials or list")

    monkeypatch.setattr(lifecycle, "validate_context", refused)
    monkeypatch.setattr(inv, "credential", forbidden)
    monkeypatch.setattr(lifecycle.subprocess, "run", forbidden)
    with pytest.raises(ContextRefused):
        lifecycle.qualified_account_listing(account)


async def test_explicit_probe_rejects_expired_or_changed_scope_before_connect():
    opened = []

    async def opener(name):
        opened.append(name)

    probe = mux.RemoteMuxProbe(
        open_manager=opener, scope_reader=lambda: SCOPE, clock=lambda: 100,
    )
    assert await probe.probe_wave("cs", ["m"], inventory(), 1, lambda: True) == {"m": None}
    assert opened == []
    probe.clock = lambda: 1
    probe.scope_reader = lambda: replace(SCOPE, configuration="replacement")
    assert await probe.probe_wave("cs", ["m"], inventory(), 1, lambda: True) == {"m": None}
    assert opened == []


@pytest.mark.parametrize("state,complete,error,verdict", [
    ("Stopped", True, None, False), ("Unknown", True, None, None),
    ("Available", False, None, None), ("Available", True, "failure", None),
])
async def test_explicit_probe_stopped_or_unqualified_never_opens(state, complete, error, verdict):
    value = inventory(rows=(inv.InventoryRow("cs", state, "a"),))
    value = replace(value, listings=(
        replace(value.listings[0], complete=complete, error=error), *value.listings[1:],
    ))

    async def opener(name):
        pytest.fail("unqualified inventory cannot connect")

    probe = mux.RemoteMuxProbe(open_manager=opener, scope_reader=lambda: SCOPE, clock=lambda: 1)
    assert await probe.probe_wave("cs", ["m"], value, 1, lambda: True) == {"m": verdict}


def test_optional_heartbeat_generation_preserves_legacy_none_and_replacement(monkeypatch, tmp_path):
    monkeypatch.setattr(owner, "OWNER_FILE", tmp_path / "owner.json")
    monkeypatch.setattr(owner, "LIVE_FILE", tmp_path / "live.json")
    monkeypatch.setattr(owner, "ensure_runtime_dir", lambda: None)
    monkeypatch.setattr(owner, "_owner_lock", nullcontext)
    now = [1000.0]
    monkeypatch.setattr(owner.time, "time", lambda: now[0])
    first = owner.hold("cs", "t", mux_session="m")
    generation = first.sessions["t"]["generation"]
    now[0] += 1
    assert owner.heartbeat("cs", "t", generation=generation).tenants["t"] == now[0]
    replacement = owner.hold("cs", "t", mux_session="m", fresh=True)
    now[0] += 1
    assert owner.heartbeat("cs", "t", generation=generation).tenants == replacement.tenants
    assert owner.heartbeat("cs", "t").tenants["t"] == now[0]
    now[0] += owner.DEFAULT_TTL + 1
    assert owner.heartbeat("cs", "t", generation=generation) is None
    assert owner.heartbeat("missing", "t", generation=generation) is None


async def test_default_factory_and_consumer_do_not_enter_wave_authority(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("production must not enter the deferred wave foundation")

    monkeypatch.setattr(inv, "capture_scope", forbidden)
    monkeypatch.setattr(inv, "collect_inventory", forbidden)
    monkeypatch.setattr(mux, "RemoteMuxProbe", forbidden)
    monkeypatch.setattr(mux, "RemoteBridgeProbe", forbidden)
    monkeypatch.setattr(lifecycle, "list_codespaces", lambda: [])
    probe = sf.make_remote_mux_probe()
    bridge = sf.make_remote_bridge_probe()
    assert callable(bridge)
    assert await probe("cs", ["m"]) == {"m": None}
    forwards = sf.SessionForwards(lambda *args: None, probe)
    await forwards.probe([])
    await forwards.shutdown()


@pytest.mark.parametrize("cancel_phase", [None, "exec", "cleanup"])
async def test_explicit_probe_cleanup_failure_is_unknown_and_preserves_cancellation(cancel_phase):
    entered, release = asyncio.Event(), asyncio.Event()
    disconnects = []

    class Manager:
        async def exec_command(self, *args, **kwargs):
            if cancel_phase == "exec":
                entered.set()
                await release.wait()
            return SimpleNamespace(exit_code=0, stdout="ACMUX1\t0\tpresent\nACMUX1\tDONE\t1\n")

        async def disconnect(self, codespace):
            disconnects.append(codespace)
            if cancel_phase == "cleanup":
                entered.set()
                await release.wait()
            raise RuntimeError("synthetic disconnect failure")

    async def opener(name):
        return Manager()

    probe = mux.RemoteMuxProbe(
        open_manager=opener, scope_reader=lambda: SCOPE, clock=lambda: 1,
    )
    task = asyncio.create_task(probe.probe_wave("cs", ["m"], inventory(), 1, lambda: True))
    try:
        if cancel_phase is not None:
            await asyncio.wait_for(entered.wait(), timeout=2)
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert await task == {"m": None}
        assert disconnects == ["cs"]
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
