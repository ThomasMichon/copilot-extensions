"""Pinned owner admission and adopted-conduit lifetime, without host escape."""

import io
import os
import subprocess
import sys
import threading
from unittest.mock import Mock

import pytest

from agent_mcp import custody_launch, custody_lifetime, sockio
from agent_procutil import no_window_kwargs
from agent_procutil._daemon_instance_identity import birth_identity


@pytest.mark.parametrize("pid,birth", [
    (True, "native:1"), (0, "native:1"), (12, ""), (12, "native:\n1"),
])
def test_invalid_owner_refused_before_native_acquisition(monkeypatch, pid, birth):
    pin = Mock()
    monkeypatch.setattr(custody_lifetime, "_pin", pin)
    with pytest.raises(ValueError):
        custody_lifetime.SessionOwner(pid, birth)
    pin.assert_not_called()


def test_unknown_observation_is_not_owner_death_and_pin_is_released(monkeypatch, caplog):
    uncertain = threading.Event()
    dead = threading.Event()
    fired = threading.Event()
    released = Mock()

    def alive():
        if dead.is_set():
            return False
        if uncertain.is_set():
            raise OSError("native observation temporarily unavailable")
        return True

    monkeypatch.setattr(custody_lifetime, "_pin", lambda *_: (alive, released))
    with custody_lifetime.SessionOwner(12, "native:1") as owner:
        owner.start(fired.set)
        uncertain.set()
        assert not fired.wait(.25)
        dead.set()
        assert fired.wait(2)
    owner.close()
    released.assert_called_once_with()
    assert not owner._thread.is_alive()
    assert len([
        record for record in caplog.records
        if record.name == "agent-mcp.custody-lifetime" and "unavailable" in record.message
    ]) == 1


def test_owner_exiting_before_admission_never_starts_monitor(monkeypatch):
    released = Mock()
    monkeypatch.setattr(custody_lifetime, "_pin", lambda *_: (lambda: False, released))
    with custody_lifetime.SessionOwner(12, "native:1") as owner:
        with pytest.raises(ProcessLookupError):
            owner.start(Mock())
        assert owner._thread is None
    released.assert_called_once_with()


def test_pump_uses_explicit_owner_instead_of_parent_watchdog(monkeypatch):
    parent = Mock(side_effect=AssertionError("ancestry is not adopted-session ownership"))
    monkeypatch.setattr("agent_mcp.watchdog.install_parent_death_watchdog", parent)
    monkeypatch.setattr("agent_mcp.watchdog.reap_descendants_on_exit", lambda: False)
    monkeypatch.setattr(sockio.sys, "stdin", Mock(buffer=io.BytesIO(b"")))
    monkeypatch.setattr(sockio.sys, "stdout", Mock(buffer=io.BytesIO()))
    connection = Mock()
    connection.recv.return_value = b""
    owner = Mock()
    owner.start.side_effect = lambda stop: stop()
    sockio.pump(connection, session_owner=owner)
    owner.start.assert_called_once()
    owner.close.assert_called_once()
    connection.close.assert_called()
    parent.assert_not_called()


def test_failed_owner_admission_closes_connection_and_pin(monkeypatch):
    monkeypatch.setattr("agent_mcp.watchdog.reap_descendants_on_exit", lambda: False)
    connection, owner = Mock(), Mock()
    owner.start.side_effect = ProcessLookupError("owner exited")
    with pytest.raises(ProcessLookupError):
        sockio.pump(connection, session_owner=owner)
    owner.close.assert_called_once()
    connection.close.assert_called_once()


def test_unverifiable_owner_refuses_before_incoming_or_custody_effects(monkeypatch):
    monkeypatch.setattr(custody_lifetime, "_pin", Mock(side_effect=OSError("owner unavailable")))
    ensure, attach = Mock(), Mock()
    monkeypatch.setattr(custody_launch.incoming, "ensure_current", ensure)
    monkeypatch.setattr(custody_launch, "attach", attach)
    with pytest.raises(OSError, match="owner unavailable"):
        custody_launch.run_adopted("fixed", "bridge", owner_pid=12, owner_birth="native:1")
    ensure.assert_not_called()
    attach.assert_not_called()


@pytest.mark.skipif(
    os.name != "nt" and not sys.platform.startswith("linux"),
    reason="explicit session owner currently supports Windows and Linux",
)
def test_real_owner_pin_observes_exit_without_signalling_the_owner():
    fired = threading.Event()
    with subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read(1)"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **no_window_kwargs(),
    ) as process:
        try:
            birth = birth_identity(process.pid)
            with pytest.raises(ProcessLookupError):
                custody_lifetime.SessionOwner(process.pid, birth + "-different")
            with custody_lifetime.SessionOwner(process.pid, birth) as owner:
                owner.start(fired.set)
                assert not fired.wait(.15)
                assert process.poll() is None
                process.stdin.write(b"x")
                process.stdin.flush()
                assert process.wait(timeout=5) == 0
                assert fired.wait(2)
            assert not owner._thread.is_alive()
        finally:
            process.stdin.close()
            process.wait(timeout=5)
