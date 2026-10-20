"""A disposable logind transport fixture; never contact the host system bus."""

from __future__ import annotations

import importlib
import os
import sys
import threading
from dataclasses import replace
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


@pytest.mark.parametrize("phase", ["pump", "close"])
@pytest.mark.parametrize("unclaimed", [False, True], ids=["inhibitor", "unclaimed"])
def test_runtime_retains_real_adapter_descriptor_cleanup_failure(
    tmp_path, notification_module, monkeypatch, phase, unclaimed,
):
    from worktree_manager import source_config
    from worktree_manager.restart_notification_runtime import RestartNotificationRuntime
    from worktree_manager.restart_observer import RestartObserver

    watching = threading.Event()
    failed = threading.Event()
    bus = FakeLogindBus()
    real_adapter = notification_module.LogindShutdownAdapter
    instances = []

    class ObservedAdapter(real_adapter):
        def __init__(self, journal, epoch):
            super().__init__(journal, epoch, bus_factory=lambda: bus)
            instances.append(self)

        def pump(self, **kwargs):
            super().pump(**kwargs)
            if self.status["status"] == "watching":
                watching.set()

    monkeypatch.setattr(notification_module, "LogindShutdownAdapter", ObservedAdapter)
    source_config.config_path(tmp_path).write_text(
        "[restart_recovery]\nshutdown_notifications = true\n", encoding="utf-8",
    )
    observer = RestartObserver(
        tmp_path, epoch=HostEpoch("linux", "environment-one", "boot-one"),
    )
    runtime = RestartNotificationRuntime(tmp_path, observer)
    close_fd = os.close
    target = None

    def fail_target(fd):
        if fd == target:
            failed.set()
            raise OSError("synthetic actual descriptor release failure")
        close_fd(fd)

    try:
        runtime.activate()
        runtime.pump(admitted=True)
        assert watching.wait(2)
        target = bus.fds[-1]
        if unclaimed:
            reader, target = os.pipe()
            bus.readers.append(reader)
            instances[0]._received_fds.add(target)
        monkeypatch.setattr(notification_module.os, "close", fail_target)
        if phase == "pump":
            bus.connected = False
            assert failed.wait(2), "actual adapter pump did not reach descriptor cleanup"
        runtime.pause()
        assert failed.is_set() and instances[0]._loop.is_closed()
        assert not runtime.drained
        assert "descriptor release failure" in runtime.status["blocker"]
        with pytest.raises(RuntimeError, match="descriptor release failure"):
            runtime.close()
        runtime.pump(admitted=True)
        assert len(instances) == 1
    finally:
        runtime.pause()
        monkeypatch.setattr(notification_module.os, "close", close_fd)
        if target is not None:
            close_fd(target)
        bus.close()


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


@pytest.mark.parametrize("reason", ["intentional", "unknown"])
def test_native_end_before_cancellation_loses_shutdown_authority(active_adapter, reason):
    adapter, bus, journal = active_adapter
    bus.prepare(True)
    journal.record_exit(
        positive(), reason=reason, detail="native-session-end", end_consumed=True,
    )
    assert journal.snapshot()[0]["disposition"] == "shutdown-interrupted"

    bus.prepare(False)
    pump_until(adapter, lambda: adapter.status["status"] == "watching")

    row = journal.snapshot()[0]
    assert row["disposition"] == "unknown"
    assert row["notification_id"] == ""
    assert row["detail"] == "native-end-after-cancel-unconfirmed"
    assert row["end_consumed"] is True
    journal.record_exit(positive(), reason="intentional", detail="native-session-end")
    assert journal.snapshot()[0]["disposition"] == "unknown"


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


def test_permanent_failure_closes_transport_in_same_pump(active_adapter):
    adapter, bus, journal = active_adapter
    bus.connected = False
    adapter.pump()
    assert adapter.status["status"] == "blocked"
    assert adapter._loop.is_closed()
    assert bus.handler is None
    assert not bus.connected


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


@pytest.mark.parametrize("preparing", [False, True])
@pytest.mark.parametrize("ended", [False, True])
def test_same_boot_replacement_reconciles_persisted_notification(
    active_adapter, notification_module, preparing, ended,
):
    first, first_bus, journal = active_adapter
    first_bus.prepare(True)
    notification_id = journal.snapshot()[0]["notification_id"]
    if ended:
        journal.record_exit(
            positive(), reason="unknown", detail="native-end-cause-unconfirmed",
            end_consumed=True,
        )
    first.close()
    bus = FakeLogindBus()
    bus.preparing = preparing
    replacement = notification_module.LogindShutdownAdapter(
        journal, HostEpoch("linux", "environment-one", "boot-one"),
        bus_factory=lambda: bus,
    )
    try:
        replacement.start()
        pump_until(replacement, lambda: replacement.status["status"] == (
            "shutdown-pending" if preparing else "watching"
        ))
        if preparing:
            assert journal.snapshot()[0]["notification_id"] == notification_id
            bus.prepare(False)
            pump_until(replacement, lambda: replacement.status["status"] == "watching")
        row = journal.snapshot()[0]
        assert row["notification_id"] == ""
        assert row["disposition"] == ("unknown" if ended else "live")
        assert row["end_consumed"] is ended
    finally:
        replacement.close()
        bus.close()


def test_replacement_cancels_all_current_epoch_ids_but_not_foreign_or_closed(
    tmp_path, notification_module,
):
    journal = RecoveryJournal(tmp_path)
    first = positive()
    second = replace(first, worktree_id="example-two")
    closed = replace(first, worktree_id="example-closed")
    foreign = replace(first, worktree_id="example-foreign", boot_id="old-boot")
    journal.observe(first)
    journal.observe(closed)
    journal.shutdown_pending(
        "linux", "environment-one", "boot-one", notification_id="older-one",
    )
    journal.record_exit(closed, reason="intentional", detail="operator-stop")
    journal.observe(second)
    journal.shutdown_pending(
        "linux", "environment-one", "boot-one", notification_id="older-two",
    )
    journal.observe(foreign)
    journal.shutdown_pending(
        "linux", "environment-one", "old-boot", notification_id="foreign-one",
    )
    bus = FakeLogindBus()
    adapter = notification_module.LogindShutdownAdapter(
        journal, HostEpoch("linux", "environment-one", "boot-one"),
        bus_factory=lambda: bus,
    )
    try:
        adapter.start()
        pump_until(adapter, lambda: adapter.status["status"] == "watching")
        rows = {row["last_positive"]["worktree_id"]: row for row in journal.snapshot()}
        for key in ("example-one", "example-two"):
            assert rows[key]["notification_id"] == ""
            assert rows[key]["disposition"] == "live"
        assert rows["example-closed"]["disposition"] == "intentional"
        assert rows["example-foreign"]["notification_id"] == "foreign-one"
        assert rows["example-foreign"]["disposition"] == "shutdown-pending"
    finally:
        adapter.close()
        bus.close()


def test_replacement_cannot_report_watching_before_cancellation_is_durable(
    active_adapter, notification_module, monkeypatch,
):
    from worktree_manager.restart_evidence import RecoveryEvidenceError

    first, first_bus, journal = active_adapter
    first_bus.prepare(True)
    first.close()
    bus = FakeLogindBus()
    adapter = notification_module.LogindShutdownAdapter(
        journal, HostEpoch("linux", "environment-one", "boot-one"),
        bus_factory=lambda: bus,
    )

    def denied(*args, **kwargs):
        assert adapter.status["status"] != "watching"
        raise RecoveryEvidenceError("cancel write denied")

    monkeypatch.setattr(journal, "shutdown_cancelled", denied)
    try:
        adapter.start()
        pump_until(adapter, lambda: adapter.status["status"] == "blocked")
        assert "cancel write denied" in adapter.status["blocker"]
        assert journal.snapshot()[0]["disposition"] == "shutdown-pending"
        assert os.read(bus.readers[-1], 1) == b""
    finally:
        adapter.close()
        bus.close()
