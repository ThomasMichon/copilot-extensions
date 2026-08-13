"""Tests for the Connection Owner registry (state redirected to tmp).

Covers increment 1 (dotfiles#1333): the ref-count / pin lifecycle only -- no
connection is opened. Verifies hold/heartbeat/release, ``should_hold``
semantics, TTL-based tenant reclamation, pin stickiness, and persistence.
"""

from __future__ import annotations

import time

import pytest
from agent_codespaces import connection_owner as owner


@pytest.fixture
def store(monkeypatch, tmp_path):
    """Redirect Connection Owner state to a tmp dir so tests never touch real state."""
    monkeypatch.setattr(owner, "OWNER_FILE", tmp_path / "connection-owner.json")
    monkeypatch.setattr(owner, "_LOCK_FILE", tmp_path / "connection-owner.lock")
    monkeypatch.setattr(owner, "RUNTIME_DIR", tmp_path)
    # ensure_runtime_dir() targets the real RUNTIME_DIR; stub it to a no-op.
    monkeypatch.setattr(owner, "ensure_runtime_dir", lambda: None)
    return tmp_path


def test_hold_creates_and_refcounts(store):
    h = owner.hold("cs-one", "tenant-a")
    assert h.codespace == "cs-one"
    assert "tenant-a" in h.tenants
    assert h.should_hold()
    assert owner.should_hold("cs-one")

    # A second tenant increments the ref-count.
    h = owner.hold("cs-one", "tenant-b")
    assert set(h.tenants) == {"tenant-a", "tenant-b"}


def test_hold_same_tenant_is_idempotent(store):
    first = owner.hold("cs-one", "tenant-a")
    created = first.created_at
    time.sleep(0.01)
    again = owner.hold("cs-one", "tenant-a")
    assert set(again.tenants) == {"tenant-a"}
    assert again.created_at == created  # preserved
    assert again.tenants["tenant-a"] >= first.tenants["tenant-a"]  # refreshed


def test_release_last_tenant_removes_hold(store):
    owner.hold("cs-one", "tenant-a")
    owner.hold("cs-one", "tenant-b")

    still = owner.release("cs-one", "tenant-a")
    assert still is not None
    assert set(still.tenants) == {"tenant-b"}

    gone = owner.release("cs-one", "tenant-b")
    assert gone is None  # no tenants, not pinned -> hold removed
    assert owner.get_hold("cs-one") is None
    assert not owner.should_hold("cs-one")


def test_pin_keeps_hold_without_tenants(store):
    owner.hold("cs-one", "tenant-a", pin=True)
    # Dropping the only tenant leaves a pinned hold in place.
    h = owner.release("cs-one", "tenant-a")
    assert h is not None
    assert h.pinned
    assert h.tenants == {}
    assert owner.should_hold("cs-one")

    # Unpinning with no tenants removes the hold.
    gone = owner.release("cs-one", unpin=True)
    assert gone is None
    assert owner.get_hold("cs-one") is None


def test_stale_tenant_reclaimed_by_ttl(store):
    owner.hold("cs-one", "tenant-a")
    # With a zero TTL every tenant is immediately stale.
    time.sleep(0.01)
    assert not owner.should_hold("cs-one", ttl=0.0)
    # get_hold prunes and drops the now-empty hold.
    assert owner.get_hold("cs-one", ttl=0.0) is None


def test_pinned_hold_survives_ttl(store):
    owner.hold("cs-one", "tenant-a", pin=True)
    time.sleep(0.01)
    # Tenants are stale under ttl=0, but the pin keeps the hold alive.
    h = owner.get_hold("cs-one", ttl=0.0)
    assert h is not None
    assert h.pinned
    assert h.tenants == {}  # stale tenant pruned
    assert owner.should_hold("cs-one", ttl=0.0)


def test_heartbeat_refreshes_but_does_not_create(store):
    assert owner.heartbeat("cs-missing", "tenant-a") is None

    owner.hold("cs-one", "tenant-a")
    time.sleep(0.01)
    h = owner.heartbeat("cs-one", "tenant-a")
    assert h is not None
    # Heartbeating an unknown tenant on an existing hold does not add it.
    h = owner.heartbeat("cs-one", "ghost")
    assert h is not None
    assert "ghost" not in h.tenants


def test_persistence_across_reads(store):
    owner.hold("cs-one", "tenant-a", pin=True)
    owner.hold("cs-two", "tenant-b")
    names = {h.codespace for h in owner.list_holds()}
    assert names == {"cs-one", "cs-two"}
    # A fresh read (new process would re-read the file) sees the same state.
    assert owner.get_hold("cs-one").pinned
    assert set(owner.get_hold("cs-two").tenants) == {"tenant-b"}


def test_release_unknown_is_noop(store):
    assert owner.release("cs-missing", "tenant-a") is None
    owner.hold("cs-one", "tenant-a")
    # Releasing a tenant that isn't held leaves the hold intact.
    h = owner.release("cs-one", "not-a-tenant")
    assert h is not None
    assert set(h.tenants) == {"tenant-a"}


def test_corrupt_store_shapes_are_tolerated(store):
    owner_file = store / "connection-owner.json"

    # A non-object top level -> treated as empty (no crash).
    owner_file.write_text("[]", encoding="utf-8")
    assert owner.list_holds() == []

    # A non-numeric tenant heartbeat is dropped on load (so should_hold can't
    # crash on ``now - hb``); a non-dict record is skipped entirely. The record
    # is pinned so it survives prune and we can inspect the sanitized tenants.
    owner_file.write_text(
        '{"cs-one": {"codespace": "cs-one", "host": "h", "created_at": 1.0, '
        '"heartbeat_at": 1.0, "pinned": true, "tenants": {"t": "oops"}}, '
        '"cs-bad": ["not", "a", "record"]}',
        encoding="utf-8",
    )
    h = owner.get_hold("cs-one")
    assert h is not None
    assert h.pinned
    assert h.tenants == {}  # non-numeric heartbeat dropped
    assert owner.get_hold("cs-bad") is None


def test_forward_compat_extra_keys_tolerated(store):
    owner_file = store / "connection-owner.json"
    # A record written by a newer version with an unknown field must still load
    # (not be silently dropped), and codespace is forced to the map key.
    owner_file.write_text(
        '{"cs-one": {"codespace": "stale-name", "host": "h", "created_at": 1.0, '
        '"heartbeat_at": 1.0, "pinned": true, "tenants": {}, '
        '"future_field": "whatever"}}',
        encoding="utf-8",
    )
    h = owner.get_hold("cs-one")
    assert h is not None
    assert h.codespace == "cs-one"  # forced to the map key, not "stale-name"
    assert h.pinned


def test_list_holds_persists_tenant_pruning(store):
    owner.hold("cs-one", "tenant-a", pin=True)  # pinned so the hold survives
    # ttl=0 makes the tenant stale; list_holds must persist the pruned tenants.
    time.sleep(0.01)
    owner.list_holds(ttl=0.0)
    import json

    on_disk = json.loads((store / "connection-owner.json").read_text(encoding="utf-8"))
    assert on_disk["cs-one"]["tenants"] == {}  # stale tenant written out
    assert on_disk["cs-one"]["pinned"] is True


# ---------------------------------------------------------------------------
# Reconciler (increment 2) -- async, fake relay transport
# ---------------------------------------------------------------------------


class FakeRelay:
    """A stand-in RelayChannel that records start/stop without any SSH."""

    def __init__(self, codespace: str, *, fail_start: bool = False) -> None:
        self.codespace = codespace
        self.fail_start = fail_start
        self.starts = 0
        self.stops = 0
        self._alive = False

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


def _factory(created: dict[str, FakeRelay], *, fail: set[str] | None = None):
    fail = fail or set()

    def make(codespace: str) -> FakeRelay:
        relay = FakeRelay(codespace, fail_start=codespace in fail)
        created[codespace] = relay
        return relay

    return make


async def test_reconcile_starts_held_and_stops_unheld(store):
    created: dict[str, FakeRelay] = {}
    owner_mgr = owner.ConnectionOwner(_factory(created))

    owner.hold("cs-one", "tenant-a", pin=True)
    await owner_mgr.reconcile()
    assert owner_mgr.active_codespaces() == {"cs-one"}
    assert created["cs-one"].is_alive()
    assert created["cs-one"].starts == 1

    # A second reconcile is idempotent -- no extra start.
    await owner_mgr.reconcile()
    assert created["cs-one"].starts == 1

    # Fully release the hold (drop the tenant *and* unpin) -> reconcile tears down.
    owner.release("cs-one", "tenant-a", unpin=True)
    await owner_mgr.reconcile()
    assert owner_mgr.active_codespaces() == set()
    assert created["cs-one"].stops == 1


async def test_ensure_returns_false_when_not_held(store):
    created: dict[str, FakeRelay] = {}
    owner_mgr = owner.ConnectionOwner(_factory(created))
    assert await owner_mgr.ensure("cs-missing") is False
    assert owner_mgr.active_codespaces() == set()
    assert created == {}


async def test_reconcile_survives_a_failing_channel(store):
    created: dict[str, FakeRelay] = {}
    owner_mgr = owner.ConnectionOwner(_factory(created, fail={"cs-bad"}))

    owner.hold("cs-bad", "t", pin=True)
    owner.hold("cs-good", "t", pin=True)
    await owner_mgr.reconcile()

    # The good channel is up; the bad one is dropped (to retry next cycle).
    assert "cs-good" in owner_mgr.active_codespaces()
    assert created["cs-good"].is_alive()
    assert "cs-bad" not in owner_mgr.active_codespaces()
    assert created["cs-bad"].starts == 1
    assert created["cs-bad"].stops == 1  # best-effort teardown, no leaked process


async def test_shutdown_stops_all_without_touching_registry(store):
    created: dict[str, FakeRelay] = {}
    owner_mgr = owner.ConnectionOwner(_factory(created))
    owner.hold("cs-one", "t", pin=True)
    owner.hold("cs-two", "t", pin=True)
    await owner_mgr.reconcile()
    assert owner_mgr.active_codespaces() == {"cs-one", "cs-two"}

    await owner_mgr.shutdown()
    assert owner_mgr.active_codespaces() == set()
    assert created["cs-one"].stops == 1
    assert created["cs-two"].stops == 1
    # Registry intent is untouched by a transport shutdown.
    assert {h.codespace for h in owner.list_holds()} == {"cs-one", "cs-two"}


# ---------------------------------------------------------------------------
# Real factory + daemon runner (increment 3)
# ---------------------------------------------------------------------------
import asyncio


def test_make_supervised_relay_factory_wires_config_and_port():
    built = {}

    class FakeRelay:
        def __init__(self, ssh_config, relay_port, *, host_port_resolver=None):
            built.update(
                ssh_config=ssh_config, relay_port=relay_port, resolver=host_port_resolver
            )

        def is_alive(self):
            return False

        async def start(self):
            pass

        async def stop(self):
            pass

    class FakeConfigSource:
        def __init__(self, name, gh_env=None):
            self.name = name
            built["gh_env"] = gh_env

        def get_ssh_config(self):
            return f"sshcfg:{self.name}"

    factory = owner.make_supervised_relay_factory(
        config="CFG",
        gh_env={"GH_TOKEN": "x"},
        relay_cls=FakeRelay,
        config_source_cls=FakeConfigSource,
        port_resolver=lambda cfg: 4321 if cfg == "CFG" else 0,
    )
    channel = factory("cs-x")
    assert isinstance(channel, FakeRelay)
    assert built["ssh_config"] == "sshcfg:cs-x"
    assert built["relay_port"] == 4321
    assert built["gh_env"] == {"GH_TOKEN": "x"}
    # host_port_resolver re-resolves the (possibly drifted) host port on demand.
    assert built["resolver"]() == 4321


class _FakeOwner:
    """Minimal ConnectionOwner stand-in for daemon-loop tests."""

    def __init__(self, *, stop_after: int, fail_first: bool = False):
        self.reconciles = 0
        self.shutdowns = 0
        self.stop_after = stop_after
        self.fail_first = fail_first
        self.stop_event = asyncio.Event()

    async def reconcile(self):
        self.reconciles += 1
        if self.fail_first and self.reconciles == 1:
            raise RuntimeError("cycle boom")
        if self.reconciles >= self.stop_after:
            self.stop_event.set()

    async def shutdown(self):
        self.shutdowns += 1


async def test_run_owner_daemon_reconciles_until_stopped():
    fake = _FakeOwner(stop_after=3)
    await owner.run_owner_daemon(fake, interval=0, stop_event=fake.stop_event)
    assert fake.reconciles >= 3
    assert fake.shutdowns == 1  # channels stopped on exit


async def test_run_owner_daemon_survives_a_failing_cycle():
    fake = _FakeOwner(stop_after=2, fail_first=True)
    # First reconcile raises; the loop logs and continues to the second, which stops.
    await owner.run_owner_daemon(fake, interval=0, stop_event=fake.stop_event)
    assert fake.reconciles >= 2
    assert fake.shutdowns == 1
