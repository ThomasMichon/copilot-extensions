from __future__ import annotations

import asyncio

from ssh_manager import forward_keeper as fk


def test_keeper_store_state_and_stop(tmp_path, monkeypatch):
    store = fk.KeeperStore(tmp_path)
    identities = {123: "keep-123", 456: "relay-456"}
    killed = []
    monkeypatch.setattr(fk, "process_identity", lambda pid: identities.get(pid))
    monkeypatch.setattr(fk, "_terminate_pid", lambda pid: killed.append(pid))

    store.write(
        "target:one",
        {
            "pid": 123,
            "pid_identity": "keep-123",
            "mux": "wt-x",
            "children": [{"pid": 456, "identity": "relay-456"}],
        },
    )

    assert store.state_path("target:one").name == "target-one.json"
    assert store.alive("target:one") is True
    assert store.stop("target:one") is True
    assert killed == [123, 456]
    assert store.read("target:one") is None


def test_keeper_store_stops_legacy_parent_record_without_identity(tmp_path, monkeypatch):
    store = fk.KeeperStore(tmp_path)
    monkeypatch.setattr(fk, "pid_alive", lambda pid: pid == 123)
    killed = []
    monkeypatch.setattr(fk, "_terminate_pid", lambda pid: killed.append(pid))

    store.write("target:one", {"pid": 123, "mux": "wt-x"})

    assert store.alive("target:one") is True
    assert store.stop("target:one") is True
    assert killed == [123]


def test_keeper_store_ignores_reused_pid_without_identity_match(tmp_path, monkeypatch):
    store = fk.KeeperStore(tmp_path)
    monkeypatch.setattr(fk, "process_identity", lambda pid: "someone-else")
    monkeypatch.setattr(fk, "_terminate_pid", lambda pid: (_ for _ in ()).throw(AssertionError("should not kill")))

    store.write("target:one", {"pid": 123, "pid_identity": "keep-123", "mux": "wt-x"})

    assert store.alive("target:one") is False
    assert store.stop("target:one") is False


def test_keeper_store_ignores_bad_json(tmp_path):
    store = fk.KeeperStore(tmp_path)
    store.state_path("x").parent.mkdir(parents=True, exist_ok=True)
    store.state_path("x").write_text("{ nope", encoding="utf-8")
    assert store.read("x") is None
    assert store.alive("x") is False
    assert store.stop("x") is False


def test_spawn_keeper_detaches_and_returns_state(monkeypatch):
    seen = {}
    monkeypatch.setattr(fk, "process_identity", lambda pid: f"proc-{pid}")

    class Proc:
        pid = 456

    def popen(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return Proc()

    state = fk.spawn_keeper(
        ["python", "-m", "x"],
        {"A": "B"},
        {"key": "target"},
        popen=popen,
        popen_kwargs={"creationflags": 99},
    )

    assert state["pid"] == 456 and state["key"] == "target"
    assert state["pid_identity"] == "proc-456"
    assert state["started_at"] > 0
    assert seen["argv"] == ["python", "-m", "x"]
    assert seen["kwargs"]["stdin"] is not None
    assert seen["kwargs"]["creationflags"] == 99


def test_keeper_store_reaps_child_pids_when_keeper_pid_is_gone(tmp_path, monkeypatch):
    store = fk.KeeperStore(tmp_path)
    killed = []
    monkeypatch.setattr(fk, "process_identity", lambda pid: {456: "relay-456"}.get(pid))
    monkeypatch.setattr(fk, "_terminate_pid", lambda pid: killed.append(pid))

    store.write(
        "target:one",
        {
            "pid": 123,
            "pid_identity": "keep-123",
            "children": [{"pid": 456, "identity": "relay-456"}],
        },
    )

    assert store.stop("target:one") is True
    assert killed == [456]


def test_terminate_pid_reaps_posix_process_group(monkeypatch):
    calls = []
    alive = iter([True, False])

    monkeypatch.setattr(fk.sys, "platform", "linux")
    monkeypatch.setattr(fk, "pid_alive", lambda pid: next(alive))
    monkeypatch.setattr(fk.os, "getpgid", lambda pid: 123)
    monkeypatch.setattr(fk.os, "killpg", lambda pgid, sig: calls.append((pgid, sig)))
    monkeypatch.setattr(fk.time, "sleep", lambda delay: None)
    monkeypatch.setattr(fk.time, "monotonic", lambda: 0.0)

    fk._terminate_pid(123)

    assert calls and calls[0][0] == 123


def test_run_supervised_loop_waits_for_startup_then_exits_when_session_gone(monkeypatch):
    events = []
    alive = iter([False, True, True, False])

    class Forward:
        async def start(self):
            events.append("start")

        async def stop(self):
            events.append("stop")

    async def fake_sleep(delay):
        events.append(("sleep", delay))

    monkeypatch.setattr(fk.asyncio, "sleep", fake_sleep)
    rc = asyncio.run(fk.run_supervised_loop(
        [Forward()],
        session_alive=lambda: next(alive),
        write_state=lambda: events.append("write"),
        remove_state=lambda: events.append("remove"),
        probe_interval=10.0,
        startup_grace=60.0,
    ))

    assert rc == 0
    assert events[0:3] == ["write", "start", "write"]
    assert "stop" in events and events[-1] == "remove"
    assert ("sleep", 5.0) in events
    assert ("sleep", 10.0) in events


def test_run_supervised_loop_exits_after_startup_grace(monkeypatch):
    events = []

    class Loop:
        now = 0.0

        def time(self):
            self.now += 10.0
            return self.now

    loop = Loop()

    class Forward:
        async def start(self):
            events.append("start")

        async def stop(self):
            events.append("stop")

    async def fake_sleep(delay):
        events.append(("sleep", delay))

    monkeypatch.setattr(fk.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(fk.asyncio, "get_running_loop", lambda: loop)

    rc = asyncio.run(fk.run_supervised_loop(
        [Forward()],
        session_alive=lambda: False,
        write_state=lambda: events.append("write"),
        remove_state=lambda: events.append("remove"),
        probe_interval=10.0,
        startup_grace=5.0,
    ))

    assert rc == 0
    assert events == ["write", "start", "write", "stop", "remove"]
