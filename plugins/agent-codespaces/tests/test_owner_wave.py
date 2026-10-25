"""Owner-wave invariants; fixtures never contact a real account or venue."""
from __future__ import annotations

import asyncio
import json
import threading
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent_codespaces import connection_owner as owner
from agent_codespaces import lifecycle, owner_inventory as inv
from agent_codespaces import owner_mux_probe as mux
from agent_codespaces import session_forwards as sf


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(owner, "OWNER_FILE", tmp_path / "owner.json")
    monkeypatch.setattr(owner, "_LOCK_FILE", tmp_path / "owner.lock")
    monkeypatch.setattr(owner, "LIVE_FILE", tmp_path / "live.json")
    monkeypatch.setattr(owner, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(owner, "ensure_runtime_dir", lambda: None)


SCOPE = inv.InventoryScope("source", "config", ("a", "b", "c"), ("x", "y", "z"))


def inventory(wave=1, *, rows=None, observed=0.0):
    rows = rows if rows is not None else (inv.InventoryRow("cs", "Available", "a"),)
    return inv.OwnerInventory(wave, SCOPE, (
        inv.AccountListing("a", rows, observed, True),
        inv.AccountListing("b", (), observed, True),
        inv.AccountListing("c", (), observed, True),
    ))


def lookup(value, **kwargs):
    return value.lookup("cs", wave=1, scope=SCOPE, now=1, max_age=30, **kwargs)


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
def test_qualified_listing_reports_errors_and_limit(monkeypatch, payload, complete, error):
    monkeypatch.setattr(inv, "credential", lambda account: "fake-credential")
    monkeypatch.setattr(lifecycle, "validate_context", lambda: None)
    monkeypatch.setattr(lifecycle.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=payload, stderr="",
    ))
    result = lifecycle.qualified_account_listing("a", clock=lambda: 1)
    assert (result.complete, result.error) == (complete, error)


async def test_wave_generation_and_freshness_before_connect():
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


async def test_slow_venue_does_not_block_sibling_and_shutdown_drains(store):
    for name in ("slow", "fast", "queued"):
        owner.hold(name, "t", mux_session="m", confirmed=True)
    entered, fast = asyncio.Event(), asyncio.Event()
    stopped = []
    active = 0
    maximum = 0

    async def probe(name, targets):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        try:
            if name == "slow":
                entered.set()
                await asyncio.Event().wait()
            fast.set()
            return {"m": True}
        finally:
            active -= 1
            stopped.append(name)

    forwards = sf.SessionForwards(lambda *a: None, probe, probe_concurrency=2)
    running = asyncio.create_task(forwards.probe(owner.list_holds()))
    await asyncio.wait_for(entered.wait(), 2)
    await asyncio.wait_for(fast.wait(), 2)
    await forwards.shutdown()
    await asyncio.gather(running, return_exceptions=True)
    assert "slow" in stopped and "fast" in stopped
    assert maximum <= 2 and active == 0
    assert not forwards._probe_tasks


async def test_timeout_disconnects_only_owned_manager(store):
    owner.hold("cs", "t", mux_session="m", confirmed=True)
    closed = []

    class Manager:
        async def exec_command(self, *args, **kwargs):
            await asyncio.Event().wait()

        async def disconnect(self, name):
            closed.append(name)

    async def opener(name):
        return Manager()

    probe = sf.make_remote_mux_probe(
        list_codespaces=lambda: [SimpleNamespace(name="cs", state="Available")],
        open_manager=opener,
    )
    forwards = sf.SessionForwards(lambda *a: None, probe, probe_timeout=0.1)
    await forwards.probe(owner.list_holds())
    assert closed == ["cs"]
    assert owner.get_hold("cs") is not None


async def test_cancel_inventory_drains_owned_thread(store):
    owner.hold("cs", "t", mux_session="m")
    entered, finish = threading.Event(), threading.Event()
    drained = []

    def read(wave):
        entered.set()
        assert finish.wait(2)
        drained.append(True)
        return inventory(wave)

    forwards = sf.SessionForwards(lambda *a: None, mux.RemoteMuxProbe(inventory_reader=read))
    task = asyncio.create_task(forwards.probe(owner.list_holds()))
    await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert drained == [True]


def test_heartbeat_current_stale_none_and_expired(store, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(owner.time, "time", lambda: now[0])
    first = owner.hold("cs", "t", mux_session="m")
    generation = first.sessions["t"]["generation"]
    expires = first.sessions["t"]["expires_at"]
    now[0] += 1
    assert owner.heartbeat("cs", "t", generation=generation).tenants["t"] == now[0]
    replacement = owner.hold("cs", "t", mux_session="m", fresh=True)
    now[0] += 1
    assert owner.heartbeat("cs", "t", generation=generation).tenants == replacement.tenants
    assert owner.heartbeat("cs", "t").tenants["t"] == now[0]
    assert first.sessions["t"]["expires_at"] == expires
    now[0] += owner.DEFAULT_TTL + 1
    assert owner.heartbeat("cs", "t", generation=generation) is None
    assert owner.heartbeat("missing", "t", generation=generation) is None


def test_heartbeat_replacement_wins_inside_lock(store, monkeypatch):
    first = owner.hold("cs", "t", mux_session="m")
    old = first.sessions["t"]["generation"]
    original_lock = owner._owner_lock
    waiting, proceed = threading.Event(), threading.Event()

    @contextmanager
    def delayed_lock():
        if threading.current_thread().name == "old-heartbeat":
            waiting.set()
            assert proceed.wait(2)
        with original_lock():
            yield

    monkeypatch.setattr(owner, "_owner_lock", delayed_lock)
    result = []
    thread = threading.Thread(
        name="old-heartbeat", target=lambda: result.append(owner.heartbeat("cs", "t", generation=old)),
    )
    thread.start()
    try:
        assert waiting.wait(2)
        replacement = owner.hold("cs", "t", mux_session="m", fresh=True)
        proceed.set()
        thread.join(2)
        assert not thread.is_alive()
        assert result[0].tenants == replacement.tenants
        assert result[0].sessions == replacement.sessions
    finally:
        proceed.set()
        thread.join(2)


async def test_positive_old_probe_cannot_renew_replacement(store):
    old = owner.hold("cs", "t", mux_session="m")
    entered, resume = asyncio.Event(), asyncio.Event()

    async def probe(name, targets):
        entered.set()
        await resume.wait()
        return {"m": True}

    forwards = sf.SessionForwards(lambda *a: None, probe)
    task = asyncio.create_task(forwards.probe([old]))
    await entered.wait()
    replacement = owner.hold("cs", "t", mux_session="m", fresh=True)
    resume.set()
    await task
    assert owner.get_hold("cs").tenants == replacement.tenants


@pytest.mark.parametrize("state,complete,error,verdict", [
    ("Shutdown", True, None, False),
    ("Unknown", True, None, None),
    ("Available", False, None, None),
    ("Available", False, "RuntimeError", None),
])
async def test_stopped_or_unqualified_never_opens(state, complete, error, verdict):
    opened = []

    async def opener(name):
        opened.append(name)

    value = inventory(rows=(inv.InventoryRow("cs", state, "a"),))
    value = replace(value, listings=(
        replace(value.listings[0], complete=complete, error=error), *value.listings[1:],
    ))
    probe = mux.RemoteMuxProbe(open_manager=opener, scope_reader=lambda: SCOPE, clock=lambda: 1)
    assert await probe.probe_wave("cs", ["m"], value, 1, lambda: True) == {"m": verdict}
    assert opened == []


async def test_queued_expired_available_does_not_connect_or_relist(store):
    for name in ("first", "queued"):
        owner.hold(name, "t", mux_session="m", confirmed=True)
    now = [0.0]
    listings, opened, closed = [], [], []

    def read(wave):
        listings.append(wave)
        return inventory(wave, rows=tuple(inv.InventoryRow(name, "Available", "a")
                                         for name in ("first", "queued")))

    class Manager:
        async def exec_command(self, *a, **k):
            now[0] = 31
            return SimpleNamespace(exit_code=0, stdout="ACMUX1\t0\tpresent\nACMUX1\tDONE\t1\n")

        async def disconnect(self, name):
            closed.append(name)

    async def opener(name):
        opened.append(name)
        return Manager()

    probe = mux.RemoteMuxProbe(
        inventory_reader=read, scope_reader=lambda: SCOPE, clock=lambda: now[0], open_manager=opener,
    )
    forwards = sf.SessionForwards(lambda *a: None, probe, probe_concurrency=1)
    await forwards.probe(owner.list_holds())
    assert len(listings) == 1
    assert opened == closed == ["first"]
    assert owner.get_hold("queued") is not None


async def test_default_opener_pins_account_without_lookup_and_closes_on_connect_failure(monkeypatch):
    token = "fixture-only"
    scope = replace(SCOPE, credentials=(inv._digest(token), "y", "z"))
    value = replace(inventory(), scope=scope)
    observed = []

    class Manager:
        async def ensure_connected(self, name, source, forwards):
            observed.append(("connect", name))
            raise RuntimeError("connection failed")

        async def disconnect(self, name):
            observed.append(("disconnect", name))

    def source(name, account, token, remaining):
        observed.append(("source", account, token))
        return SimpleNamespace(get_ssh_config=lambda: None)

    monkeypatch.setattr(mux, "_admitted_manager", lambda remaining: Manager())
    monkeypatch.setattr(mux, "_admitted_source", source)
    monkeypatch.setattr(mux, "credential", lambda account: token)
    monkeypatch.setattr(lifecycle, "account_for_codespace",
                        lambda name: pytest.fail("hidden account relisting"))
    probe = mux.RemoteMuxProbe(scope_reader=lambda: scope, clock=lambda: 1)
    assert await probe.probe_wave("cs", ["m"], value, 1, lambda: True) == {"m": None}
    assert observed == [("source", "a", token), ("connect", "cs"), ("disconnect", "cs")]


def test_config_fetch_has_one_bounded_attempt(monkeypatch, tmp_path):
    import subprocess

    calls = []

    def run(args, **kwargs):
        calls.append(kwargs["timeout"])
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(mux.subprocess, "run", run)
    source = mux._admitted_source("cs", "a", "fixture", lambda: 0.5)
    source._config_dir = tmp_path
    with pytest.raises(subprocess.TimeoutExpired):
        source.get_ssh_config()
    assert calls == [0.5]


async def test_exec_retry_cannot_use_expired_admission():
    calls = []
    budget = [1]

    class Manager:
        async def exec_command(self, *args, **kwargs):
            calls.append(kwargs["timeout"])
            budget[0] = 0
            return SimpleNamespace(exit_code=255)

    async def remaining():
        if not budget[0]:
            raise RuntimeError("expired")
        return budget[0]

    adapter = mux._AdmittedExec(Manager(), remaining)
    await adapter.exec_command("cs", "command", timeout=30)
    with pytest.raises(RuntimeError, match="expired"):
        await adapter.exec_command("cs", "command", timeout=30)
    assert calls == [1]


async def test_shutdown_never_restarts_owed_push(store):
    owner.hold("cs", "t", mux_session="m")
    entered = asyncio.Event()
    pushes = []

    class Mirror:
        def owed_codespaces(self):
            return ["owed"]

        async def push_owed(self, name):
            pushes.append(name)

    async def probe(*args):
        entered.set()
        await asyncio.Event().wait()

    forwards = sf.SessionForwards(lambda *a: None, probe, transcript_mirror=Mirror())
    task = asyncio.create_task(forwards.probe(owner.list_holds()))
    await entered.wait()
    await forwards.shutdown()
    await asyncio.gather(task, return_exceptions=True)
    await forwards.probe([])
    assert pushes == [] and not forwards._mirroring


@pytest.mark.parametrize("timeout_first", [False, True])
async def test_shutdown_waits_for_unequal_probe_cancellation_drains(store, timeout_first, monkeypatch):
    for name in ("fast", "draining"):
        owner.hold(name, "t", mux_session="m")
    entered, release = threading.Event(), threading.Event()
    fast_entered = asyncio.Event()
    finished = []

    def config_worker():
        entered.set()
        assert release.wait(5)
        finished.append(True)

    async def probe(name, targets):
        if name == "draining":
            await inv.joined_thread(config_worker)
        else:
            fast_entered.set()
            await asyncio.Event().wait()
        return {"m": None}

    if timeout_first:
        wait_for = asyncio.wait_for

        async def start_then_timeout(awaitable, timeout):
            if timeout == 0.01:
                await fast_entered.wait()
                assert await asyncio.to_thread(entered.wait, 2)
            return await wait_for(awaitable, timeout)

        # Arm the unchanged short timeout only once both synthetic operations
        # have entered; registry startup latency is not this fault's subject.
        monkeypatch.setattr(asyncio, "wait_for", start_then_timeout)
    forwards = sf.SessionForwards(
        lambda *a: None, probe, probe_timeout=0.01 if timeout_first else 90,
    )
    wave = asyncio.create_task(forwards.probe(owner.list_holds()))
    await fast_entered.wait()
    assert await asyncio.to_thread(entered.wait, 2)
    if timeout_first:
        await asyncio.sleep(0.03)
    closing = asyncio.create_task(forwards.shutdown())
    try:
        # Let the immediately-cancelled sibling unwind through both gathers.
        for _ in range(10):
            await asyncio.sleep(0)
        assert not closing.done()
        assert not finished
    finally:
        release.set()
        await closing
        await asyncio.gather(wave, return_exceptions=True)
    assert finished == [True] and not forwards._probe_tasks


@pytest.mark.parametrize("during", ["exec", "disconnect"])
async def test_scope_change_during_remote_work_is_unknown(during):
    selected = [SCOPE]

    class Manager:
        async def exec_command(self, *a, **k):
            if during == "exec":
                selected[0] = replace(SCOPE, configuration="changed")
            return SimpleNamespace(exit_code=0, stdout="ACMUX1\t0\tgone\nACMUX1\tDONE\t1\n")

        async def disconnect(self, name):
            if during == "disconnect":
                selected[0] = replace(SCOPE, configuration="changed")

    async def opener(name):
        return Manager()

    probe = mux.RemoteMuxProbe(
        scope_reader=lambda: selected[0], clock=lambda: 1, open_manager=opener,
    )
    assert await probe.probe_wave("cs", ["m"], inventory(), 1, lambda: True) == {"m": None}


async def test_scope_change_during_credential_lookup_cannot_fetch_config(monkeypatch):
    token = "fixture"
    scope = replace(SCOPE, credentials=(inv._digest(token), "y", "z"))
    selected = [scope]

    def credential(account):
        selected[0] = replace(scope, source="replaced")
        return token

    class Source:
        def get_ssh_config(self):
            pytest.fail("stale scope performed config fetch")

    monkeypatch.setattr(mux, "credential", credential)
    monkeypatch.setattr(mux, "_admitted_source", lambda *a: Source())
    probe = mux.RemoteMuxProbe(scope_reader=lambda: selected[0], clock=lambda: 1)
    value = replace(inventory(), scope=scope)
    assert await probe.probe_wave("cs", ["m"], value, 1, lambda: True) == {"m": None}


def test_owner_credential_bypasses_legacy_cache_and_preserves_provider(monkeypatch):
    from agent_codespaces import gh_account, picker_auth

    selected, provider = ["old"], [None]
    monkeypatch.setattr(picker_auth, "credential_token", lambda account: provider[0])
    monkeypatch.setattr(gh_account.shutil, "which", lambda name: "fixture-gh")
    monkeypatch.setattr(gh_account.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=selected[0],
    ))
    gh_account._token_for_account.cache_clear()
    try:
        assert gh_account._token_for_account("fixture-account") == "old"
        selected[0] = "new"
        assert inv.credential("fixture-account") == "new"
        assert gh_account._token_for_account("fixture-account") == "old"
        provider[0] = "provider"
        assert inv.credential("fixture-account") == "provider"
    finally:
        gh_account._token_for_account.cache_clear()


async def test_listing_only_injection_retains_default_opener(monkeypatch):
    opened = []

    class Manager:
        async def exec_command(self, *a, **k):
            return SimpleNamespace(exit_code=0, stdout="ACMUX1\t0\tpresent\nACMUX1\tDONE\t1\n")

        async def disconnect(self, name):
            pass

    async def opener(name):
        opened.append(name)
        return Manager()

    monkeypatch.setattr(sf, "_open_codespace", opener)
    probe = sf.make_remote_mux_probe(
        list_codespaces=lambda: [SimpleNamespace(name="cs", state="Available")],
    )
    assert await probe("cs", ["m"]) == {"m": True}
    assert opened == ["cs"]


async def test_contended_registry_does_not_block_sibling_probe(store, monkeypatch):
    for name in ("slow", "fast"):
        owner.hold(name, "t", mux_session="m")
    release, entered = threading.Event(), threading.Event()
    fast = asyncio.Event()
    real = sf.get_hold

    def read(name, ttl):
        if name == "slow":
            entered.set()
            assert release.wait(5)
        return real(name, ttl)

    async def probe(name, targets):
        if name == "fast":
            fast.set()
        return {"m": None}

    monkeypatch.setattr(sf, "get_hold", read)
    forwards = sf.SessionForwards(lambda *a: None, probe)
    task = asyncio.create_task(forwards.probe(owner.list_holds()))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        await asyncio.wait_for(fast.wait(), 2)
    finally:
        release.set()
        await task


async def test_default_bridge_reuses_inventory_not_transport(store, monkeypatch):
    owner.hold("cs", "t", mux_session="m", daemon_port=12345)
    acquisitions, operations, closed = [], [], []

    def read(wave):
        acquisitions.append(wave)
        return inventory(wave)

    class Manager:
        async def exec_command(self, name, command, **kwargs):
            operations.append("mux" if "ACMUX1" in command else "bridge")
            return SimpleNamespace(exit_code=0, stdout="ACMUX1\t0\tpresent\nACMUX1\tDONE\t1\n")

        async def disconnect(self, name):
            closed.append(name)

    async def opener(name):
        return Manager()

    monkeypatch.setattr(lifecycle, "list_codespaces", lambda: pytest.fail("hidden relisting"))
    probe = mux.RemoteMuxProbe(
        inventory_reader=read, scope_reader=lambda: SCOPE, clock=lambda: 1, open_manager=opener,
    )
    forwards = sf.SessionForwards(
        lambda *a: None, probe, bridge_probe=sf.make_remote_bridge_probe(),
    )
    forwards._channels["cs"] = (12345, SimpleNamespace(is_alive=True))
    await forwards.probe(owner.list_holds())
    assert len(acquisitions) == 1
    assert operations == ["mux", "bridge"]
    assert closed == ["cs", "cs"]


@pytest.mark.parametrize("change", ["scope", "expiry"])
async def test_bridge_await_invalidates_cached_mux_verdict(store, change):
    first = owner.hold("cs", "t", mux_session="m", daemon_port=12345)
    scope, clock, mirrors = [SCOPE], [1], []

    class Manager:
        async def exec_command(self, *a, **k):
            return SimpleNamespace(exit_code=0, stdout="ACMUX1\t0\tpresent\nACMUX1\tDONE\t1\n")

        async def disconnect(self, name):
            pass

    async def opener(name):
        return Manager()

    async def bridge(name, port):
        if change == "scope":
            scope[0] = replace(SCOPE, source="replacement")
        else:
            clock[0] = 31
        return None

    async def mirror(name):
        mirrors.append(name)

    probe = mux.RemoteMuxProbe(
        inventory_reader=lambda wave: inventory(wave), scope_reader=lambda: scope[0],
        clock=lambda: clock[0], open_manager=opener,
    )
    forwards = sf.SessionForwards(
        lambda *a: None, probe, bridge_probe=bridge, transcript_mirror=mirror,
    )
    forwards._channels["cs"] = (12345, SimpleNamespace(is_alive=True))
    await forwards.probe([first])
    assert not forwards._mirroring and mirrors == []
    assert owner.get_hold("cs").tenants == first.tenants


async def test_control_master_result_reaches_owner_before_cancellation(monkeypatch):
    import ssh_manager

    opened, disposed = [], []

    class Base:
        async def _start_control_master(self, *a, **k):
            resource = object()
            opened.append(resource)
            asyncio.current_task().cancel()
            return resource

        async def ensure_connected(self):
            self.resource = await self._start_control_master()

        async def disconnect(self):
            disposed.append(self.resource)

    async def admit():
        return 1

    monkeypatch.setattr(ssh_manager, "ConnectionManager", Base)
    manager = mux._admitted_manager(admit)
    try:
        with pytest.raises(asyncio.CancelledError):
            await inv.bounded_operation(manager.ensure_connected(), 1)
    finally:
        await inv.finish_cleanup(manager.disconnect())
    assert opened == disposed and len(opened) == 1


@pytest.mark.parametrize("outcome", ["ok", "error", "timeout"])
def test_owner_config_fetch_retains_dial_diagnostics(monkeypatch, tmp_path, outcome):
    import subprocess
    from ssh_manager import dial_log

    records = []
    monkeypatch.setattr(dial_log, "record", lambda *a, **k: records.append(k))

    def run(args, **kwargs):
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        return SimpleNamespace(
            returncode=0 if outcome == "ok" else 1,
            stdout="Host fixture\n  HostName fixture\n", stderr="fixture failure",
        )

    monkeypatch.setattr(mux.subprocess, "run", run)
    source = mux._admitted_source("cs", "a", "fixture", lambda: 1)
    source._config_dir = tmp_path
    if outcome == "ok":
        source.get_ssh_config()
    else:
        with pytest.raises((RuntimeError, subprocess.TimeoutExpired)):
            source.get_ssh_config()
    assert len(records) == 1
    assert records[0]["kind"] == "config_fetch"
    assert records[0]["outcome"] == outcome
    assert records[0]["attempt"] == 1 and records[0]["account"] == "pinned"


@pytest.mark.parametrize("state", ["Shutdown", "Available"])
@pytest.mark.parametrize("change", ["scope", "expiry", "unchanged"])
async def test_negative_verdict_revalidated_after_registry_await(store, monkeypatch, state, change):
    first = owner.hold("cs", "t", mux_session="m", confirmed=True)
    selected, now, reads = [SCOPE], [1], []
    real = sf.get_hold

    def read(name, ttl):
        reads.append(name)
        if len(reads) == 2:
            if change == "scope":
                selected[0] = replace(SCOPE, configuration="changed")
            elif change == "expiry":
                now[0] = 31
        return real(name, ttl)

    class NegativeProbe(mux.RemoteMuxProbe):
        async def probe_wave(self, codespace, targets, *a, **k):
            return dict.fromkeys(targets, False)

    probe = NegativeProbe(
        inventory_reader=lambda wave: inventory(wave, rows=(inv.InventoryRow("cs", state, "a"),)),
        scope_reader=lambda: selected[0], clock=lambda: now[0],
    )
    monkeypatch.setattr(sf, "get_hold", read)
    await sf.SessionForwards(lambda *a: None, probe).probe([first])
    if change == "unchanged":
        assert owner.get_hold("cs") is None
    else:
        assert owner.get_hold("cs").tenants == first.tenants
