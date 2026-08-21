"""Integration tests for the one-off ``agent-codespaces ssh`` Connection Owner
defer (dotfiles#1345, slice 2c).

Drives ``_cmd_ssh`` with the external SSH / provisioning seams mocked so the
hold/skip/release wiring is exercised end-to-end:

- when the Owner is live + enabled, ``_cmd_ssh`` does NOT stand up its own relay
  and places a hold that is released on cleanup; and
- a busy target (``TargetBusyError`` before the run) does NOT leak a hold --
  the hold is placed only *after* the target lock is acquired.

The connect loop returns a fake connection and the cross-harness fence is forced
False so ``_run`` returns right after the relay decision, exercising the cleanup
``finally`` (hold release) without mocking the entire provisioning pipeline.
"""

from __future__ import annotations

import argparse
import types

import pytest
import ssh_manager
from agent_codespaces import __main__ as m
from agent_codespaces import connection_owner as owner


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(owner, "OWNER_FILE", tmp_path / "connection-owner.json")
    monkeypatch.setattr(owner, "_LOCK_FILE", tmp_path / "connection-owner.lock")
    monkeypatch.setattr(owner, "LIVE_FILE", tmp_path / "connection-owner.live.json")
    monkeypatch.setattr(owner, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(owner, "ensure_runtime_dir", lambda: None)
    return tmp_path


class _FakeConn:
    config = types.SimpleNamespace()


class _FakeManager:
    def __init__(self) -> None:
        self.disconnected: list[str] = []

    async def ensure_connected(self, name, source, port_forwards):
        return _FakeConn()

    async def disconnect(self, name):
        self.disconnected.append(name)


class _FakeLock:
    busy = False  # per-test toggle

    def __init__(self, target, op=None):
        self.target = target

    def acquire(self, force=False):
        if _FakeLock.busy:
            raise ssh_manager.TargetBusyError(
                self.target,
                types.SimpleNamespace(pid=1, op="stdio", age_seconds=1.0),
            )

    def release(self):
        pass


def _args(name="cs-test"):
    return argparse.Namespace(
        name=name,
        no_relay=False,
        remote_cmd="true",
        stdio=False,
        force=False,
        force_claim=False,
        no_provision=False,
        timeout=60.0,
    )


@pytest.fixture
def harness(monkeypatch, store):
    monkeypatch.setenv("AGENT_CODESPACES_DISABLE_CLAIM", "1")
    monkeypatch.setattr(m, "CodespaceSource", lambda *a, **k: object())

    import agent_codespaces.lifecycle as lifecycle
    monkeypatch.setattr(lifecycle, "account_for_codespace", lambda name: None)

    monkeypatch.setattr(
        m,
        "load_merged_config",
        lambda *a, **k: types.SimpleNamespace(
            credentials=types.SimpleNamespace(
                ado_host=None, feed_token_env=None, relay_port=3000
            ),
            connection_owner=types.SimpleNamespace(
                enabled=True, reconcile_interval=15.0
            ),
        ),
    )

    import agent_codespaces.relay_launch as relay_launch
    monkeypatch.setattr(relay_launch, "effective_relay_port", lambda config: 3000)
    monkeypatch.setattr(m, "_clear_status_quietly", lambda name: None)

    import agent_codespaces.coordination as coordination
    monkeypatch.setattr(coordination, "owner_ref", lambda **k: None)

    monkeypatch.setattr(m, "_relay_listening", lambda port, timeout=0.5: True)

    import agent_codespaces.relay_token as relay_token
    monkeypatch.setattr(relay_token, "token_for", lambda name: "tok")

    monkeypatch.setattr(ssh_manager, "ConnectionManager", _FakeManager)
    monkeypatch.setattr(ssh_manager, "TargetLock", _FakeLock)

    # Force the live-defer decision without a real daemon; keep hold/release real.
    monkeypatch.setattr(owner, "should_defer_to_owner", lambda *a, **k: True)

    async def _served(name, **k):
        return True

    monkeypatch.setattr(owner, "await_owner_relay", _served)

    calls = {"relay": 0}

    async def _relay_spy(*a, **k):
        calls["relay"] += 1
        return None

    monkeypatch.setattr(m, "_start_supervised_relay", _relay_spy)

    async def _fence(*a, **k):
        return False  # early clean return after the relay decision

    monkeypatch.setattr(m, "_check_cross_harness_fence", _fence)

    _FakeLock.busy = False
    return types.SimpleNamespace(calls=calls)


def test_ssh_defers_and_releases_hold(monkeypatch, harness):
    released = {"n": 0}
    real_release = owner.release

    def _rel(name, tenant=None, **k):
        released["n"] += 1
        return real_release(name, tenant, **k)

    monkeypatch.setattr(owner, "release", _rel)

    m._cmd_ssh(_args())

    # Deferred to the Owner -> never stood up its own relay.
    assert harness.calls["relay"] == 0
    # Hold was placed then released on cleanup -> registry empty, release called.
    assert released["n"] >= 1
    assert owner.list_holds() == []


def test_ssh_busy_target_does_not_leak_hold(harness):
    _FakeLock.busy = True
    rc = m._cmd_ssh(_args())
    assert rc == m._BUSY_EXIT
    # The hold is placed only AFTER the lock is acquired, so a busy reject leaks
    # nothing and never stands up a relay.
    assert owner.list_holds() == []
    assert harness.calls["relay"] == 0
