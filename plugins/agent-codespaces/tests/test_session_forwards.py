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
from agent_codespaces import owner_local_forwards as olf
from agent_codespaces import session_forwards as sf
from agent_codespaces import transcript_mirror as tm


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
                   local_forwards={41909: 41909, 0: 0, "x": 5})
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


async def test_dynamic_local_forward_records_the_assigned_host_port(store):
    created = {}

    def make(codespace, host_port, venue_port):
        channel = FakeChannel((codespace, host_port, venue_port))
        channel.bound_port = 49152
        created[(codespace, host_port, venue_port)] = channel
        return channel

    forwards = sf.SessionForwards(_any_factory({}), local_factory=make)
    owner.hold("cs-1", "cli:t", daemon_port=41234, mux_session="wt-x",
               local_forwards={0: 3000})

    await forwards.reconcile({h.codespace: h for h in owner.list_holds()})

    assert created[("cs-1", 0, 3000)].starts == 1
    assert owner.get_hold("cs-1").local_forwards == {"49152": 3000}
    assert forwards.active_local_forwards() == {"cs-1": {49152: 3000}}
    kept = owner.hold("cs-1", "cli:t", mux_session="wt-x")  # rejoin without the flag
    assert kept.local_forwards == {"49152": 3000}


async def test_dynamic_local_forward_keeps_assigned_port_across_reconnects_and_owner_restart(store):
    ports = iter([49152, 49153])
    forwards = []

    class Forward:
        def __init__(self, cfg, remote_port, *, local_port):
            self.remote_port = remote_port
            self.local_port = int(local_port) if local_port else None
            self.fixed = bool(local_port)
            self.establishes = 0
            self.refreshes = 0
            self.is_alive = False
            forwards.append(self)

        async def establish(self):
            self.establishes += 1
            if self.local_port is None:
                self.local_port = next(ports)
            self.is_alive = True
            return self.local_port

        async def refresh(self):
            self.refreshes += 1
            self.is_alive = True
            return self.local_port

        async def cancel(self):
            self.is_alive = False

    class Source:
        def __init__(self, codespace, gh_env=None):
            self.codespace = codespace

        def get_ssh_config(self):
            return f"cfg:{self.codespace}"

    local_factory = sf.make_local_forward_factory(forward_cls=Forward, config_source_cls=Source)
    forwards_mgr = sf.SessionForwards(_any_factory({}), local_factory=local_factory)
    owner.hold("cs-1", "cli:t", daemon_port=41234, mux_session="wt-x",
               local_forwards={0: 3000})

    await forwards_mgr.reconcile({h.codespace: h for h in owner.list_holds()})
    assert owner.get_hold("cs-1").local_forwards == {"49152": 3000}
    assert forwards_mgr.active_local_forwards() == {"cs-1": {49152: 3000}}

    await forwards_mgr.reconcile({h.codespace: h for h in owner.list_holds()})
    assert len(forwards) == 1 and forwards[0].local_port == 49152

    forwards[0].is_alive = False
    await forwards_mgr.reconcile({h.codespace: h for h in owner.list_holds()})
    assert forwards[0].refreshes == 1
    assert forwards[0].local_port == 49152
    assert forwards_mgr.active_local_forwards() == {"cs-1": {49152: 3000}}

    restarted = sf.SessionForwards(_any_factory({}), local_factory=local_factory)
    await restarted.reconcile({h.codespace: h for h in owner.list_holds()})
    assert len(forwards) == 2
    assert forwards[1].fixed is True
    assert forwards[1].local_port == 49152
    assert restarted.active_local_forwards() == {"cs-1": {49152: 3000}}


async def test_dynamic_local_forward_reassigns_after_repeated_rebind_failures(store, monkeypatch):
    monkeypatch.setattr(sf, "_local_port_in_use", lambda port: port == 49152)

    class Forward:
        def __init__(self, cfg, remote_port, *, local_port):
            self.remote_port = remote_port
            self.local_port = int(local_port) if local_port else None
            self.is_alive = False

        async def establish(self):
            if self.local_port == 49152:
                raise ConnectionError("address already in use")
            if self.local_port is None:
                self.local_port = 49153
            self.is_alive = True
            return self.local_port

        async def refresh(self):
            return await self.establish()

        async def cancel(self):
            self.is_alive = False

    class Source:
        def __init__(self, codespace, gh_env=None):
            pass

        def get_ssh_config(self):
            return object()

    owner.hold("cs-1", "cli:t", daemon_port=41234, mux_session="wt-x",
               local_forwards={49152: 3000})
    h = owner.get_hold("cs-1")
    h.assigned_local_forwards = {"49152": 3000}
    owner._write_holds({"cs-1": h})
    forwards = sf.SessionForwards(
        _any_factory({}),
        local_factory=sf.make_local_forward_factory(forward_cls=Forward, config_source_cls=Source),
    )

    await forwards.reconcile({h.codespace: h for h in owner.list_holds()})
    await forwards.reconcile({h.codespace: h for h in owner.list_holds()})

    assert owner.get_hold("cs-1").local_forwards == {"49153": 3000}
    assert owner.get_hold("cs-1").assigned_local_forwards == {"49153": 3000}
    assert forwards.active_local_forwards() == {"cs-1": {49153: 3000}}


async def test_a_transport_outage_never_reassigns_a_dynamic_local_forward(store, monkeypatch):
    """Failures while the assigned port is still bindable are SSH/venue outages:
    the stable host port is kept and retried, never replaced."""
    monkeypatch.setattr(sf, "_local_port_in_use", lambda port: False)
    calls = []

    def make(codespace, host_port, venue_port):
        calls.append(host_port)
        return FakeChannel((codespace, host_port, venue_port), fail_start=True)  # ssh down

    owner.hold("cs-1", "cli:t", daemon_port=41234, mux_session="wt-x",
               local_forwards={49152: 3000})
    h = owner.get_hold("cs-1")
    h.assigned_local_forwards = {"49152": 3000}
    owner._write_holds({"cs-1": h})
    forwards = sf.SessionForwards(_any_factory({}), local_factory=make)
    for _ in range(sf.LOCAL_REBIND_FAILURES_BEFORE_REASSIGN + 2):
        await forwards.reconcile({h.codespace: h for h in owner.list_holds()})

    assert 0 not in calls  # never asked the kernel for a replacement port
    assert owner.get_hold("cs-1").assigned_local_forwards == {"49152": 3000}


async def test_new_dynamic_local_forward_stops_when_assignment_record_fails(store):
    channels = []

    def make(codespace, host_port, venue_port):
        channel = FakeChannel((codespace, host_port, venue_port))
        channel.bound_port = 49152
        channels.append(channel)
        return channel

    owner.hold("cs-1", "cli:t", daemon_port=41234, mux_session="wt-x",
               local_forwards={0: 3000})
    holds = {h.codespace: h for h in owner.list_holds()}
    owner.release("cs-1", "cli:t")
    forwards = sf.SessionForwards(_any_factory({}), local_factory=make)

    await forwards.reconcile(holds)

    assert channels[0].stops == 1
    assert forwards.active_local_forwards() == {}


async def test_dynamic_local_forward_excludes_fixed_requested_host_ports(store):
    assigned = iter([49152, 49153])
    channels = []

    def make(codespace, host_port, venue_port):
        channel = FakeChannel((codespace, host_port, venue_port))
        if host_port == 0:
            channel.bound_port = next(assigned)
        else:
            channel.bound_port = host_port
        channels.append(channel)
        return channel

    owner.hold("cs-1", "cli:t", daemon_port=41234, mux_session="wt-x",
               local_forwards={0: 3000, 49152: 4000})
    forwards = sf.SessionForwards(_any_factory({}), local_factory=make)

    await forwards.reconcile({h.codespace: h for h in owner.list_holds()})
    assert channels[0].key == ("cs-1", 0, 3000)
    assert channels[0].bound_port == 49152
    assert channels[0].stops == 1
    assert owner.get_hold("cs-1").local_forwards == {"0": 3000, "49152": 4000}
    assert forwards.active_local_forwards() == {"cs-1": {49152: 4000}}

    await forwards.reconcile({h.codespace: h for h in owner.list_holds()})
    assert owner.get_hold("cs-1").local_forwards == {"49152": 4000, "49153": 3000}
    assert owner.get_hold("cs-1").assigned_local_forwards == {"49153": 3000}
    assert forwards.active_local_forwards() == {"cs-1": {49152: 4000, 49153: 3000}}


def test_repeated_dynamic_request_reuses_active_assignment_inside_owner_lock(store):
    owner.hold("cs-1", "cli:t", daemon_port=41234, mux_session="wt-x",
               local_forwards={49152: 3000})
    h = owner.get_hold("cs-1")
    h.assigned_local_forwards = {"49152": 3000}
    owner._write_holds({"cs-1": h})
    olf.write_active_local_forwards({"cs-1": {49152: 3000}})

    kept = owner.hold("cs-1", "cli:t", mux_session="wt-x", local_forwards={0: 3000})

    assert kept.local_forwards == {"49152": 3000}
    assert kept.assigned_local_forwards == {"49152": 3000}


def test_repeated_dynamic_request_reuses_assignment_with_missing_or_stale_beacon(store, monkeypatch):
    owner.hold("cs-1", "cli:t", daemon_port=41234, mux_session="wt-x",
               local_forwards={49152: 3000})
    h = owner.get_hold("cs-1")
    h.assigned_local_forwards = {"49152": 3000}
    owner._write_holds({"cs-1": h})
    monkeypatch.setattr(olf, "read_active_local_forwards", lambda: {})

    kept = owner.hold("cs-1", "cli:t", mux_session="wt-x", local_forwards={0: 3000})

    assert kept.local_forwards == {"49152": 3000}
    assert kept.assigned_local_forwards == {"49152": 3000}


def test_active_local_forward_beacon_requires_live_matching_owner(store):
    owner._write_liveness(15.0)
    olf.write_active_local_forwards({"cs-1": {49152: 3000}})
    assert olf.read_active_local_forwards() == {"cs-1": {49152: 3000}}

    raw = json.loads(olf.ACTIVE_LOCAL_FORWARDS_FILE.read_text(encoding="utf-8"))
    raw["pid"] = raw["pid"] + 1
    olf.ACTIVE_LOCAL_FORWARDS_FILE.write_text(json.dumps(raw), encoding="utf-8")
    assert olf.read_active_local_forwards() == {}


def test_active_local_forward_beacon_rejects_a_dead_owner_pid(store, monkeypatch):
    owner._write_liveness(15.0)
    olf.write_active_local_forwards({"cs-1": {49152: 3000}})
    assert olf.read_active_local_forwards() == {"cs-1": {49152: 3000}}

    monkeypatch.setattr(owner, "_pid_alive", lambda _pid: False)
    assert olf.read_active_local_forwards() == {}


def test_active_local_forward_beacon_ignores_a_provably_dead_owner(store, monkeypatch):
    """A crashed Owner's beacon can stay fresh for minutes; a dead pid still
    means no process owns these forwards."""
    owner._write_liveness(300.0)
    olf.write_active_local_forwards({"cs-1": {49152: 3000}})
    assert olf.read_active_local_forwards() == {"cs-1": {49152: 3000}}

    monkeypatch.setattr(owner, "_pid_alive", lambda pid: False)
    assert olf.read_active_local_forwards() == {}


def test_a_superseded_owner_never_publishes_or_clears_the_live_owners_file(store, monkeypatch):
    owner._write_liveness(15.0)
    olf.write_active_local_forwards({"cs-1": {49152: 3000}})
    successor = json.loads(olf.ACTIVE_LOCAL_FORWARDS_FILE.read_text(encoding="utf-8"))
    successor["pid"] = successor["pid"] + 1
    olf.ACTIVE_LOCAL_FORWARDS_FILE.write_text(json.dumps(successor), encoding="utf-8")
    # The beacon now names another process: this one has been superseded.
    monkeypatch.setattr(owner, "_PROCESS_STARTED_AT", owner._PROCESS_STARTED_AT - 1.0)

    olf.write_active_local_forwards({"cs-1": {50000: 3000}})
    olf.clear_active_local_forwards()

    assert json.loads(olf.ACTIVE_LOCAL_FORWARDS_FILE.read_text(encoding="utf-8")) == successor


def test_active_local_forward_beacon_uses_owner_liveness_threshold(store):
    owner._write_liveness(300.0)
    olf.write_active_local_forwards({"cs-1": {49152: 3000}})
    raw = json.loads(olf.ACTIVE_LOCAL_FORWARDS_FILE.read_text(encoding="utf-8"))
    now = time.time()

    raw["heartbeat_at"] = now - 120.0
    olf.ACTIVE_LOCAL_FORWARDS_FILE.write_text(json.dumps(raw), encoding="utf-8")
    assert olf.read_active_local_forwards(now=now) == {"cs-1": {49152: 3000}}

    raw["heartbeat_at"] = now - 901.0
    olf.ACTIVE_LOCAL_FORWARDS_FILE.write_text(json.dumps(raw), encoding="utf-8")
    assert olf.read_active_local_forwards(now=now) == {}


async def test_dynamic_reassignment_stops_replacement_when_hold_was_released(store, monkeypatch):
    monkeypatch.setattr(sf, "_local_port_in_use", lambda port: port == 49152)
    channels = []

    class Forward:
        def __init__(self, cfg, remote_port, *, local_port):
            self.local_port = int(local_port) if local_port else None
            self.is_alive = False
            self.stops = 0
            channels.append(self)

        async def establish(self):
            if self.local_port == 49152:
                raise ConnectionError("address already in use")
            self.local_port = 49153
            self.is_alive = True
            return self.local_port

        async def refresh(self):
            return await self.establish()

        async def cancel(self):
            self.stops += 1
            self.is_alive = False

    class Source:
        def __init__(self, codespace, gh_env=None):
            pass

        def get_ssh_config(self):
            return object()

    owner.hold("cs-1", "cli:t", daemon_port=41234, mux_session="wt-x",
               local_forwards={49152: 3000})
    h = owner.get_hold("cs-1")
    h.assigned_local_forwards = {"49152": 3000}
    owner._write_holds({"cs-1": h})
    forwards = sf.SessionForwards(
        _any_factory({}),
        local_factory=sf.make_local_forward_factory(forward_cls=Forward, config_source_cls=Source),
    )
    holds = {h.codespace: h for h in owner.list_holds()}

    await forwards.reconcile(holds)
    owner.release("cs-1", "cli:t")
    await forwards.reconcile(holds)  # stale snapshot says the hold still wants 49152

    assert len(channels) == 3
    assert channels[-1].local_port == 49153
    assert channels[-1].stops == 1
    assert forwards.active_local_forwards() == {}


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


async def _await_mirror_tasks(forwards: sf.SessionForwards) -> None:
    for task in list(forwards._mirroring.values()):
        await task


def _dirty_mirror(tmp_path):
    pushes = []
    root = tmp_path / "mirror"
    (root / "cs-1" / "session-state" / "0123abcd").mkdir(parents=True)
    (root / "cs-1" / "session-state" / "0123abcd" / "events.jsonl").write_text(
        "{}\n", encoding="utf-8",
    )
    (root / "cs-1.dirty").touch()

    def push(source, label):
        pushes.append((source, label))
        return True, "pushed"

    return tm.TranscriptMirror(root=root, push=push), pushes


async def test_probe_retries_owed_transcript_push_on_unknown_verdict(store, tmp_path):
    mirror, pushes = _dirty_mirror(tmp_path)
    owner.hold("cs-1", "cli:a", mux_session="wt-a", confirmed=True)
    forwards = sf.SessionForwards(
        _daemon_factory({}), _probe({"wt-a": None}, []), transcript_mirror=mirror,
    )
    await forwards.probe(owner.list_holds())
    await _await_mirror_tasks(forwards)
    assert pushes == [(mirror._root / "cs-1", ".codespaces-live/cs-1")]
    assert not (mirror._root / "cs-1.dirty").exists()


async def test_probe_retries_owed_transcript_push_with_no_holds(store, tmp_path):
    mirror, pushes = _dirty_mirror(tmp_path)
    forwards = sf.SessionForwards(_daemon_factory({}), transcript_mirror=mirror)
    await forwards.probe([])
    await _await_mirror_tasks(forwards)
    assert pushes == [(mirror._root / "cs-1", ".codespaces-live/cs-1")]
    assert not (mirror._root / "cs-1.dirty").exists()


def test_an_owed_push_extends_the_idle_owners_stay_only_while_owed(store, tmp_path):
    mirror, _ = _dirty_mirror(tmp_path)
    forwards = sf.SessionForwards(_daemon_factory({}), transcript_mirror=mirror)
    assert forwards.owed_grace() == sf.OWED_PUSH_GRACE_SECONDS
    (mirror._root / "cs-1.dirty").unlink()
    assert forwards.owed_grace() == 0.0
    assert sf.SessionForwards(_daemon_factory({})).owed_grace() == 0.0


async def test_owed_transcript_push_skips_when_the_codespace_lock_is_held(store, tmp_path):
    from single_instance_lease import SingleInstance

    mirror, pushes = _dirty_mirror(tmp_path)
    other = SingleInstance(mirror._root, service="transcript-mirror", lock_name="cs-1.lock")
    other.acquire()
    try:
        forwards = sf.SessionForwards(_daemon_factory({}), transcript_mirror=mirror)
        await forwards.probe([])
        await _await_mirror_tasks(forwards)
    finally:
        other.release()
    assert pushes == []
    assert (mirror._root / "cs-1.dirty").exists()


async def test_a_running_session_keeps_its_full_passes_while_a_push_is_owed(store, tmp_path):
    """A dirty CodeSpace whose session runs must still be read: owed-only pushes
    stay out of its way (its full passes push what's owed), even when the hub
    keeps failing and every tick runs reconcile() then probe()."""
    root = tmp_path / "mirror"
    root.mkdir()
    (root / "cs-1.dirty").touch()
    ran = []

    class Mirror:
        _root = root

        def owed_codespaces(self):
            return ["cs-1"] if (root / "cs-1.dirty").exists() else []

        async def push_owed(self, codespace):
            ran.append("owed")

        async def __call__(self, codespace):
            ran.append("full")  # a hub that keeps failing: the marker stays

    clock = [0.0]
    owner.hold("cs-1", "cli:a", mux_session="wt-a", confirmed=True)
    forwards = sf.SessionForwards(
        _daemon_factory({}), _probe({"wt-a": True}, []), transcript_mirror=Mirror(),
        clock=lambda: clock[0],
    )
    for _ in range(40):  # 10 minutes of 15-second ticks
        holds = owner.list_holds()
        await forwards.reconcile({h.codespace: h for h in holds})
        await forwards.probe(holds)
        await _await_mirror_tasks(forwards)
        clock[0] += 15.0
    assert ran.count("full") >= 5 and "owed" not in ran[1:]


async def test_a_pass_that_found_its_slot_busy_needs_a_fresh_running_verdict(store, tmp_path):
    """A full pass that couldn't start (slot busy) is not replayed later on the
    old verdict: the next tick probes again, and a session that has stopped by
    then gets no remote read (which could wake the box)."""
    import asyncio as _asyncio

    full = []

    async def mirror(codespace):
        full.append(codespace)

    verdict = {"wt-a": True}

    async def session_probe(codespace, muxes):
        return {m: verdict.get(m) for m in muxes}

    owner.hold("cs-1", "cli:a", mux_session="wt-a", confirmed=True)
    forwards = sf.SessionForwards(_daemon_factory({}), session_probe, transcript_mirror=mirror)
    busy = _asyncio.get_running_loop().create_future()
    forwards._mirroring["cs-1"] = busy  # another task holds the slot
    await forwards.probe(owner.list_holds())
    assert full == []
    busy.set_result(None)
    verdict["wt-a"] = False  # the session stopped meanwhile
    await forwards.probe(owner.list_holds())
    await _await_mirror_tasks(forwards)
    assert full == []
    owner.hold("cs-1", "cli:a", mux_session="wt-a", confirmed=True)
    verdict["wt-a"] = True  # still running at the next probe: now it runs
    forwards._last_probe.clear()
    await forwards.probe(owner.list_holds())
    await _await_mirror_tasks(forwards)
    assert full == ["cs-1"]


# -- SessionForwards: a bridge forward that is up but not serving ------------------

def _bridge_probe(answer, calls):
    async def probe(codespace, port):
        calls.append((codespace, port))
        if isinstance(answer, Exception):
            raise answer
        return answer
    return probe


async def _forward_with(store, *, mux_verdict, bridge_answer):
    daemons, calls = {}, []
    forwards = sf.SessionForwards(
        _daemon_factory(daemons), _probe({"wt-a": mux_verdict}, []),
        bridge_probe=_bridge_probe(bridge_answer, calls),
    )
    owner.hold("cs-1", "cli:a", daemon_port=41234, mux_session="wt-a", confirmed=True)
    await forwards.reconcile({h.codespace: h for h in owner.list_holds()})
    first = daemons[("cs-1", 41234)]
    await forwards.probe(owner.list_holds())
    await forwards.reconcile({h.codespace: h for h in owner.list_holds()})
    return forwards, daemons, first, calls


async def test_a_bridge_forward_that_stopped_serving_is_rebuilt(store):
    forwards, daemons, first, calls = await _forward_with(store, mux_verdict=True, bridge_answer=False)
    assert calls == [("cs-1", 41234)]
    assert first.stops == 1  # its ssh was alive but forwarded nothing
    rebuilt = daemons[("cs-1", 41234)]
    assert rebuilt is not first and rebuilt.is_alive
    assert forwards.active() == {"cs-1": 41234}


async def test_a_serving_or_unknown_bridge_forward_is_left_alone(store):
    for answer in (True, None, RuntimeError("transport")):
        owner.release("cs-1", "cli:a")
        _forwards, daemons, first, _calls = await _forward_with(store, mux_verdict=True, bridge_answer=answer)
        assert first.stops == 0 and daemons[("cs-1", 41234)] is first


async def test_the_bridge_is_only_probed_while_a_session_provably_runs(store):
    # Stopped CodeSpace or unknown session: never connect (it would wake the box).
    for verdict in (False, None):
        owner.release("cs-1", "cli:a")
        _forwards, _daemons, first, calls = await _forward_with(store, mux_verdict=verdict, bridge_answer=False)
        assert calls == []
        # (A gone session releases its tenant, which stops its forward: that's
        # the existing release path, not a rebuild.)
        assert first.stops == (1 if verdict is False else 0)


async def test_remote_bridge_probe_maps_curl_exits(monkeypatch):
    results = iter([0, 7, 28, 22, 99])

    class Manager:
        async def disconnect(self, codespace):
            pass

    async def opener(codespace):
        return Manager()

    async def fake_exec(manager, codespace, cmd, **kw):
        assert "127.0.0.1:41234/api/v1/live-sessions" in cmd
        return types.SimpleNamespace(exit_code=next(results))

    monkeypatch.setattr(sf, "exec_with_retry", fake_exec)
    probe = sf.make_remote_bridge_probe(open_manager=opener)
    got = [await probe("cs-1", 41234) for _ in range(5)]
    # 0 serves; 7/28 don't connect/answer (rebuild); 22 = HTTP refusal (forwarding fine); other = unknown.
    assert got == [True, False, False, None, None]


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


def test_failed_rejoin_restore_puts_back_dynamic_local_forward_provenance(store):
    from agent_codespaces.owner_local_forwards import record_assigned_local_forward

    owner.hold("cs-1", "cli:a", mux_session="wt-a", local_forwards={0: 5000})
    assert record_assigned_local_forward(
        "cs-1", requested_host_port=0, assigned_host_port=41909, venue_port=5000,
    )
    held = owner.get_hold("cs-1")
    old = dict(held.sessions["cli:a"])
    prior_local, prior_assigned = dict(held.local_forwards), dict(held.assigned_local_forwards)
    assert prior_assigned == {"41909": 5000}
    # The rejoin asks for different forwards, which drops the old provenance ...
    owner.hold("cs-1", "cli:a", mux_session="wt-a", fresh=True, local_forwards={6000: 6000})
    assert owner.get_hold("cs-1").assigned_local_forwards == {}
    # ... and its failure rollback must restore it, not just the port map.
    back = owner.hold(
        "cs-1", "cli:a", mux_session="wt-a",
        restore={**old, "assigned_local_forwards": prior_assigned}, local_forwards=prior_local,
    )
    assert back.local_forwards == {"41909": 5000}
    assert back.assigned_local_forwards == {"41909": 5000}
    assert "assigned_local_forwards" not in back.sessions["cli:a"]


def test_a_dynamic_port_already_claimed_as_a_fixed_forward_is_never_persisted(store):
    """A fixed request for the kernel-chosen port can land between the
    reconcile snapshot and the persist; the locked persist re-checks it."""
    from agent_codespaces.owner_local_forwards import (
        record_assigned_local_forward,
        reassign_dynamic_local_forward,
    )

    owner.hold("cs-1", "cli:a", mux_session="wt-a", local_forwards={0: 5000})
    owner.hold("cs-2", "cli:b", mux_session="wt-b", local_forwards={41909: 7000})
    assert not record_assigned_local_forward(
        "cs-1", requested_host_port=0, assigned_host_port=41909, venue_port=5000,
    )
    assert owner.get_hold("cs-1").local_forwards == {"0": 5000}
    assert record_assigned_local_forward(
        "cs-1", requested_host_port=0, assigned_host_port=41910, venue_port=5000,
    )
    assert not reassign_dynamic_local_forward(
        "cs-1", old_host_port=41910, assigned_host_port=41909, venue_port=5000,
    )
    assert owner.get_hold("cs-1").assigned_local_forwards == {"41910": 5000}


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
