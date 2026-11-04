from __future__ import annotations

import ctypes as c
from types import SimpleNamespace

import pytest

from worktree_manager import daemons_status, windows_daemon_observation as observation


def _command_buffer(text):
    encoded = text.encode("utf-16-le")
    offset = c.sizeof(observation._UnicodeString)
    buffer = c.create_string_buffer(offset + len(encoded))
    header = observation._UnicodeString.from_buffer(buffer)
    header.length = header.maximum = len(encoded)
    header.buffer = c.addressof(buffer) + offset
    c.memmove(header.buffer, encoded, len(encoded))
    return buffer


def test_native_command_line_preserves_literal_unicode_and_quotes():
    command = '"example python.exe" -m worktree_manager mux-daemon run --root="spaces \u65e5"'
    assert observation._decode_command_line(_command_buffer(command)) == command


@pytest.mark.parametrize("damage", ["short", "odd", "outside", "oversize", "encoding"])
def test_native_command_line_refuses_invalid_native_buffers(damage):
    buffer = _command_buffer("example")
    if damage == "short":
        buffer = c.create_string_buffer(c.sizeof(observation._UnicodeString) - 1)
    else:
        header = observation._UnicodeString.from_buffer(buffer)
        if damage == "odd":
            header.length = 1
        elif damage == "outside":
            header.buffer = c.addressof(buffer) - 1
        elif damage == "oversize":
            header.length = header.maximum + 2
        else:
            c.memmove(header.buffer, b"\x00\xd8", 2)
    with pytest.raises(OSError):
        observation._decode_command_line(buffer)


def test_native_mux_census_keeps_matching_processes_and_skips_unreadable_ones(monkeypatch):
    monkeypatch.setattr(observation, "process_ids", lambda: {1, 2, 3})

    def command(pid):
        if pid == 3:
            raise OSError("process exited or denied access")
        return {
            1: "python -m worktree_manager mux-daemon run --root=example",
            2: "python -m worktree_manager daemons status",
        }[pid]

    monkeypatch.setattr(observation, "process_command_line", command)
    assert observation.mux_daemon_pids() == {1}


def test_windows_status_uses_native_census_and_preserves_root_scope(monkeypatch, tmp_path):
    monkeypatch.setattr(daemons_status, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(observation, "mux_daemon_pids", lambda: {41})
    monkeypatch.setattr(
        daemons_status.subprocess, "run",
        lambda *args, **kwargs: pytest.fail("Windows status must not start PowerShell"),
    )
    root = tmp_path / "example"
    monkeypatch.setattr(
        observation, "process_command_line", lambda pid: f"python --root={root}",
    )
    assert daemons_status._iter_status_pids() == {41}
    assert daemons_status._status_pid_matches_root(41, root=root)
    assert not daemons_status._status_pid_matches_root(41, root=tmp_path / "other")
    monkeypatch.setattr(observation, "process_command_line", lambda pid: "")
    assert not daemons_status._status_pid_matches_root(41, root=root)


def test_windows_owner_refusal_never_becomes_a_match(monkeypatch):
    monkeypatch.setattr(daemons_status, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(observation, "current_user_sid", lambda: "")
    monkeypatch.setattr(observation, "process_user_sid", lambda pid: "")
    assert not daemons_status._pid_owned_by_current_user(41)


def test_windows_birth_read_avoids_platform_discovery_and_fails_closed(monkeypatch):
    monkeypatch.setattr(daemons_status, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        daemons_status, "process_start_time",
        lambda pid: pytest.fail("Windows birth lookup must not trigger platform discovery"),
    )
    monkeypatch.setattr(observation, "process_start_time", lambda pid: "native-birth")
    assert daemons_status._process_start_time(41) == "native-birth"

    def unavailable(pid):
        raise OSError("native birth unavailable")

    monkeypatch.setattr(observation, "process_start_time", unavailable)
    assert daemons_status._process_start_time(41) is None


def test_native_owner_refusal_closes_the_observation_handle(monkeypatch):
    from zdd.private_protocol import PrivateIPCError, Refusal

    closed = []

    def refused(handle):
        raise PrivateIPCError(Refusal.PRINCIPAL)

    native = SimpleNamespace(
        k=SimpleNamespace(OpenProcess=lambda *args: 73),
        process_sid=refused, close=closed.append,
    )
    monkeypatch.setattr(observation, "_native", lambda: native)
    with pytest.raises(OSError, match="identity could not be verified"):
        observation.process_user_sid(41)
    assert closed == [73]
