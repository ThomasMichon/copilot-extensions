"""Tests for detached CLI-mode session support in the Connection Owner:
session tenants + the host-bridge-daemon forward (``session_forwards``), the
registry fields that carry them, and the Owner singleton guard.
"""

from __future__ import annotations

import json
import time
import types

import pytest
from agent_codespaces import connection_owner as owner
from agent_codespaces import session_forwards as sf


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(owner, "OWNER_FILE", tmp_path / "connection-owner.json")
    monkeypatch.setattr(owner, "_LOCK_FILE", tmp_path / "connection-owner.lock")
    monkeypatch.setattr(owner, "LIVE_FILE", tmp_path / "connection-owner.live.json")
    monkeypatch.setattr(owner, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(owner, "ensure_runtime_dir", lambda: None)
    return tmp_path


class FakeChannel:
    def __init__(self, key, *, fail_start: bool = False) -> None:
        self.key = key
        self.fail_start = fail_start
        self.starts = 0
        self.stops = 0
        self._alive = False

    @property
    def is_alive(self) -> bool:
        return self._alive

    async def start(self) -> None:
        self.starts += 1
        if self.fail_start:
            raise RuntimeError("boom")
        self._alive = True

    async def stop(self) -> None:
        self.stops += 1
        self._alive = False


def _relay_factory(created):
    def make(codespace):
        created[codespace] = FakeChannel(codespace)
        return created[codespace]
    return make


def _daemon_factory(created, *, fail=()):
    def make(codespace, port):
        created[(codespace, port)] = FakeChannel((codespace, port), fail_start=codespace in fail)
        return created[(codespace, port)]
    return make


# -- registry: session tenants + daemon_port -------------------------------------

def test_session_hold_records_mux_and_daemon_port(store):
    h = owner.hold("cs-1", "cli:x@cs-1", daemon_port=41234, mux_session="wt-x")
    assert h.daemon_port == 41234
    assert h.sessions["cli:x@cs-1"]["mux_session"] == "wt-x"
    again = owner.get_hold("cs-1")
    assert again.daemon_port == 41234 and "cli:x@cs-1" in again.sessions


def test_rehold_never_extends_the_absolute_lease(store):
    first = owner.hold("cs-1", "cli:t", mux_session="wt-x", max_lease=100.0)
    expires = first.sessions["cli:t"]["expires_at"]
    time.sleep(0.01)
    again = owner.hold("cs-1", "cli:t", mux_session="wt-x", max_lease=10_000.0)
    assert again.sessions["cli:t"]["expires_at"] == expires


def test_expired_session_lease_is_pruned_with_its_forward(store, monkeypatch):
    owner.hold("cs-1", "cli:t", daemon_port=41234, mux_session="wt-x", max_lease=5.0)
    real = time.time
    monkeypatch.setattr(owner.time, "time", lambda: real() + 60.0)
    assert owner.get_hold("cs-1") is None  # the only tenant hit its cap -> hold gone


def test_releasing_last_session_tenant_clears_daemon_port(store):
    owner.hold("cs-1", "ssh:1")
    owner.hold("cs-1", "cli:t", daemon_port=41234, mux_session="wt-x")
    h = owner.release("cs-1", "cli:t")
    assert h is not None and h.daemon_port is None and not h.sessions


def test_malformed_session_fields_are_tolerated(store):
    owner.OWNER_FILE.write_text(json.dumps({
        "cs-1": {
            "codespace": "cs-1", "host": "h", "created_at": 1.0,
            "heartbeat_at": time.time(), "tenants": {"a": time.time()},
            "daemon_port": "not-a-port",
            "sessions": {"a": {"mux_session": 5}, "b": "junk"},
        },
    }), encoding="utf-8")
    h = owner.get_hold("cs-1")
    assert h is not None and h.daemon_port is None and h.sessions == {}


# -- SessionForwards: daemon forwards --------------------------------------------

async def test_daemon_forward_follows_holds(store):
    created = {}
    forwards = sf.SessionForwards(_daemon_factory(created))
    owner_mgr = owner.ConnectionOwner(_relay_factory({}), sessions=forwards)
    owner.hold("cs-1", "cli:t", daemon_port=41234, mux_session="wt-x")

    await owner_mgr.reconcile()
    assert owner_mgr.active_daemon_forwards() == {"cs-1": 41234}

    owner.release("cs-1", "cli:t")
    await owner_mgr.reconcile()
    assert owner_mgr.active_daemon_forwards() == {}
    assert created[("cs-1", 41234)].stops >= 1


async def test_plain_tenant_gets_relay_but_no_daemon_forward(store):
    relays, daemons = {}, {}
    owner_mgr = owner.ConnectionOwner(
        _relay_factory(relays), sessions=sf.SessionForwards(_daemon_factory(daemons)),
    )
    owner.hold("cs-1", "ssh:1")
    await owner_mgr.reconcile()
    assert owner_mgr.active_codespaces() == {"cs-1"}
    assert daemons == {}


async def test_failed_daemon_forward_is_retried_next_cycle(store):
    created = {}
    forwards = sf.SessionForwards(_daemon_factory(created, fail={"cs-1"}))
    owner.hold("cs-1", "cli:t", daemon_port=41234, mux_session="wt-x")
    holds = {h.codespace: h for h in owner.list_holds()}
    await forwards.reconcile(holds)
    assert forwards.active() == {}
    await forwards.reconcile(holds)
    assert len([k for k in created]) == 1  # rebuilt under the same key
    assert created[("cs-1", 41234)].starts == 1  # a fresh channel each attempt


async def test_shutdown_stops_daemon_forwards(store):
    created = {}
    forwards = sf.SessionForwards(_daemon_factory(created))
    owner.hold("cs-1", "cli:t", daemon_port=41234, mux_session="wt-x")
    await forwards.reconcile({h.codespace: h for h in owner.list_holds()})
    await forwards.shutdown()
    assert forwards.active() == {}
    assert created[("cs-1", 41234)].stops == 1


# -- extra reverse forwards (fixed host port) ------------------------------------

def _any_factory(created):
    def make(codespace, port, host_port=None):
        created[(codespace, port, host_port)] = FakeChannel((codespace, port, host_port))
        return created[(codespace, port, host_port)]
    return make


def test_reverse_forwards_persist_sanitized_and_clear_with_the_session(store):
    owner.hold("cs-1", "ssh:1")
    h = owner.hold("cs-1", "cli:t", daemon_port=41234, mux_session="wt-x",
                   reverse_forwards={9222: 50111, 0: 1, "x": 5})
    assert h.reverse_forwards == {"9222": 50111}
    assert owner.get_hold("cs-1").reverse_forwards == {"9222": 50111}
    kept = owner.hold("cs-1", "cli:t", mux_session="wt-x")  # rejoin without the flag
    assert kept.reverse_forwards == {"9222": 50111}
    assert owner.release("cs-1", "cli:t").reverse_forwards == {}


async def test_reverse_forward_follows_the_hold_with_its_fixed_host_port(store):
    created = {}
    forwards = sf.SessionForwards(_any_factory(created))
    owner.hold("cs-1", "cli:t", daemon_port=41234, mux_session="wt-x",
               reverse_forwards={9222: 50111})
    await forwards.reconcile({h.codespace: h for h in owner.list_holds()})
    assert forwards.active_reverse_forwards() == {"cs-1": {9222: 50111}}
    assert ("cs-1", 41234, None) in created  # the bridge forward is unchanged

    owner.hold("cs-1", "cli:t", mux_session="wt-x", reverse_forwards={9222: 50222})
    await forwards.reconcile({h.codespace: h for h in owner.list_holds()})
    assert created[("cs-1", 9222, 50111)].stops == 1
    assert forwards.active_reverse_forwards() == {"cs-1": {9222: 50222}}

    await forwards.shutdown()
    assert forwards.active_reverse_forwards() == {}


# -- local forwards (host port -> venue port) ------------------------------------

def _local_factory(created):
    def make(codespace, host_port, venue_port):
        created[(codespace, host_port, venue_port)] = FakeChannel((codespace, host_port, venue_port))
        return created[(codespace, host_port, venue_port)]
    return make


def test_local_forwards_persist_sanitized_and_clear_with_the_session(store):
    owner.hold("cs-1", "ssh:1")
    h = owner.hold("cs-1", "cli:t", daemon_port=41234, mux_session="wt-x",
                   local_forwards={41909: 41909, 0: 1, "x": 5})
    assert h.local_forwards == {"41909": 41909}
    kept = owner.hold("cs-1", "cli:t", mux_session="wt-x")  # rejoin without the flag
    assert kept.local_forwards == {"41909": 41909}
    assert owner.release("cs-1", "cli:t").local_forwards == {}


async def test_local_forward_follows_the_hold_and_stops_on_shutdown(store):
    created = {}
    forwards = sf.SessionForwards(_any_factory({}), local_factory=_local_factory(created))
    owner.hold("cs-1", "cli:t", daemon_port=41234, mux_session="wt-x",
               local_forwards={41909: 41909})
    await forwards.reconcile({h.codespace: h for h in owner.list_holds()})
    assert forwards.active_local_forwards() == {"cs-1": {41909: 41909}}

    owner.hold("cs-1", "cli:t", mux_session="wt-x", local_forwards={41909: 5000})
    await forwards.reconcile({h.codespace: h for h in owner.list_holds()})
    assert created[("cs-1", 41909, 41909)].stops == 1
    assert forwards.active_local_forwards() == {"cs-1": {41909: 5000}}

    owner.release("cs-1", "cli:t")
    await forwards.reconcile({h.codespace: h for h in owner.list_holds()})
    assert forwards.active_local_forwards() == {}
    assert created[("cs-1", 41909, 5000)].stops == 1


async def test_local_forwards_are_ignored_without_a_local_factory(store):
    forwards = sf.SessionForwards(_any_factory({}))
    owner.hold("cs-1", "cli:t", daemon_port=41234, mux_session="wt-x", local_forwards={41909: 41909})
    await forwards.reconcile({h.codespace: h for h in owner.list_holds()})
    assert forwards.active_local_forwards() == {}


def test_local_forward_factory_pins_the_host_port(store):
    made = {}

    class Forward:
        def __init__(self, cfg, remote_port, *, local_port):
            made.update(cfg=cfg, remote_port=remote_port, local_port=local_port)
            self.is_alive = False

    class Source:
        def __init__(self, codespace, gh_env=None):
            self.codespace = codespace

        def get_ssh_config(self):
            return f"cfg:{self.codespace}"

    channel = sf.make_local_forward_factory(forward_cls=Forward, config_source_cls=Source)("cs-1", 41909, 5000)
    assert made == {"cfg": "cfg:cs-1", "remote_port": 5000, "local_port": 41909}
    assert channel.is_alive is False


def test_supervised_factory_targets_a_fixed_host_port(store):
    made = {}

    class Relay:
        def __init__(self, cfg, listen, host_port_resolver):
            made["listen"], made["host"] = listen, host_port_resolver()

    class Source:
        def __init__(self, cs, gh_env=None):
            pass

        def get_ssh_config(self):
            return object()

    make = sf.make_supervised_daemon_forward_factory(
        relay_cls=Relay, config_source_cls=Source, port_resolver=lambda: 7000,
    )
    make("cs-1", 9222, 50111)
    assert made == {"listen": 9222, "host": 50111}
    make("cs-1", 41234)
    assert made == {"listen": 41234, "host": 7000}


# -- SessionForwards: venue-probe renewal ----------------------------------------

def _probe(verdicts, calls):
    async def probe(codespace, muxes):
        calls.append((codespace, list(muxes)))
        return {m: verdicts.get(m) for m in muxes}
    return probe


async def test_probe_true_renews_false_releases_none_leaves(store):
    owner.hold("cs-1", "cli:a", mux_session="wt-a", confirmed=True)
    owner.hold("cs-1", "cli:b", mux_session="wt-b", confirmed=True)
    owner.hold("cs-1", "cli:c", mux_session="wt-c", confirmed=True)
    before = owner.get_hold("cs-1").tenants["cli:a"]
    time.sleep(0.01)
    calls = []
    forwards = sf.SessionForwards(
        _daemon_factory({}),
        _probe({"wt-a": True, "wt-b": False, "wt-c": None}, calls),
    )
    await forwards.probe(owner.list_holds())
    h = owner.get_hold("cs-1")
    assert h.tenants["cli:a"] > before
    assert "cli:b" not in h.tenants
    assert "cli:c" in h.tenants
    assert calls == [("cs-1", ["wt-a", "wt-b", "wt-c"])]


async def test_probe_is_rate_limited_per_codespace(store):
    owner.hold("cs-1", "cli:a", mux_session="wt-a")
    now = {"t": 1000.0}
    calls = []
    forwards = sf.SessionForwards(
        _daemon_factory({}), _probe({"wt-a": True}, calls),
        probe_interval=120.0, clock=lambda: now["t"],
    )
    await forwards.probe(owner.list_holds())
    now["t"] += 60.0
    await forwards.probe(owner.list_holds())
    assert len(calls) == 1
    now["t"] += 61.0
    await forwards.probe(owner.list_holds())
    assert len(calls) == 2


async def test_probe_failure_neither_renews_nor_releases(store):
    owner.hold("cs-1", "cli:a", mux_session="wt-a")

    async def boom(codespace, muxes):
        raise RuntimeError("network")

    forwards = sf.SessionForwards(_daemon_factory({}), boom)
    await forwards.probe(owner.list_holds())
    assert "cli:a" in owner.get_hold("cs-1").tenants


async def test_owner_reconcile_releases_gone_session_and_its_forward(store):
    daemons = {}
    forwards = sf.SessionForwards(_daemon_factory(daemons), _probe({"wt-a": False}, []))
    owner_mgr = owner.ConnectionOwner(_relay_factory({}), sessions=forwards)
    owner.hold("cs-1", "cli:a", daemon_port=41234, mux_session="wt-a", confirmed=True)
    await owner_mgr.reconcile()
    assert owner.get_hold("cs-1") is None
    assert owner_mgr.active_codespaces() == set()
    assert owner_mgr.active_daemon_forwards() == {}


# -- default remote mux probe ------------------------------------------------------

class _Result:
    def __init__(self, code):
        self.exit_code = code


class _Manager:
    def __init__(self, codes):
        self.codes = codes
        self.commands = []
        self.disconnected = False

    async def exec_command(self, name, command, timeout=None):
        self.commands.append(command)
        return _Result(self.codes.pop(0))

    async def disconnect(self, name):
        self.disconnected = True


def _cs(name, state):
    return types.SimpleNamespace(name=name, state=state)


async def test_remote_probe_never_connects_to_a_stopped_codespace():
    opened = []

    async def opener(name):
        opened.append(name)
        return _Manager([0])

    probe = sf.make_remote_mux_probe(
        list_codespaces=lambda: [_cs("cs-1", "Shutdown")], open_manager=opener,
    )
    assert await probe("cs-1", ["wt-a"]) == {"wt-a": False}
    assert opened == []


async def test_remote_probe_codespace_missing_from_listing_is_unknown():
    # A per-account listing can fail partially; absence is not proof it is gone
    # (the tenant still lapses by TTL, since unknown never renews it).
    probe = sf.make_remote_mux_probe(list_codespaces=lambda: [], open_manager=None)
    assert await probe("cs-gone", ["wt-a"]) == {"wt-a": None}


async def test_remote_probe_maps_tmux_exit_codes():
    # 255 is an SSH transport failure: retried once, then reported unknown.
    manager = _Manager([0, 1, 255, 255])

    async def opener(name):
        return manager

    probe = sf.make_remote_mux_probe(
        list_codespaces=lambda: [_cs("cs-1", "Available")], open_manager=opener,
    )
    import ssh_manager.manager as _sm

    async def _no_sleep(s):
        return None

    _real_sleep, _sm.asyncio.sleep = _sm.asyncio.sleep, _no_sleep
    try:
        got = await probe("cs-1", ["wt-a", "wt-b", "wt-c"])
    finally:
        _sm.asyncio.sleep = _real_sleep
    assert got == {"wt-a": True, "wt-b": False, "wt-c": None}
    assert len(manager.commands) == 4
    assert "has-session -t" in manager.commands[0] and "=wt-a" in manager.commands[0]
    assert manager.disconnected


async def test_remote_probe_list_failure_is_unknown():
    def boom():
        raise RuntimeError("gh down")

    probe = sf.make_remote_mux_probe(list_codespaces=boom, open_manager=None)
    assert await probe("cs-1", ["wt-a"]) == {"wt-a": None}


def test_daemon_forward_factory_uses_listen_port_and_live_host_port():
    built = {}

    class _Relay:
        def __init__(self, cfg, port, *, host_port_resolver):
            built.update(cfg=cfg, port=port, resolver=host_port_resolver)

    class _Source:
        def __init__(self, name, gh_env=None):
            self.name = name

        def get_ssh_config(self):
            return f"cfg:{self.name}"

    live = {"port": 5001}
    make = sf.make_supervised_daemon_forward_factory(
        relay_cls=_Relay, config_source_cls=_Source, port_resolver=lambda: live["port"],
    )
    make("cs-1", 41234)
    assert built["cfg"] == "cfg:cs-1" and built["port"] == 41234
    live["port"] = 5999  # host daemon restarted on a new port
    assert built["resolver"]() == 5999


# -- beacon + singleton ------------------------------------------------------------

def test_bridge_forwards_roundtrip_in_beacon(store):
    owner._write_liveness(15.0, active=["cs-1"], bridge_forwards=["cs-1"])
    assert sf.owner_serves_bridge("cs-1")
    assert not sf.owner_serves_bridge("cs-2")


def test_claim_owner_singleton_refuses_a_second_live_owner(store, monkeypatch):
    assert owner.claim_owner_singleton(15.0) is True
    monkeypatch.setattr(owner.os, "getpid", lambda: 999_999)
    monkeypatch.setattr(owner, "_pid_alive", lambda pid: True)
    assert owner.claim_owner_singleton(15.0) is False


async def test_run_owner_daemon_exits_without_touching_a_live_owners_beacon(store, monkeypatch):
    owner._write_liveness(15.0, active=["cs-1"])
    monkeypatch.setattr(owner.os, "getpid", lambda: 999_999)
    monkeypatch.setattr(owner, "_pid_alive", lambda pid: True)

    class _Never:
        async def reconcile(self):
            raise AssertionError("must not run a second owner")

    await owner.run_owner_daemon(_Never(), interval=0)
    assert owner.LIVE_FILE.exists()
    assert owner.read_liveness().active == ("cs-1",)


async def test_unconfirmed_session_survives_a_probe_during_startup(store):
    # The launcher holds before the mux session exists (and before a stopped
    # CodeSpace finishes booting); the probe must not release it then.
    daemons = {}
    forwards = sf.SessionForwards(_daemon_factory(daemons), _probe({"wt-a": False}, []))
    owner_mgr = owner.ConnectionOwner(_relay_factory({}), sessions=forwards)
    owner.hold("cs-1", "cli:a", daemon_port=41234, mux_session="wt-a")
    await owner_mgr.reconcile()
    assert "cli:a" in owner.get_hold("cs-1").tenants
    assert owner_mgr.active_daemon_forwards() == {"cs-1": 41234}


def test_confirmation_is_sticky_across_reholds(store):
    owner.hold("cs-1", "cli:a", mux_session="wt-a", confirmed=True)
    first = owner.get_hold("cs-1").sessions["cli:a"]
    again = owner.hold("cs-1", "cli:a", mux_session="wt-b")
    assert again.sessions["cli:a"] == {
        "mux_session": "wt-b", "expires_at": first["expires_at"],
        "confirmed": True, "generation": first["generation"],
    }


def test_fresh_hold_starts_a_new_unconfirmed_generation_with_a_new_lease(store):
    old = owner.hold("cs-1", "cli:a", mux_session="wt-a", confirmed=True, max_lease=10).sessions["cli:a"]
    new = owner.hold("cs-1", "cli:a", mux_session="wt-a", fresh=True).sessions["cli:a"]
    assert new["confirmed"] is False
    assert new["generation"] != old["generation"]
    assert new["expires_at"] > old["expires_at"]


def test_restore_puts_back_the_exact_prior_entry(store):
    old = dict(owner.hold("cs-1", "cli:a", mux_session="wt-a", confirmed=True).sessions["cli:a"])
    owner.hold("cs-1", "cli:a", mux_session="wt-a", fresh=True)
    back = owner.hold("cs-1", "cli:a", mux_session="wt-a", restore=old).sessions["cli:a"]
    assert back == old


def test_release_with_a_stale_generation_leaves_the_new_launch(store):
    old = owner.hold("cs-1", "cli:a", mux_session="wt-a", confirmed=True).sessions["cli:a"]["generation"]
    owner.hold("cs-1", "cli:a", mux_session="wt-a", fresh=True)
    owner.release("cs-1", "cli:a", generation=old)
    assert "cli:a" in owner.get_hold("cs-1").sessions


async def test_probe_of_an_older_generation_cannot_release_a_relaunch(store):
    # The probe snapshots the holds, then awaits a slow SSH check; a relaunch
    # (fresh generation) lands meanwhile and must survive the stale verdict.
    owner.hold("cs-1", "cli:a", daemon_port=41234, mux_session="wt-a", confirmed=True)

    async def slow_probe(codespace, muxes):
        owner.hold("cs-1", "cli:a", daemon_port=41234, mux_session="wt-a", fresh=True)
        return {m: False for m in muxes}

    forwards = sf.SessionForwards(_daemon_factory({}), slow_probe)
    await forwards.probe(owner.list_holds())
    assert "cli:a" in owner.get_hold("cs-1").sessions
