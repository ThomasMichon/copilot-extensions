"""Pinned owner admission and adopted-conduit lifetime, without host escape."""

import io
import os
import socket
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


def test_initial_post_attach_observation_outage_retains_owner(monkeypatch, caplog):
    unavailable, dead, fired = threading.Event(), threading.Event(), threading.Event()
    unavailable.set()
    released = Mock()

    def alive():
        if unavailable.is_set():
            raise OSError("first post-attach observation unavailable")
        return not dead.is_set()

    monkeypatch.setattr(custody_lifetime, "_pin", lambda *_: (alive, released))
    with custody_lifetime.SessionOwner(12, "native:1") as owner:
        owner.start(fired.set)
        assert not fired.wait(.25)
        unavailable.clear()
        dead.set()
        assert fired.wait(2)
    released.assert_called_once_with()
    assert len([
        record for record in caplog.records
        if record.name == "agent-mcp.custody-lifetime" and "unavailable" in record.message
    ]) == 1


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


def test_dedicated_exit_follows_connection_and_owner_cleanup(monkeypatch):
    events = []
    owner = Mock()
    owner.__enter__ = Mock(return_value=owner)
    owner.__exit__ = Mock(side_effect=lambda *_: events.append("owner-released"))
    monkeypatch.setattr(custody_lifetime, "SessionOwner", Mock(return_value=owner))
    monkeypatch.setattr(custody_launch.incoming, "ensure_current", Mock())
    monkeypatch.setattr(custody_launch, "attach", Mock())
    monkeypatch.setattr(
        sockio, "pump", lambda *_args, **_kwargs: events.append("pump-settled"),
    )

    def exit_process(code):
        events.append("process-exit")
        raise SystemExit(code)

    monkeypatch.setattr(custody_launch.os, "_exit", exit_process)
    with pytest.raises(SystemExit) as result:
        custody_launch.run_adopted("fixed", "bridge", owner_pid=12, owner_birth="native:1")
    assert result.value.code == 0
    assert events == ["pump-settled", "owner-released", "process-exit"]


@pytest.mark.parametrize("pid", ["42", 42])
def test_native_fixture_normalizes_inspected_pid_before_native_open(monkeypatch, pid):
    import importlib.util
    from pathlib import Path

    from agent_procutil import _daemon_instance_identity, daemon_broker

    spec = importlib.util.spec_from_file_location(
        "custody_native_lifetime_control",
        Path(__file__).with_name("test_custody_native_lifetime.py"),
    )
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    process = Mock()

    def open_process(value, _argv):
        assert type(value) is int
        assert value == 42
        return process

    monkeypatch.setattr(daemon_broker, "WindowsDaemonProcess", open_process)
    monkeypatch.setattr(_daemon_instance_identity, "_windows_birth", lambda _: "native:1")
    assert native.pin(pid, "native:1") is process


def test_native_fixture_owner_explicitly_forwards_redirected_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "custody_lifetime_stream_control",
        Path(__file__).with_name("custody_lifetime_fixture.py"),
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    streams = {name: Mock() for name in ("stdin", "stdout", "stderr")}
    for name, stream in streams.items():
        monkeypatch.setattr(fixture.sys, name, stream)
    monkeypatch.setattr(
        fixture.sys, "argv",
        ["fixture", "owner", "actor", "root", "fixed", "config", "private"],
    )
    monkeypatch.setattr(fixture, "birth_identity", lambda _: "native:1")

    def launch(_argv: list[str], **kwargs: object) -> None:
        for name, stream in streams.items():
            assert kwargs.get(name) is stream
        assert kwargs["require_job"] is True
        assert kwargs["allow_breakaway"] is True
        raise SystemExit(0)

    monkeypatch.setattr(fixture.pu, "spawn_sync_in_kill_on_close_job", launch)
    with pytest.raises(SystemExit) as result:
        fixture.main()
    assert result.value.code == 0


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


@pytest.mark.skipif(
    os.name != "nt" and not sys.platform.startswith("linux"),
    reason="explicit session owner currently supports Windows and Linux",
)
@pytest.mark.parametrize("command", ["bridge", "forward"])
def test_dedicated_conduit_exits_cleanly_with_stdin_writer_still_open(
    monkeypatch: pytest.MonkeyPatch, command: str,
) -> None:
    for key in ("AGENT_MCP_NO_MULTIPLEX", "AGENT_MCP_NO_SERVE", "AGENT_MCP_NO_ENSURE_SERVE"):
        monkeypatch.delenv(key, raising=False)
    code = """
import socket, sys
from unittest.mock import patch
from agent_mcp import custody_launch
from agent_mcp.__main__ import main
connection = socket.create_connection(('127.0.0.1', int(sys.argv[1])))
with patch.object(custody_launch.incoming, 'ensure_current', return_value=object()), \\
     patch.object(custody_launch, 'attach', return_value=connection):
    raise SystemExit(main([
        sys.argv[4], 'bridge', '--session-owner-pid', sys.argv[2],
        '--session-owner-birth', sys.argv[3],
    ]))
"""
    with subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read(1)"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **no_window_kwargs(),
    ) as owner, socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(5)
        with subprocess.Popen(
            [sys.executable, "-c", code, str(listener.getsockname()[1]),
             str(owner.pid), birth_identity(owner.pid), command],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            **no_window_kwargs(),
        ) as conduit:
            try:
                with listener.accept()[0] as connection:
                    connection.settimeout(5)
                    conduit.stdin.write(b"reader-started\n")
                    conduit.stdin.flush()
                    assert connection.recv(65536) == b"reader-started\n"
                    owner.stdin.write(b"x")
                    owner.stdin.flush()
                    assert owner.wait(timeout=5) == 0
                    result = conduit.wait(timeout=5)
                    error = conduit.stderr.read()
                    assert result == 0, error.decode(errors="replace")
                    assert conduit.stdout.read() == b""
                    assert not conduit.stdin.closed
            finally:
                conduit.stdin.close()
                conduit.wait(timeout=5)
                owner.stdin.close()
                owner.wait(timeout=5)
