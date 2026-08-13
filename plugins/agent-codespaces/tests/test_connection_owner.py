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
