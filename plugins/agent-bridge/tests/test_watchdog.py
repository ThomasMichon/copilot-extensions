"""Tests for the self-watchdog decision loop (agent_bridge.watchdog, #166).

The watchdog force-exits a daemon that is alive but no longer serving so the
kernel frees the singleton lock. The decision loop is exercised here with
injected signals, a fake clock, and a fake terminal action -- no real server,
sockets, or process exit.
"""

from __future__ import annotations

import pytest

from agent_bridge.watchdog import ServingWatchdog, _probe_host, _watchdog_enabled


class _Clock:
    """Deterministic monotonic clock advanced by the injected sleep."""

    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, dt: float) -> None:
        self.t += dt


def _make(**overrides):
    clock = _Clock()
    dead: list[str] = []
    defaults = dict(
        is_started=lambda: True,
        is_shutting_down=lambda: False,
        is_stopped=lambda: False,
        probe_serving=lambda: True,
        on_dead=dead.append,
        interval=15.0,
        startup_grace=120.0,
        serving_grace=60.0,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    defaults.update(overrides)
    return ServingWatchdog(**defaults), dead, clock


def test_serving_ok_then_graceful_shutdown_does_not_fire():
    shutting = {"v": False}
    # Serve fine for a while, then a graceful shutdown is requested.
    calls = {"n": 0}

    def probe() -> bool:
        calls["n"] += 1
        if calls["n"] >= 3:
            shutting["v"] = True
        return True

    wd, dead, _ = _make(
        probe_serving=probe,
        is_shutting_down=lambda: shutting["v"],
        is_stopped=lambda: shutting["v"],
    )
    wd.run()
    assert dead == []  # graceful stop is not a wedge


def test_sustained_serving_failure_fires():
    wd, dead, _ = _make(probe_serving=lambda: False)
    wd.run()
    assert len(dead) == 1
    assert "stopped serving" in dead[0]


def test_single_transient_failure_is_tolerated():
    # One failed probe, then healthy forever -> never fires. Bound the loop by
    # flipping to a graceful shutdown after enough healthy probes.
    seq = iter([False] + [True] * 10)
    shutting = {"v": False}
    healthy_seen = {"n": 0}

    def probe() -> bool:
        try:
            ok = next(seq)
        except StopIteration:
            ok = True
        if ok:
            healthy_seen["n"] += 1
            if healthy_seen["n"] >= 5:
                shutting["v"] = True
        return ok

    wd, dead, _ = _make(
        probe_serving=probe,
        is_shutting_down=lambda: shutting["v"],
        is_stopped=lambda: shutting["v"],
        serving_grace=60.0,
        interval=15.0,
    )
    wd.run()
    assert dead == []  # a lone failure inside the grace window never fires


def test_failure_recovers_before_grace_resets_timer():
    # Fail for < serving_grace, recover, then a graceful stop. Must not fire.
    # interval=15, grace=60 -> 3 consecutive failures (45s) is under grace.
    pattern = [False, False, False, True, True]
    it = iter(pattern)
    shutting = {"v": False}

    def probe() -> bool:
        try:
            return next(it)
        except StopIteration:
            shutting["v"] = True
            return True

    wd, dead, _ = _make(
        probe_serving=probe,
        is_shutting_down=lambda: shutting["v"],
        is_stopped=lambda: shutting["v"],
    )
    wd.run()
    assert dead == []


def test_startup_never_completes_fires():
    wd, dead, _ = _make(is_started=lambda: False)
    wd.run()
    assert len(dead) == 1
    assert "startup did not complete" in dead[0]


def test_startup_graceful_shutdown_before_start_is_clean():
    # Shutdown requested before the server ever started -> clean no-op.
    wd, dead, _ = _make(
        is_started=lambda: False,
        is_shutting_down=lambda: True,
        is_stopped=lambda: True,
    )
    wd.run()
    assert dead == []


def test_stuck_graceful_shutdown_force_exits():
    serving_dead = []
    shutdown_dead = []
    wd, dead, _ = _make(
        is_shutting_down=lambda: True,
        is_stopped=lambda: False,
        shutdown_grace=30.0,
        interval=5.0,
        on_dead=serving_dead.append,
        on_shutdown_stuck=shutdown_dead.append,
    )
    wd.run()
    assert dead == []
    assert serving_dead == []
    assert len(shutdown_dead) == 1
    assert "graceful shutdown did not complete" in shutdown_dead[0]


def test_shutdown_requested_during_failed_probe_does_not_spawn_replacement():
    shutting = {"value": False}
    serving_dead = []
    shutdown_dead = []
    probes = {"count": 0}

    def probe():
        probes["count"] += 1
        if probes["count"] >= 2:
            shutting["value"] = True
        return False

    wd, _dead, _ = _make(
        probe_serving=probe,
        is_shutting_down=lambda: shutting["value"],
        is_stopped=lambda: True,
        on_dead=serving_dead.append,
        on_shutdown_stuck=shutdown_dead.append,
        serving_grace=0,
    )
    wd.run()

    assert serving_dead == []
    assert shutdown_dead == []


def test_startup_completes_late_then_serves():
    # Not started for the first two checks, then started; serving fine; then a
    # graceful stop bounds the loop.
    started = {"v": False}
    checks = {"n": 0}

    def is_started() -> bool:
        checks["n"] += 1
        if checks["n"] >= 2:
            started["v"] = True
        return started["v"]

    shutting = {"v": False}
    served = {"n": 0}

    def probe() -> bool:
        served["n"] += 1
        if served["n"] >= 2:
            shutting["v"] = True
        return True

    wd, dead, _ = _make(
        is_started=is_started,
        probe_serving=probe,
        is_shutting_down=lambda: shutting["v"],
        is_stopped=lambda: shutting["v"],
    )
    wd.run()
    assert dead == []


@pytest.mark.parametrize(
    ("bind", "expected"),
    [
        ("0.0.0.0", "127.0.0.1"),
        ("::", "127.0.0.1"),
        ("", "127.0.0.1"),
        ("127.0.0.1", "127.0.0.1"),
        ("192.168.1.5", "192.168.1.5"),
    ],
)
def test_probe_host_maps_wildcard_to_loopback(bind, expected):
    assert _probe_host(bind) == expected


def test_watchdog_enabled_default_and_disable(monkeypatch):
    monkeypatch.delenv("AGENT_BRIDGE_WATCHDOG", raising=False)
    assert _watchdog_enabled() is True
    for val in ("0", "false", "no", "off", "OFF"):
        monkeypatch.setenv("AGENT_BRIDGE_WATCHDOG", val)
        assert _watchdog_enabled() is False
    monkeypatch.setenv("AGENT_BRIDGE_WATCHDOG", "1")
    assert _watchdog_enabled() is True


def test_arm_uses_supplied_terminal_action(monkeypatch):
    from agent_bridge import watchdog

    captured = {}

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            captured.update(target=target, name=name, daemon=daemon)

        def start(self):
            pass

    class Server:
        started = True
        should_exit = False

    terminal = lambda reason: None
    shutdown_terminal = lambda reason: None
    monkeypatch.setattr(watchdog.threading, "Thread", FakeThread)
    monkeypatch.setattr(watchdog, "_force_exit", shutdown_terminal)
    thread = watchdog.arm_serving_watchdog(
        Server(),
        bind="127.0.0.1",
        port=1234,
        is_stopped=lambda: False,
        on_dead=terminal,
    )

    assert thread is not None
    assert captured["target"].__self__._on_dead is terminal
    assert captured["target"].__self__._on_shutdown_stuck is shutdown_terminal
