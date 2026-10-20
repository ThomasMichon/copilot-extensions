"""Actual private D-Bus/SCM_RIGHTS boundary, never the host system bus."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from worktree_manager.restart_evidence import PositiveEvidence, RecoveryJournal
from worktree_manager.restart_observer import HostEpoch


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux logind FD transport")


def advance(adapter, predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        adapter.pump()
        if predicate():
            return
        time.sleep(0.001)
    pytest.fail(f"Private D-Bus adapter did not settle: {adapter.status}")


@pytest.fixture
def private_bus(tmp_path):
    executable = shutil.which("dbus-daemon")
    if executable is None:
        pytest.skip("Private D-Bus qualification needs dbus-daemon; no host-bus fallback")
    socket_path = tmp_path / "private-bus.sock"
    address = f"unix:path={socket_path}"
    process = subprocess.Popen(
        [executable, "--session", "--nofork", f"--address={address}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 3
        while not socket_path.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert socket_path.exists(), "Disposable D-Bus daemon failed to start"
        yield address
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if process.stderr is not None:
            process.stderr.close()


def test_real_dbus_fd_checkpoint_cancellation_and_owner_loss(
    tmp_path, private_bus,
):
    from dbus_next import PropertyAccess
    from dbus_next.aio import MessageBus
    from dbus_next.service import ServiceInterface, dbus_property, method, signal

    from worktree_manager.restart_notifications import LogindShutdownAdapter

    class Logind(ServiceInterface):
        def __init__(self):
            super().__init__("org.freedesktop.login1.Manager")
            self.pipes = []

        @method()
        def Inhibit(self, what: "s", who: "s", why: "s", mode: "s") -> "h":  # noqa: F821
            assert what == "shutdown" and mode == "delay"
            reader, writer = os.pipe()
            os.set_blocking(reader, False)
            self.pipes.append((reader, writer))
            return writer

        @dbus_property(access=PropertyAccess.READ)
        def InhibitDelayMaxUSec(self) -> "t":  # noqa: F821
            return 5_000_000

        @dbus_property(access=PropertyAccess.READ)
        def PreparingForShutdown(self) -> "b":  # noqa: F821
            return False

        @signal()
        def PrepareForShutdown(self, preparing) -> "b":  # noqa: F821
            return preparing

    loop = asyncio.new_event_loop()
    service = Logind()

    async def serve():
        bus = await MessageBus(bus_address=private_bus, negotiate_unix_fd=True).connect()
        bus.export("/org/freedesktop/login1", service)
        await bus.request_name("org.freedesktop.login1")
        return bus

    service_bus = loop.run_until_complete(serve())
    journal = RecoveryJournal(tmp_path / "state")
    journal.observe(PositiveEvidence(
        project="example", worktree_id="example-one", worktree_path="/example/worktree",
        native_session_id="00000000-0000-4000-8000-000000000001",
        native_incarnation="native-one", binding_proof="inuse-hold-v1", owner_revision=1,
        session_incarnation="mux-one", mapping_revision=1, platform="linux",
        environment_id="environment-one", boot_id="boot-one",
        observed_at=100.0, observation_sequence=1,
    ))
    adapter = LogindShutdownAdapter(
        journal, HostEpoch("linux", "environment-one", "boot-one"),
        bus_factory=lambda: MessageBus(bus_address=private_bus, negotiate_unix_fd=True),
        loop_factory=lambda: loop,
    )
    closed_writer_indices = set()
    try:
        adapter.start()
        advance(adapter, lambda: adapter.status["status"] in {"watching", "blocked"})
        assert adapter.status["status"] == "watching"
        reader, writer = service.pipes[-1]
        os.close(writer)
        closed_writer_indices.add(len(service.pipes) - 1)
        with pytest.raises(BlockingIOError):
            os.read(reader, 1)
        service.PrepareForShutdown(True)
        advance(adapter, lambda: adapter.status["status"] == "shutdown-pending")
        assert journal.snapshot()[0]["disposition"] == "shutdown-pending"
        assert os.read(reader, 1) == b""

        service.PrepareForShutdown(False)
        advance(adapter, lambda: adapter.status["status"] == "watching")
        assert journal.snapshot()[0]["detail"] == "shutdown-cancelled"
        reader, writer = service.pipes[-1]
        os.close(writer)
        closed_writer_indices.add(len(service.pipes) - 1)
        with pytest.raises(BlockingIOError):
            os.read(reader, 1)

        loop.run_until_complete(service_bus.release_name("org.freedesktop.login1"))
        advance(adapter, lambda: adapter.status["status"] == "blocked")
        assert os.read(reader, 1) == b""
        assert journal.snapshot()[0]["disposition"] == "live"
    finally:
        service_bus.disconnect()
        adapter.close()
        if not loop.is_closed():
            loop.close()
        for index, (reader, writer) in enumerate(service.pipes):
            os.close(reader)
            if index not in closed_writer_indices:
                os.close(writer)


def test_inhibit_timeout_closes_connection_before_late_fd_reply(tmp_path, private_bus):
    from dbus_next import PropertyAccess
    from dbus_next.aio import MessageBus
    from dbus_next.service import ServiceInterface, dbus_property, method

    from worktree_manager.restart_notifications import LogindShutdownAdapter

    class DelayedLogind(ServiceInterface):
        def __init__(self):
            super().__init__("org.freedesktop.login1.Manager")
            self.release_reply = asyncio.Event()
            self.pipe = None
            self.replied = False

        @method()
        async def Inhibit(self, what: "s", who: "s", why: "s", mode: "s") -> "h":  # noqa: F821
            self.pipe = os.pipe()
            os.set_blocking(self.pipe[0], False)
            await self.release_reply.wait()
            self.replied = True
            return self.pipe[1]

        @dbus_property(access=PropertyAccess.READ)
        def InhibitDelayMaxUSec(self) -> "t":  # noqa: F821
            return 5_000_000

    service_loop = asyncio.new_event_loop()
    service = DelayedLogind()

    async def serve():
        bus = await MessageBus(bus_address=private_bus, negotiate_unix_fd=True).connect()
        bus.export("/org/freedesktop/login1", service)
        await bus.request_name("org.freedesktop.login1")
        return bus

    service_bus = service_loop.run_until_complete(serve())
    client_bus = None

    def connect():
        nonlocal client_bus
        client_bus = MessageBus(bus_address=private_bus, negotiate_unix_fd=True)
        return client_bus

    adapter = LogindShutdownAdapter(
        RecoveryJournal(tmp_path / "state"),
        HostEpoch("linux", "environment-one", "boot-one"), bus_factory=connect,
    )
    writer_closed = False
    try:
        adapter.start()
        deadline = time.monotonic() + 5
        while adapter.status["status"] != "blocked" and time.monotonic() < deadline:
            service_loop.run_until_complete(asyncio.sleep(0))
            adapter.pump()
            time.sleep(0.001)
        assert adapter.status["status"] == "blocked"
        assert service.pipe is not None
        service.release_reply.set()
        service_loop.run_until_complete(asyncio.sleep(0.05))
        assert service.replied
        reader, writer = service.pipe
        os.close(writer)
        writer_closed = True
        # Let the private broker either forward the FD or discard the dead recipient.
        service_loop.run_until_complete(asyncio.sleep(0.05))
        assert not client_bus.connected
        assert adapter._loop.is_closed()
        assert os.read(reader, 1) == b""
        adapter.pump()
        assert adapter.status["status"] == "blocked"
    finally:
        adapter.close()
        service_bus.disconnect()
        service_loop.run_until_complete(service_bus.wait_for_disconnect())
        service_loop.close()
        if service.pipe is not None:
            os.close(service.pipe[0])
            if not writer_closed:
                os.close(service.pipe[1])


@pytest.mark.parametrize("blocked_work", [False, True])
def test_real_dbus_setup_at_production_daemon_cadence(
    tmp_path, private_bus, monkeypatch, blocked_work,
):
    from dbus_next import PropertyAccess
    from dbus_next.aio import MessageBus
    from dbus_next.service import ServiceInterface, dbus_property, method, signal

    from worktree_manager import mux_daemon, restart_observer, source_config
    from worktree_manager.restart_notifications import LogindShutdownAdapter

    class Logind(ServiceInterface):
        def __init__(self):
            super().__init__("org.freedesktop.login1.Manager")
            self.pipes = []

        @method()
        def Inhibit(self, what: "s", who: "s", why: "s", mode: "s") -> "h":  # noqa: F821
            reader, writer = os.pipe()
            self.pipes.append((reader, writer))
            return writer

        @dbus_property(access=PropertyAccess.READ)
        def InhibitDelayMaxUSec(self) -> "t":  # noqa: F821
            return 1_000_000 if blocked_work else 5_000_000

        @dbus_property(access=PropertyAccess.READ)
        def PreparingForShutdown(self) -> "b":  # noqa: F821
            return False

        @signal()
        def PrepareForShutdown(self, preparing) -> "b":  # noqa: F821
            return preparing

    loop = asyncio.new_event_loop()
    service = Logind()

    async def serve():
        bus = await MessageBus(bus_address=private_bus, negotiate_unix_fd=True).connect()
        bus.export("/org/freedesktop/login1", service)
        await bus.request_name("org.freedesktop.login1")
        return bus

    service_bus = loop.run_until_complete(serve())
    final_states = []
    watching = threading.Event()
    checkpointed = threading.Event()
    closed = threading.Event()
    owners = []

    class ObservedAdapter(LogindShutdownAdapter):
        def __init__(self, journal, epoch):
            owners.append(threading.current_thread())
            super().__init__(
                journal, epoch,
                bus_factory=lambda: MessageBus(bus_address=private_bus, negotiate_unix_fd=True),
                loop_factory=lambda: loop,
            )

        def close(self):
            final_states.append(self.status.copy())
            service_bus.disconnect()
            super().close()
            closed.set()

        def pump(self, **kwargs):
            super().pump(**kwargs)
            if self.status["status"] == "watching":
                watching.set()

        def _prepare(self, preparing):
            super()._prepare(preparing)
            if preparing and self.status["status"] == "shutdown-pending":
                checkpointed.set()

    monkeypatch.setitem(sys.modules, "worktree_manager.restart_notifications", SimpleNamespace(
        LogindShutdownAdapter=ObservedAdapter,
    ))
    monkeypatch.setattr(restart_observer, "native_epoch", lambda: HostEpoch(
        "linux", "environment-one", "boot-one",
    ))
    root = tmp_path / "state"
    root.mkdir()
    source_config.config_path(root).write_text(
        "[restart_recovery]\nshutdown_notifications = true\n", encoding="utf-8",
    )
    journal = RecoveryJournal(root)
    journal.observe(PositiveEvidence(
        project="example", worktree_id="example-one", worktree_path="/example/worktree",
        native_session_id="00000000-0000-4000-8000-000000000001",
        native_incarnation="native-one", binding_proof="inuse-hold-v1", owner_revision=1,
        session_incarnation="mux-one", mapping_revision=1, platform="linux",
        environment_id="environment-one", boot_id="boot-one",
        observed_at=100.0, observation_sequence=1,
    ))
    closed_writers = set()
    probed = False

    def blocking_probe(self, registry):
        nonlocal probed
        if not blocked_work or probed:
            return
        probed = True
        assert watching.wait(5), "notification setup waited on the blocked ordinary loop"
        reader, writer = service.pipes[-1]
        os.close(writer)
        closed_writers.add(len(service.pipes) - 1)
        os.set_blocking(reader, False)
        with pytest.raises(BlockingIOError):
            os.read(reader, 1)
        started = time.monotonic()
        loop.call_soon_threadsafe(service.PrepareForShutdown, True)
        assert checkpointed.wait(0.75), "shutdown checkpoint missed the native delay budget"
        elapsed = time.monotonic() - started
        assert elapsed < 0.75
        assert journal.snapshot()[0]["disposition"] == "shutdown-pending"
        assert os.read(reader, 1) == b""
        # Keep ordinary work blocked beyond the one-second native delay.
        time.sleep(max(0.0, 1.25 - elapsed))

    monkeypatch.setattr(mux_daemon.AttachedClientObserver, "observe", blocking_probe)
    release_lease = mux_daemon._release_daemon_lease

    def release_after_native_cleanup(lease):
        assert closed.is_set() and loop.is_closed()
        assert len(owners) == 1 and not owners[0].is_alive()
        release_lease(lease)

    monkeypatch.setattr(mux_daemon, "_release_daemon_lease", release_after_native_cleanup)
    try:
        assert mux_daemon.run_daemon_foreground(root, max_iterations=5) == 0
        expected = "shutdown-pending" if blocked_work else "watching"
        assert final_states and final_states[0]["status"] == expected
        assert final_states[0]["qualified"] is False
    finally:
        service_bus.disconnect()
        if not loop.is_closed():
            loop.run_until_complete(service_bus.wait_for_disconnect())
            loop.close()
        for index, (reader, writer) in enumerate(service.pipes):
            os.close(reader)
            if index not in closed_writers:
                os.close(writer)
