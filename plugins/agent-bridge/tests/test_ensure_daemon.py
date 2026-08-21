"""On-demand daemon ensure -- a daemon-touching command self-heals (#1713 Slice 3).

When the daemon is down, ``_get_client()`` boots it first so ``send``/``wait``/
``read``/``agents``/``sessions`` self-heal a crash / idle-exit / missing restart
task -- rather than failing. Pure reporters (``status``) pass ``ensure=False`` and
must never boot what they report on. Guarded by a kill switch, crash-loop
backoff, and single-flight.
"""

from __future__ import annotations

import os

import pytest

from agent_bridge import __main__ as m


@pytest.fixture(autouse=True)
def _fast(monkeypatch, tmp_path):
    # No real sleeping; isolate ensure state files to a temp dir.
    import time as _t

    monkeypatch.setattr(_t, "sleep", lambda *_a: None)
    monkeypatch.setattr(m, "_ENSURE_LOCK", str(tmp_path / ".ensure.lock"))
    monkeypatch.setattr(m, "_ENSURE_MARKER", str(tmp_path / ".ensure-attempt"))
    monkeypatch.delenv("AGENT_BRIDGE_NO_ENSURE", raising=False)


def test_ensure_noop_when_already_running(monkeypatch):
    monkeypatch.setattr(m, "_service_is_running", lambda: True)
    spawned = {"n": 0}
    monkeypatch.setattr(m, "_spawn_detached_daemon", lambda: spawned.__setitem__("n", spawned["n"] + 1))
    assert m._ensure_daemon() is True
    assert spawned["n"] == 0  # never booted a live daemon


def test_ensure_boots_when_down_then_healthy(monkeypatch):
    state = {"up": False}
    monkeypatch.setattr(m, "_service_is_running", lambda: state["up"])

    def _spawn():
        state["up"] = True  # boot succeeds; next probe sees it up

    monkeypatch.setattr(m, "_spawn_detached_daemon", _spawn)
    assert m._ensure_daemon() is True


def test_ensure_kill_switch(monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_NO_ENSURE", "1")
    monkeypatch.setattr(m, "_service_is_running", lambda: False)
    spawned = {"n": 0}
    monkeypatch.setattr(m, "_spawn_detached_daemon", lambda: spawned.__setitem__("n", spawned["n"] + 1))
    assert m._ensure_daemon() is False
    assert spawned["n"] == 0  # kill switch prevents the boot


def test_ensure_crash_loop_backoff(monkeypatch):
    # A boot was attempted just now (fresh marker) and the daemon is still down:
    # ensure must NOT hammer -- no second spawn within the backoff window.
    import time as _t

    with open(m._ENSURE_MARKER, "w") as fh:
        fh.write(str(_t.time()))
    monkeypatch.setattr(m, "_service_is_running", lambda: False)
    spawned = {"n": 0}
    monkeypatch.setattr(m, "_spawn_detached_daemon", lambda: spawned.__setitem__("n", spawned["n"] + 1))
    assert m._ensure_daemon() is False
    assert spawned["n"] == 0


def test_ensure_single_flight_waits_when_locked(monkeypatch):
    # A held (fresh) ensure lock means another invocation is booting -- this one
    # must wait for health, not spawn a second daemon.
    with open(m._ENSURE_LOCK, "w") as fh:
        fh.write("99999")
    calls = {"probe": 0}

    def _running():
        calls["probe"] += 1
        return calls["probe"] > 3  # winner's daemon comes up after a few probes

    monkeypatch.setattr(m, "_service_is_running", _running)
    spawned = {"n": 0}
    monkeypatch.setattr(m, "_spawn_detached_daemon", lambda: spawned.__setitem__("n", spawned["n"] + 1))
    assert m._ensure_daemon() is True
    assert spawned["n"] == 0  # did not boot a second daemon


def test_ensure_breaks_stale_lock(monkeypatch):
    # A lock older than the backoff window is stale -> broken and re-acquired.
    with open(m._ENSURE_LOCK, "w") as fh:
        fh.write("1")
    old = os.path.getmtime(m._ENSURE_LOCK) - (m._ENSURE_BACKOFF_S + 60)
    os.utime(m._ENSURE_LOCK, (old, old))
    fd = m._acquire_ensure_lock()
    assert fd is not None
    m._release_ensure_lock(fd)


def test_get_client_ensure_false_does_not_boot(monkeypatch):
    called = {"ensure": 0}
    monkeypatch.setattr(m, "_ensure_daemon", lambda: called.__setitem__("ensure", called["ensure"] + 1) or True)

    class _FakeClient:
        def assert_client_supported(self):
            return None

    monkeypatch.setattr(m, "_get_client", m._get_client)  # keep real
    import agent_bridge.client as c

    monkeypatch.setattr(c.BridgeClient, "from_config", classmethod(lambda cls: _FakeClient()))
    m._get_client(ensure=False)
    assert called["ensure"] == 0


def test_get_client_default_ensures(monkeypatch):
    called = {"ensure": 0}
    monkeypatch.setattr(m, "_ensure_daemon", lambda: called.__setitem__("ensure", called["ensure"] + 1) or True)

    class _FakeClient:
        def assert_client_supported(self):
            return None

    import agent_bridge.client as c

    monkeypatch.setattr(c.BridgeClient, "from_config", classmethod(lambda cls: _FakeClient()))
    m._get_client()
    assert called["ensure"] == 1
