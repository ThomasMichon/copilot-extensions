"""Actual private D-Bus/SCM_RIGHTS boundary, never the host system bus."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time

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
