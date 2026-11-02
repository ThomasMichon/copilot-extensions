"""Tests for the self-watchdog decision loop (agent_bridge.watchdog, #166).

The watchdog force-exits a daemon that is alive but no longer serving so the
kernel frees the singleton lock. The decision loop is exercised here with
injected signals, a fake clock, and a fake terminal action -- no real server,
sockets, or process exit.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from process_telemetry.buffer import BufferedSink

from agent_bridge.watchdog import ServingWatchdog, _probe_host, _watchdog_enabled


@pytest.fixture
def event_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[BufferedSink, threading.Event]]:
    from agent_bridge import install_paths, watchdog

    monkeypatch.setattr(install_paths, "effective_config_dir", lambda: tmp_path)
    monkeypatch.setattr(watchdog, "_diagnostic_writer", None)
    monkeypatch.setattr(watchdog, "_diagnostics_initialized", False)
    writer = watchdog._start_event_writer()
    assert writer is not None
    release = threading.Event()
    try:
        yield writer, release
    finally:
        release.set()
        writer.close()
        assert writer.join(2), "fixture-owned diagnostic writer did not stop"


def _await_callbacks(writer: BufferedSink, count: int) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        stats = writer.stats()
        if stats["delivered"] + stats["sink_failed"] >= count:
            return
        time.sleep(0.005)
    pytest.fail("diagnostic writer did not consume the offered records")


def test_watchdog_offers_probe_and_exit_reason_before_replacement_log_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    event_writer: tuple[BufferedSink, threading.Event],
) -> None:
    from agent_bridge import service_process_cli, watchdog

    writer, _release = event_writer
    events = tmp_path / "agent-bridge-watchdog.log"
    stderr = tmp_path / "agent-bridge-err.log"
    stderr.write_text("incumbent", encoding="utf-8")
    calls = []

    def fail_probe(*args, **kwargs):
        raise TimeoutError("synthetic health probe timed out")

    def spawn(**kwargs) -> None:
        stderr.write_text("replacement", encoding="utf-8")
        calls.append("spawn")

    monkeypatch.setattr(watchdog.urllib.request, "urlopen", fail_probe)
    monkeypatch.setattr(watchdog.os, "_exit", lambda code: calls.append(code))
    monkeypatch.setattr(service_process_cli, "_core", lambda: SimpleNamespace(
        _spawn_watchdog_replacement=spawn,
    ))
    assert watchdog._make_health_probe("127.0.0.1", 1)() is False
    service_process_cli._watchdog_dead("stopped serving for 15s")
    _await_callbacks(writer, 4)
    rows = [json.loads(line) for line in events.read_text().splitlines()]
    assert [row["event"] for row in rows] == [
        "probe-failed", "replacement-requested", "replacement-scheduled", "exit",
    ]
    assert rows[0]["detail"] == "TimeoutError"
    assert rows[-1]["detail"] == "stopped serving for 15s"
    assert all(row["pid"] > 0 and row["time_ns"] > 0 for row in rows)
    assert calls == ["spawn", 70]


def test_unavailable_watchdog_diagnostic_path_does_not_prevent_terminal_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    event_writer: tuple[BufferedSink, threading.Event],
) -> None:
    from agent_bridge import install_paths, service_process_cli, watchdog

    writer, _release = event_writer
    monkeypatch.setattr(install_paths, "effective_config_dir", lambda: tmp_path / "absent")
    calls = []
    monkeypatch.setattr(service_process_cli, "_core", lambda: SimpleNamespace(
        _spawn_watchdog_replacement=lambda **kwargs: calls.append("spawn"),
    ))
    monkeypatch.setattr(watchdog.os, "_exit", lambda code: calls.append(code))
    service_process_cli._watchdog_dead("stopped serving")
    _await_callbacks(writer, 3)
    assert calls == ["spawn", 70]
    assert writer.stats()["sink_failed"] == 3
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("blocked_operation", ["resolve", "open", "write"])
def test_stalled_watchdog_storage_does_not_gate_supervision_or_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    event_writer: tuple[BufferedSink, threading.Event], blocked_operation: str,
) -> None:
    from agent_bridge import install_paths, service_process_cli, watchdog

    writer, release = event_writer
    entered = threading.Event()
    original_open = Path.open
    calls = []

    def stall() -> None:
        entered.set()
        assert release.wait(5), "test did not release stalled storage"

    def resolve() -> Path:
        stall()
        return tmp_path

    class StalledStream:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def write(self, _text: str) -> None:
            stall()

    def open_log(path: Path, *args, **kwargs):
        if path.name == "agent-bridge-watchdog.log":
            if blocked_operation == "open":
                stall()
            elif blocked_operation == "write":
                return StalledStream()
        return original_open(path, *args, **kwargs)

    if blocked_operation == "resolve":
        monkeypatch.setattr(install_paths, "effective_config_dir", resolve)
    else:
        monkeypatch.setattr(Path, "open", open_log)

    def fail_probe(*_args, **_kwargs) -> None:
        raise TimeoutError("synthetic failure")

    monkeypatch.setattr(watchdog.urllib.request, "urlopen", fail_probe)
    monkeypatch.setattr(service_process_cli, "_core", lambda: SimpleNamespace(
        _spawn_watchdog_replacement=lambda **_k: calls.append("spawn"),
    ))
    monkeypatch.setattr(watchdog.os, "_exit", lambda code: calls.append(code))
    probe = watchdog._make_health_probe("127.0.0.1", 1)
    assert probe() is False
    assert entered.wait(2), "writer did not enter storage"

    def forbidden(*_args, **_kwargs) -> None:
        pytest.fail("terminal path performed synchronous logging or stderr flush")

    with monkeypatch.context() as terminal_io:
        terminal_io.setattr(watchdog.log, "error", forbidden)
        terminal_io.setattr(sys, "stderr", SimpleNamespace(flush=forbidden))
        wd, _dead, _clock = _make(
            probe_serving=probe, serving_grace=0,
            on_dead=service_process_cli._watchdog_dead,
        )
        watchdog._run_watchdog(wd, writer)
    assert calls == ["spawn", 70]
    assert not release.is_set()
    assert writer.closed
    assert not writer.join(0)
    assert watchdog._start_event_writer() is writer


def test_full_contended_and_closed_watchdog_writer_remains_lossy(
    monkeypatch: pytest.MonkeyPatch,
    event_writer: tuple[BufferedSink, threading.Event],
) -> None:
    from agent_bridge import install_paths, service_process_cli, watchdog

    writer, release = event_writer
    entered = threading.Event()
    offered = []

    def stalled_root() -> Path:
        entered.set()
        assert release.wait(5)
        raise OSError("synthetic unavailable storage")

    monkeypatch.setattr(install_paths, "effective_config_dir", stalled_root)
    original_sink = writer._sink

    def capture(record: dict) -> None:
        offered.append(record)
        original_sink(record)

    writer._sink = capture
    watchdog._record_event("probe-failed", "x" * 10000)
    assert entered.wait(2)
    assert len(offered[0]["detail"]) == 512
    for _ in range(40):
        watchdog._record_event("probe-failed", "failed")
    assert writer.stats()["full"] > 0
    with writer._lock:
        watchdog._record_event("probe-failed", "contended")
    assert writer.stats()["contended"] == 1
    calls = []
    monkeypatch.setattr(service_process_cli, "_core", lambda: SimpleNamespace(
        _spawn_watchdog_replacement=lambda **_k: calls.append("spawn"),
    ))
    monkeypatch.setattr(watchdog.os, "_exit", lambda code: calls.append(code))
    service_process_cli._watchdog_dead("queue full")
    writer.close()
    watchdog._record_event("exit", "closed")
    assert watchdog._start_event_writer() is writer
    assert writer.stats()["inactive"] == 1
    assert calls == ["spawn", 70]


def test_failed_watchdog_writer_initialization_is_not_retried(monkeypatch) -> None:
    from agent_bridge import service_process_cli, watchdog

    attempts = []
    monkeypatch.setattr(watchdog, "_diagnostic_writer", None)
    monkeypatch.setattr(watchdog, "_diagnostics_initialized", False)

    def unavailable(*_args, **_kwargs) -> None:
        attempts.append("initialize")
        raise RuntimeError("synthetic diagnostic initialization failure")

    monkeypatch.setattr(watchdog, "BufferedSink", unavailable)
    assert watchdog._start_event_writer() is None
    assert watchdog._start_event_writer() is None
    assert attempts == ["initialize"]
    calls = []
    monkeypatch.setattr(service_process_cli, "_core", lambda: SimpleNamespace(
        _spawn_watchdog_replacement=lambda **_k: calls.append("spawn"),
    ))
    monkeypatch.setattr(watchdog.os, "_exit", lambda code: calls.append(code))
    service_process_cli._watchdog_dead("diagnostics unavailable")
    assert calls == ["spawn", 70]


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

    monkeypatch.setenv("AGENT_BRIDGE_WATCHDOG", "1")
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
    monkeypatch.setattr(watchdog, "_start_event_writer", lambda: None)
    monkeypatch.setattr(watchdog, "_force_exit", shutdown_terminal)
    thread = watchdog.arm_serving_watchdog(
        Server(),
        bind="127.0.0.1",
        port=1234,
        is_stopped=lambda: False,
        on_dead=terminal,
    )

    assert thread is not None
    assert captured["target"].args[0]._on_dead is terminal
    assert captured["target"].args[0]._on_shutdown_stuck is shutdown_terminal
