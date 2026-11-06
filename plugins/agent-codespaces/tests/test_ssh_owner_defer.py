"""Integration tests for the one-off ``agent-codespaces ssh`` Connection Owner
defer (dotfiles#1345, slice 2c).

Drives the SSH handler and CLI parser with external SSH / provisioning seams
mocked so the hold/skip/release and admission wiring is exercised:

- when the Owner is live + enabled, ``_cmd_ssh`` does NOT stand up its own relay
  and places a hold that is released on cleanup; and
- a busy target (``TargetBusyError`` before the run) does NOT leak a hold --
  the hold is placed only *after* the target lock is acquired; and
- failed relay admission stops downstream work while preserving cleanup.

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
        self.released = False

    def acquire(self, force=False):
        if _FakeLock.busy:
            raise ssh_manager.TargetBusyError(
                self.target,
                types.SimpleNamespace(pid=1, op="stdio", age_seconds=1.0),
            )

    def release(self):
        self.released = True


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
        "agent_codespaces.gh_account.credential_account_for_codespace",
        lambda name: None,
    )

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
    monkeypatch.setattr(relay_token, "token_for", lambda name, **kw: "tok")

    manager = _FakeManager()
    lock = _FakeLock("cs-test")
    monkeypatch.setattr(ssh_manager, "ConnectionManager", lambda: manager)
    monkeypatch.setattr(ssh_manager, "TargetLock", lambda *a, **k: lock)

    # Force the live-defer decision without a real daemon; keep hold/release real.
    monkeypatch.setattr(
        owner, "should_defer_to_owner", lambda *a, **k: not k.get("no_relay", False)
    )

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
    return types.SimpleNamespace(calls=calls, manager=manager, lock=lock)


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


@pytest.mark.parametrize("defer_to_owner", [True, False], ids=["owner", "direct"])
@pytest.mark.parametrize("entrypoint", ["command", "cli"])
def test_ssh_refuses_failed_relay_before_downstream_work(
    monkeypatch, harness, capsys, defer_to_owner, entrypoint,
):
    monkeypatch.setattr(owner, "should_defer_to_owner", lambda *a, **k: defer_to_owner)

    async def unavailable(*a, **k):
        return False

    async def downstream(*a, **k):
        pytest.fail("failed relay admission reached downstream work")

    monkeypatch.setattr(owner, "await_owner_relay", unavailable)
    monkeypatch.setattr(m, "_check_cross_harness_fence", downstream)

    if entrypoint == "cli":
        rc = m.main(["ssh", "cs-test", "--remote-cmd", "true"])
    else:
        rc = m._cmd_ssh(_args())

    assert rc == 1
    assert "credential relay" in capsys.readouterr().err.lower()
    assert harness.manager.disconnected == ["cs-test"]
    assert harness.lock.released
    assert owner.list_holds() == []
    assert harness.calls["relay"] == (0 if defer_to_owner else 1)


@pytest.mark.parametrize("mode", ["owner", "direct", "no-relay"])
def test_ssh_preserves_successful_relay_and_explicit_no_relay(
    monkeypatch, harness, mode,
):
    calls = []

    class Relay:
        async def stop(self):
            calls.append("stop")

    async def ready(*a, **k):
        calls.append("start")
        return Relay()

    monkeypatch.setattr(
        owner, "should_defer_to_owner", lambda *a, **k: mode == "owner"
    )
    monkeypatch.setattr(m, "_start_supervised_relay", ready)
    args = _args()
    args.no_relay = mode == "no-relay"

    assert m._cmd_ssh(args) == m._BUSY_EXIT
    assert calls == (["start", "stop"] if mode == "direct" else [])
    assert harness.lock.released
    assert owner.list_holds() == []
