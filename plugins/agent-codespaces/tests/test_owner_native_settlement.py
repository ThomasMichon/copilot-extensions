"""Offline native-I/O witnesses for the Connection Owner's retirement boundary."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest
from agent_procutil import no_window_kwargs
from agent_procutil.daemon_instances import daemon_test_lifecycle, inspect_instances
from single_instance_lease import AlreadyRunningError, SingleInstance
from ssh_manager import SupervisedRelayForward
from ssh_manager.config_sources import SSHConfig
from ssh_manager.forward import LocalForward
from ssh_manager.process import register_process_cleanup

from agent_codespaces import connection_owner as co
from agent_codespaces import owner_beacon as ob
from agent_codespaces import session_forwards as sf
from agent_codespaces import transcript_mirror as tm
from agent_codespaces.owner_availability import AvailabilityGate


class NativeIO:
    """A real bounded child blocked on stdin, with caller-owned release/reaping."""

    def __init__(self):
        self.entered = threading.Event()
        self.done = threading.Event()
        self.child = None

    def __call__(self):
        self.child = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdin.buffer.read(1)"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **no_window_kwargs(),
        )
        self.entered.set()
        try:
            self.child.wait(timeout=20)
        finally:
            if self.child.poll() is None:
                self.child.kill()
                self.child.wait(timeout=3)
            self.done.set()

    def release(self):
        if (self.child is not None and self.child.poll() is None
                and not self.child.stdin.closed):
            try:
                self.child.stdin.write(b"x")
                self.child.stdin.close()
            except (BrokenPipeError, OSError):
                pass


@pytest.fixture
def registry(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    for name, filename in (
        ("OWNER_FILE", "connection-owner.json"),
        ("_LOCK_FILE", "connection-owner.lock"),
        ("LIVE_FILE", "connection-owner.live.json"),
    ):
        monkeypatch.setattr(co, name, runtime / filename)
    monkeypatch.setattr(co, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(co, "ensure_runtime_dir", lambda: None)
    root = tmp_path / "instances"
    with daemon_test_lifecycle(owner="offline-owner-settlement", registry_root=root) as environment:
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        yield root


async def until(predicate, timeout=5):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "native witness did not reach its seam"
        await asyncio.sleep(0.01)


def record_state(registry):
    records = inspect_instances(registry)
    assert len(records) == 1
    return records[0]["state"]


async def repeatedly_cancel(task):
    for _ in range(3):
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()


async def finish(task, native):
    native.release()
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 8)
    await until(native.done.is_set)
    assert native.done.is_set()
    assert native.child.poll() is not None


def config_source(native, fail=False):
    class Source:
        def __init__(self, *_args, **_kwargs):
            pass

        def get_ssh_config(self):
            native()
            if fail:
                raise RuntimeError("offline constructor failed")
            return SSHConfig(host_alias="offline-fixture")

    return Source


@pytest.mark.parametrize("fail", [False, True])
async def test_actual_daemon_retains_native_factory_and_late_resource(registry, fail):
    native = NativeIO()
    created = []

    class Relay(SupervisedRelayForward):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    factory = co.make_supervised_relay_factory(
        object(), relay_cls=Relay, config_source_cls=config_source(native, fail),
        port_resolver=lambda _: 43127,
    )
    owner = co.ConnectionOwner(factory)
    co.hold("offline-fixture", "offline-tenant", pin=True)
    task = asyncio.create_task(co.run_owner_daemon(owner, interval=0.05))
    try:
        await until(native.entered.is_set)
        await repeatedly_cancel(task)
        assert record_state(registry) == "running"
        assert native.child.poll() is None
    finally:
        await finish(task, native)
    assert task.cancelled()
    assert record_state(registry) == "finished"
    assert co.should_hold("offline-fixture")
    assert not owner._channels
    if not fail:
        assert len(created) == 1
        assert created[0]._stopped
        assert created[0]._proc is None
        assert created[0]._monitor_task is None  # cleanup never started a transport


@pytest.mark.parametrize("kind", ["bridge", "reverse", "local", "replacement"])
async def test_session_factory_publishes_late_channel_before_cancellation(registry, kind):
    native = NativeIO()
    source = config_source(native)
    daemon_factory = sf.make_supervised_daemon_forward_factory(
        config_source_cls=source, port_resolver=lambda: 43127,
    )
    local_factory = sf.make_local_forward_factory(config_source_cls=source)
    forwards = sf.SessionForwards(daemon_factory, local_factory=local_factory)
    hold = SimpleNamespace(
        daemon_port=43128 if kind == "bridge" else None,
        reverse_forwards={43128: 43127} if kind == "reverse" else {},
        local_forwards={43129: 43130} if kind == "local" else {},
    )
    if kind == "replacement":
        old = sf._LocalForwardChannel(LocalForward(SSHConfig("offline-fixture"), 43130))
        operation = forwards._reassign_dynamic_local_forward(
            ("offline-fixture", 43129), 43130, old, set(),
        )
    else:
        operation = forwards.reconcile({"offline-fixture": hold})
    task = asyncio.create_task(operation)
    try:
        await until(native.entered.is_set)
        await repeatedly_cancel(task)
    finally:
        await finish(task, native)
    assert task.cancelled()
    channels = [
        channel for table in (forwards._channels, forwards._extra, forwards._local)
        for _, channel in table.values()
    ] + forwards._retiring
    assert len(channels) == 1
    channel = channels[0]
    transport = getattr(channel, "_forward", channel)
    assert transport._proc is None
    await forwards.shutdown()
    assert not forwards._channels and not forwards._extra and not forwards._local
    assert not forwards._retiring


@pytest.mark.parametrize("kind", ["availability", "mux", "probe-source"])
async def test_cancelled_native_listing_or_source_is_settled(registry, monkeypatch, kind):
    native = NativeIO()
    connected = []

    def listing():
        native()
        return []

    if kind == "availability":
        operation = AvailabilityGate(listing).stopped(["offline-fixture"])
    elif kind == "mux":
        operation = sf.make_remote_mux_probe(list_codespaces=listing)("offline-fixture", ["mux"])
    else:
        import ssh_manager
        from agent_codespaces import codespace_config, lifecycle

        class Manager:
            async def ensure_connected(self, *args):
                connected.append(args)

        def source(*args, **kwargs):
            native()
            return object()

        monkeypatch.setattr(ssh_manager, "ConnectionManager", Manager)
        monkeypatch.setattr(lifecycle, "account_for_codespace", lambda _: "offline-account")
        monkeypatch.setattr(codespace_config, "CodespaceSource", source)
        operation = sf._open_codespace("offline-fixture")
    task = asyncio.create_task(operation)
    try:
        await until(native.entered.is_set)
        await repeatedly_cancel(task)
    finally:
        await finish(task, native)
    assert task.cancelled()
    assert not connected


@pytest.mark.parametrize("mode", ["ordinary", "repeated-cancel"])
async def test_actual_daemon_settles_mirror_slot_lock_and_owed_push(registry, tmp_path, mode):
    native = NativeIO()
    root = tmp_path / "mirror"
    root.mkdir()
    mirror = tm.TranscriptMirror(root=root, push=lambda *_: (native(), (False, "unknown"))[1])
    sid = "0123abcd-4567-89ef-0123-456789abcdef"
    mirror.apply("offline-fixture", f"{tm._HEAD}{sid} 0 3 0\ne30K\n")
    (root / "offline-fixture.dirty").touch()
    (root / "offline-fixture.prune").touch()
    forwards = sf.SessionForwards(lambda *_: None, transcript_mirror=mirror)
    owner = co.ConnectionOwner(lambda _: None, sessions=forwards)
    stop = asyncio.Event()
    task = asyncio.create_task(co.run_owner_daemon(owner, interval=0.05, stop_event=stop))
    try:
        await until(native.entered.is_set)
        stop.set()
        await asyncio.sleep(0.05)
        if mode == "repeated-cancel":
            await repeatedly_cancel(task)
        assert not task.done()
        assert record_state(registry) == "running"
        assert "offline-fixture" in forwards._mirroring
        probe = SingleInstance(root, service="transcript-mirror", lock_name="offline-fixture.lock")
        with pytest.raises(AlreadyRunningError):
            probe.acquire()
    finally:
        await finish(task, native)
    assert record_state(registry) == "finished"
    assert not forwards._mirroring
    assert mirror.owed_codespaces() == ["offline-fixture"]
    assert (root / "offline-fixture.prune").exists()
    assert (root / "offline-fixture" / "session-state" / sid / "events.jsonl").read_bytes() == b"{}\n"
    probe.acquire()
    probe.release()


async def test_actual_mirror_append_outcome_remains_owned_until_push_finishes(registry, tmp_path):
    native = NativeIO()
    mirror = tm.TranscriptMirror(root=tmp_path / "mirror",
                                 push=lambda *_: (native(), (False, "unknown"))[1])

    async def read(_):
        return f"{tm._HEAD}0123abcd-4567-89ef-0123-456789abcdef 0 3 0\ne30K\n{tm._DONE}\n"

    mirror._read = read
    forwards = sf.SessionForwards(lambda *_: None, transcript_mirror=mirror)
    assert forwards._start_mirror("offline-fixture")
    task = None
    try:
        await until(native.entered.is_set)
        task = asyncio.create_task(forwards.shutdown())
        await until(lambda: forwards._shutdown_task is not None)
        await repeatedly_cancel(task)
        assert "offline-fixture" in forwards._mirroring
        assert not forwards._start_mirror("offline-fixture")
    finally:
        if task is None:
            task = asyncio.create_task(forwards.shutdown())
        await finish(task, native)
    assert mirror.owed_codespaces() == ["offline-fixture"]
    assert not forwards._mirroring


async def test_actual_beacon_cannot_republish_after_owner_withdrawal(registry, monkeypatch):
    native = NativeIO()
    original = ob.BeaconKeeper.beat

    def beat(self):
        if threading.current_thread().name == "owner-beacon":
            native()
        return original(self)

    monkeypatch.setattr(ob.BeaconKeeper, "beat", beat)
    owner = co.ConnectionOwner(lambda _: None)
    stop = asyncio.Event()
    task = asyncio.create_task(co.run_owner_daemon(owner, interval=1, stop_event=stop))
    try:
        await until(native.entered.is_set)
        stop.set()
        await asyncio.sleep(0.02)  # stop's original bounded join expires before this resumes
        await repeatedly_cancel(task)
        assert record_state(registry) == "running"
        assert co.LIVE_FILE.exists()
        assert any(t.name == "owner-beacon" and t.is_alive() for t in threading.enumerate())
    finally:
        await finish(task, native)
    assert record_state(registry) == "finished"
    assert not co.LIVE_FILE.exists()
    assert not any(t.name == "owner-beacon" and t.is_alive() for t in threading.enumerate())


async def test_startup_failure_still_retires_real_beacon_thread(registry, monkeypatch):
    original = ob.BeaconKeeper.start

    def start(self):
        original(self)
        raise RuntimeError("offline startup failure")

    monkeypatch.setattr(ob.BeaconKeeper, "start", start)
    with pytest.raises(RuntimeError, match="offline startup failure"):
        await co.run_owner_daemon(co.ConnectionOwner(lambda _: None))
    assert record_state(registry) == "finished"
    assert not co.LIVE_FILE.exists()
    assert not any(t.name == "owner-beacon" and t.is_alive() for t in threading.enumerate())


@pytest.mark.parametrize("kind", ["relay", "local"])
async def test_actual_channel_health_false_still_awaits_registered_native_cleanup(
    registry, kind,
):
    native = NativeIO()
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "pass", stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL, **no_window_kwargs(),
    )
    await process.wait()

    async def cleanup():
        await asyncio.to_thread(native)

    register_process_cleanup(process, cleanup)
    config = SSHConfig("offline-fixture")
    if kind == "relay":
        channel = SupervisedRelayForward(config, 43127)
        channel._proc = process
    else:
        forward = LocalForward(config, 43127)
        forward._proc = process
        channel = sf._LocalForwardChannel(forward)
    assert not channel.is_alive
    owner = co.ConnectionOwner(lambda _: None)
    owner._channels["offline-fixture"] = channel
    stop = asyncio.Event()
    stop.set()
    task = asyncio.create_task(co.run_owner_daemon(owner, stop_event=stop))
    try:
        await until(native.entered.is_set)
        await repeatedly_cancel(task)
        assert record_state(registry) == "running"
        assert "offline-fixture" in owner._channels
        assert native.child.poll() is None
    finally:
        await finish(task, native)
    assert record_state(registry) == "finished"
    assert not owner._channels


async def test_real_stop_error_does_not_finish_before_native_root_exit(registry):
    native = NativeIO()
    kwargs = no_window_kwargs() if os.name == "nt" else {"start_new_session": True}
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import sys; sys.stdin.buffer.read(1)",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL, **kwargs,
    )
    async def cleanup():
        await asyncio.to_thread(native)

    register_process_cleanup(process, cleanup)
    entered = asyncio.Event()

    async def failed_monitor():
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise RuntimeError("offline monitor failure") from None

    channel = SupervisedRelayForward(SSHConfig("offline-fixture"), 43127)
    channel._proc = process
    channel._monitor_task = asyncio.create_task(failed_monitor())
    await entered.wait()
    owner = co.ConnectionOwner(lambda _: None)
    owner._channels["offline-fixture"] = channel
    stop = asyncio.Event()
    stop.set()
    task = asyncio.create_task(co.run_owner_daemon(owner, stop_event=stop))
    try:
        await until(native.entered.is_set)
        await repeatedly_cancel(task)
        assert record_state(registry) == "running"
        assert process.returncode is not None
        assert native.child.poll() is None
        assert owner._channels["offline-fixture"] is channel
    finally:
        native.release()
        await asyncio.wait_for(process.wait(), 5)
        await finish(task, native)
    assert isinstance(task.exception(), RuntimeError)
    assert record_state(registry) == "finished"
