"""A disposable logind transport fixture; never contact the host system bus."""

from __future__ import annotations

import importlib
import os
import sys
from types import SimpleNamespace

import pytest

from worktree_manager.restart_evidence import PositiveEvidence, RecoveryJournal
from worktree_manager.restart_observer import HostEpoch


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux logind FD transport")


@pytest.fixture
def notification_module():
    return importlib.import_module("worktree_manager.restart_notifications")


def positive():
    return PositiveEvidence(
        project="example", worktree_id="example-one", worktree_path="/example/worktree",
        native_session_id="00000000-0000-4000-8000-000000000001",
        native_incarnation="native-one", binding_proof="inuse-hold-v1", owner_revision=1,
        session_incarnation="mux-one", mapping_revision=1, platform="linux",
        environment_id="environment-one", boot_id="boot-one",
        observed_at=100.0, observation_sequence=1,
    )


class FakeLogindBus:
    def __init__(self):
        from dbus_next import MessageType

        self.reply_type = MessageType.METHOD_RETURN
        self.connected = False
        self.calls = []
        self.handler = None
        self.fds = []
        self.readers = []
        self.denied = False
        self.preparing = False

    async def connect(self):
        self.connected = True
        return self

    async def call(self, message):
        from dbus_next import MessageType, Variant

        self.calls.append(message.member)
        result = SimpleNamespace(
            message_type=self.reply_type, signature="", body=[], unix_fds=[],
            sender=message.destination, error_name=None,
        )
        if message.member == "GetNameOwner":
            result.signature, result.body = "s", [":1.10"]
        elif message.member == "Get":
            value = (
                Variant("t", 5_000_000) if message.body[1] == "InhibitDelayMaxUSec"
                else Variant("b", self.preparing)
            )
            result.signature, result.body = "v", [value]
        elif message.member == "Inhibit":
            if self.denied:
                result.message_type = MessageType.ERROR
                result.error_name = "org.freedesktop.DBus.Error.AccessDenied"
                result.body = ["not permitted"]
            else:
                reader, writer = os.pipe()
                os.set_blocking(reader, False)
                self.readers.append(reader)
                self.fds.append(writer)
                result.signature, result.body, result.unix_fds = "h", [0], [writer]
        return result

    def add_message_handler(self, handler):
        self.handler = handler

    def remove_message_handler(self, handler):
        assert self.handler == handler
        self.handler = None

    def disconnect(self):
        self.connected = False

    def prepare(self, value, **overrides):
        from dbus_next import MessageType

        message = SimpleNamespace(
            message_type=MessageType.SIGNAL, path="/org/freedesktop/login1",
            interface="org.freedesktop.login1.Manager", member="PrepareForShutdown",
            sender=":1.10", signature="b", body=[value], unix_fds=[],
        )
        for name, field in overrides.items():
            setattr(message, name, field)
        self.handler(message)

    def close(self):
        for reader in self.readers:
            os.close(reader)


def pump_until(adapter, predicate):
    import time

    for _ in range(100):
        adapter.pump()
        if predicate():
            return
        time.sleep(0.001)
    pytest.fail(f"adapter did not settle: {adapter.status}")


@pytest.fixture
def active_adapter(tmp_path, notification_module):
    bus = FakeLogindBus()
    journal = RecoveryJournal(tmp_path)
    journal.observe(positive())
    adapter = notification_module.LogindShutdownAdapter(
        journal, HostEpoch("linux", "environment-one", "boot-one"),
        bus_factory=lambda: bus,
    )
    try:
        adapter.start()
        pump_until(adapter, lambda: adapter.status["status"] == "watching")
        yield adapter, bus, journal
    finally:
        adapter.close()
        bus.close()


def test_inhibitor_precedes_subscribing_and_closes_on_shutdown(active_adapter):
    adapter, bus, journal = active_adapter
    assert bus.calls.index("Inhibit") < bus.calls.index("AddMatch")
    original = bus.fds[-1]
    assert not os.get_inheritable(original)

    bus.prepare(True)

    row = journal.snapshot()[0]
    assert row["disposition"] == "shutdown-pending"
    assert row["notification_id"]
    assert adapter.status["status"] == "shutdown-pending"
    with pytest.raises(OSError):
        os.fstat(original)
    assert os.read(bus.readers[-1], 1) == b""
    bus.prepare(True)
    assert journal.snapshot() == [row]


def test_cancellation_is_durable_before_rearming(active_adapter):
    adapter, bus, journal = active_adapter
    bus.prepare(True)
    bus.prepare(False)
    assert journal.snapshot()[0]["detail"] == "shutdown-cancelled"
    pump_until(adapter, lambda: adapter.status["status"] == "watching")
    assert bus.calls.count("Inhibit") == 2
    with pytest.raises(BlockingIOError):
        os.read(bus.readers[-1], 1)


@pytest.mark.parametrize("changes", [
    {"sender": ":1.99"},
    {"path": "/example/unrelated"},
    {"interface": "org.example.unrelated"},
    {"member": "PrepareForSleep"},
])
def test_unrelated_or_spoofed_signal_cannot_mark_shutdown(active_adapter, changes):
    adapter, bus, journal = active_adapter
    original = journal.snapshot()
    bus.prepare(True, **changes)
    assert journal.snapshot() == original
    assert adapter.status["status"] == "watching"


def test_malformed_owner_signal_blocks_and_releases_fd(active_adapter):
    adapter, bus, journal = active_adapter
    original = journal.snapshot()
    bus.prepare(1)
    assert adapter.status["status"] == "blocked"
    assert journal.snapshot() == original
    assert os.read(bus.readers[-1], 1) == b""


def test_write_failure_is_visible_and_never_holds_inhibitor(
    active_adapter, monkeypatch,
):
    from worktree_manager.restart_evidence import RecoveryEvidenceError

    adapter, bus, journal = active_adapter

    def denied(*args, **kwargs):
        raise RecoveryEvidenceError("read-only test journal")

    monkeypatch.setattr(journal, "shutdown_pending", denied)
    bus.prepare(True)
    assert adapter.status["status"] == "blocked"
    assert "read-only test journal" in adapter.status["blocker"]
    assert os.read(bus.readers[-1], 1) == b""
    assert journal.snapshot()[0]["disposition"] == "live"


def test_disconnect_blocks_without_retry(active_adapter):
    adapter, bus, journal = active_adapter
    bus.connected = False
    adapter.pump()
    assert adapter.status["status"] == "blocked"
    assert os.read(bus.readers[-1], 1) == b""
    calls = list(bus.calls)
    adapter.pump()
    assert bus.calls == calls


def test_close_prevents_new_events_and_releases_owned_fd(active_adapter):
    adapter, bus, journal = active_adapter
    original = journal.snapshot()
    adapter.close()
    assert bus.handler is None
    assert os.read(bus.readers[-1], 1) == b""
    adapter.pump()
    assert journal.snapshot() == original


def test_inhibit_permission_denial_is_not_escalated(tmp_path, notification_module):
    bus = FakeLogindBus()
    bus.denied = True
    adapter = notification_module.LogindShutdownAdapter(
        RecoveryJournal(tmp_path), HostEpoch("linux", "environment-one", "boot-one"),
        bus_factory=lambda: bus,
    )
    try:
        adapter.start()
        pump_until(adapter, lambda: adapter.status["status"] == "blocked")
        assert "AccessDenied" in adapter.status["blocker"]
        assert "AddMatch" not in bus.calls
        assert not list(tmp_path.iterdir())
    finally:
        adapter.close()
        bus.close()


def test_start_during_shutdown_reads_preparing_property(tmp_path, notification_module):
    bus = FakeLogindBus()
    bus.preparing = True
    journal = RecoveryJournal(tmp_path)
    journal.observe(positive())
    adapter = notification_module.LogindShutdownAdapter(
        journal, HostEpoch("linux", "environment-one", "boot-one"),
        bus_factory=lambda: bus,
    )
    try:
        adapter.start()
        pump_until(adapter, lambda: adapter.status["status"] == "shutdown-pending")
        assert journal.snapshot()[0]["disposition"] == "shutdown-pending"
        assert os.read(bus.readers[-1], 1) == b""
    finally:
        adapter.close()
        bus.close()


def test_locked_journal_fails_promptly_and_releases_delay_fd(active_adapter):
    import sqlite3
    import time

    adapter, bus, journal = active_adapter
    with sqlite3.connect(journal.path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        bus.prepare(True)
        assert time.monotonic() - started < 0.5
        assert adapter.status["status"] == "blocked"
        assert "locked" in adapter.status["blocker"]
        assert os.read(bus.readers[-1], 1) == b""


def test_owner_replacement_releases_fd_and_blocks(active_adapter):
    adapter, bus, journal = active_adapter
    original = journal.snapshot()
    bus.prepare(
        True, sender="org.freedesktop.DBus", path="/org/freedesktop/DBus",
        interface="org.freedesktop.DBus", member="NameOwnerChanged", signature="sss",
        body=["org.freedesktop.login1", ":1.10", ":1.20"],
    )
    assert adapter.status["status"] == "blocked"
    assert journal.snapshot() == original
    assert os.read(bus.readers[-1], 1) == b""


def test_close_releases_reply_fd_before_coroutine_claim(active_adapter):
    from dbus_next import MessageType

    adapter, bus, journal = active_adapter
    reader, writer = os.pipe()
    try:
        bus.handler(SimpleNamespace(
            message_type=MessageType.METHOD_RETURN, sender=":1.10",
            unix_fds=[writer],
        ))
        adapter.close()
        assert os.read(reader, 1) == b""
    finally:
        os.close(reader)
