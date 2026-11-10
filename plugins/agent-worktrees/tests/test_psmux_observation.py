"""Cold-stop proof never relies on a missing registration or a failed CLI."""

from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from agent_worktrees import launch_seed_exec, reclaim, resume_seed, sessions
from agent_worktrees import windows_mux_processes as native
from agent_remote_driver import mux_inventory as reader
from agent_remote_driver.mux_observation import observe_psmux as observe_native_psmux
from agent_remote_driver.mux_observation_types import ObservationUnavailable as MuxObservationUnavailable


@pytest.fixture
def registered(tmp_path, monkeypatch):
    from agent_remote_driver import mux_observation

    monkeypatch.setattr(
        mux_observation, "observe_psmux",
        lambda *a, **kw: mux_observation.Observation("unknown", "pane-unobserved"),
    )
    monkeypatch.setenv("PSMUX_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(native, "servers", lambda: {101: "123"})
    (tmp_path / "wt-other.pid").write_text("101:123")
    (tmp_path / "wt-other.port").write_text("12345")
    (tmp_path / "wt-other.key").write_text("a" * 16)
    monkeypatch.setattr(reader, "_query", lambda *a: ("wt-other", 1, 101))
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("PSMux CLI execution"))
    return tmp_path


def test_native_absence_allows_cold_resume_but_live_mux_does_not(registered, monkeypatch):
    monkeypatch.setattr(sessions.platform, "system", lambda: "Windows")
    monkeypatch.setattr(reclaim, "resolve_bound_copilots", lambda **kw: [])
    for worktree_id, expected_live in (("fixture", False), ("other", True)):
        record = SimpleNamespace(worktree_id=worktree_id, sessions=[])
        verdict = sessions.verify_worktree_active(record)
        assert verdict.probes_ok and verdict.mux_probe_ok
        assert verdict.active is expected_live
        assert (resume_seed.cold_start_error(verdict) is None) is not expected_live
        assert (launch_seed_exec._final_cold_error(record) is None) is not expected_live


def test_confirmed_mux_absence_does_not_ignore_a_bound_copilot(registered, monkeypatch):
    monkeypatch.setattr(sessions.platform, "system", lambda: "Windows")
    monkeypatch.setattr(reclaim, "resolve_bound_copilots", lambda **kw: [
        {"session_id": "live-session", "homing": "bare"},
    ])
    record = SimpleNamespace(worktree_id="fixture", sessions=[])
    verdict = sessions.verify_worktree_active(record)
    assert verdict.probes_ok and not verdict.mux_live and verdict.bare
    assert resume_seed.cold_start_error(verdict) is not None
    assert "seed remains staged" in launch_seed_exec._final_cold_error(record)


@pytest.mark.parametrize("failure", [TimeoutError(), ConnectionRefusedError(), OSError()])
def test_failed_native_query_cannot_admit_a_cold_prompt(registered, monkeypatch, failure):
    def fail(*args):
        raise failure

    monkeypatch.setattr(reader, "_query", fail)
    monkeypatch.setattr(sessions.platform, "system", lambda: "Windows")
    monkeypatch.setattr(reclaim, "resolve_bound_copilots", lambda **kw: [])
    verdict = sessions.verify_worktree_active(SimpleNamespace(worktree_id="fixture", sessions=[]))
    assert not verdict.probes_ok and not verdict.mux_probe_ok
    assert "seed remains staged" in resume_seed.cold_start_error(verdict)


@pytest.mark.parametrize("filename,contents", [
    ("wt-other.pid", "101:999"), ("wt-other.pid", "101"),
    ("wt-other.port", None), ("wt-other.port", "0"), ("wt-other.key", None),
    ("wt-other.key", "a" * 16 + "\nkill-server"),
])
def test_missing_or_changed_live_registration_stays_unknown(registered, filename, contents):
    path = registered / filename
    if contents is None:
        path.unlink()
    else:
        path.write_text(contents)
    with pytest.raises(MuxObservationUnavailable):
        reader.observe()


def test_legacy_registration_without_pid_allows_confirmed_absence(registered, monkeypatch):
    from agent_remote_driver import mux_observation

    (registered / "wt-other.pid").unlink()
    monkeypatch.setattr(mux_observation, "observe_psmux", observe_native_psmux)
    assert reader.observe_registrations() == {"wt-other": ("wt-other", 1)}
    assert mux_observation.observe_psmux("wt-new", data_dir=registered).status == "absent"
    assert not (registered / "wt-other.pid").exists()


def test_mixed_legacy_and_current_servers_keep_complete_coverage(registered, monkeypatch):
    monkeypatch.setattr(native, "servers", lambda: {101: "123", 202: "456"})
    (registered / "__warm__.port").write_text("12346")
    (registered / "__warm__.key").write_text("b" * 16)
    monkeypatch.setattr(
        reader, "_query",
        lambda port, *a: ("wt-other", 1, 101) if port == 12345 else ("__warm__", 0, 202),
    )
    assert reader.observe() == {"wt-other": 1, "__warm__": 0}
    (registered / "__warm__.key").unlink()
    with pytest.raises(MuxObservationUnavailable):
        reader.observe()


@pytest.mark.parametrize("legacy_live", [False, True])
@pytest.mark.parametrize("uncovered_live", [False, True])
def test_refused_legacy_hint_requires_complete_authenticated_coverage(
    registered, monkeypatch, legacy_live, uncovered_live,
):
    if legacy_live:
        (registered / "wt-other.pid").unlink()
    servers = {101: "123", 202: "456"} if uncovered_live else {101: "123"}
    monkeypatch.setattr(native, "servers", lambda: servers)
    (registered / "exited.port").write_text("12346")
    (registered / "exited.key").write_text("b" * 16)

    def query(port, *args):
        if port == 12346:
            raise ConnectionRefusedError()
        return "wt-other", 1, 101

    monkeypatch.setattr(reader, "_query", query)
    if uncovered_live:
        with pytest.raises(MuxObservationUnavailable, match="missing its registration"):
            reader.observe()
    else:
        assert reader.observe_registrations() == {"wt-other": ("wt-other", 1)}
    assert (registered / "exited.port").read_text() == "12346"


@pytest.mark.parametrize("changed", ["birth", "new-server", "registration", "pid-published"])
def test_legacy_registration_changes_never_prove_absence(registered, monkeypatch, changed):
    (registered / "wt-other.pid").unlink()

    def query(*args):
        if changed == "birth":
            monkeypatch.setattr(native, "servers", lambda: {101: "999"})
        elif changed == "new-server":
            monkeypatch.setattr(native, "servers", lambda: {101: "123", 202: "456"})
        elif changed == "registration":
            (registered / "wt-other.port").write_text("12346")
        else:
            (registered / "wt-other.pid").write_text("101:123")
        return "wt-other", 1, 101

    monkeypatch.setattr(reader, "_query", query)
    with pytest.raises(MuxObservationUnavailable):
        reader.observe()


@pytest.mark.parametrize("reply", [
    ("wt-other", 1, 202), ("renamed", 1, 101),
])
def test_wrong_or_renamed_server_cannot_establish_absence(registered, monkeypatch, reply):
    monkeypatch.setattr(reader, "_query", lambda *a: reply)
    with pytest.raises(MuxObservationUnavailable):
        reader.observe()


def test_namespaced_registration_preserves_live_session_name(registered, monkeypatch):
    for extension in ("pid", "port", "key"):
        (registered / f"wt-other.{extension}").rename(registered / f"example__wt-fixture.{extension}")
    monkeypatch.setattr(reader, "_query", lambda *a: ("wt-fixture", 1, 101))
    monkeypatch.setattr(sessions.platform, "system", lambda: "Windows")
    assert reader.observe_registrations() == {"example__wt-fixture": ("wt-fixture", 1)}
    assert reader.observe() == {"wt-fixture": 1}
    assert sessions.mux_status_many(["fixture"], strict=True)["fixture"].exists


def test_same_session_name_in_multiple_namespaces_remains_unknown(registered, monkeypatch):
    monkeypatch.setattr(native, "servers", lambda: {101: "123", 202: "456"})
    (registered / "example__wt-other.pid").write_text("202:456")
    (registered / "example__wt-other.port").write_text("12346")
    (registered / "example__wt-other.key").write_text("b" * 16)
    monkeypatch.setattr(reader, "_query", lambda port, *a: ("wt-other", 1, 101 if port == 12345 else 202))
    with pytest.raises(MuxObservationUnavailable, match="ambiguous"):
        reader.observe()


def test_namespace_qualified_absence_preserves_complete_coverage_and_legacy_api(registered, monkeypatch):
    from agent_remote_driver import mux_observation

    monkeypatch.setattr(native, "servers", lambda: {101: "123", 202: "456"})
    (registered / "example__wt-other.pid").write_text("202:456")
    (registered / "example__wt-other.port").write_text("12346")
    (registered / "example__wt-other.key").write_text("b" * 16)
    monkeypatch.setattr(reader, "_query", lambda port, *a: ("wt-other", 1, 101 if port == 12345 else 202))
    monkeypatch.setattr(mux_observation, "observe_psmux", observe_native_psmux)
    monkeypatch.setenv("COPILOT_DRIVER_PSMUX_NAMESPACE", "another")
    assert reader.observe(qualified=True) == {"wt-other": 1, "example__wt-other": 1}
    assert mux_observation.observe_psmux("wt-other", data_dir=registered).status == "absent"
    assert mux_observation.observe_psmux_many(["wt-other"])["wt-other"].status == "absent"
    with pytest.raises(MuxObservationUnavailable, match="ambiguous"):
        reader.observe()


def test_orphan_and_new_server_cannot_be_omitted(registered, monkeypatch):
    for snapshots in ([{101: "123", 202: "456"}], [{101: "123"}, {101: "123", 202: "456"}]):
        values = iter(snapshots)
        monkeypatch.setattr(native, "servers", lambda: next(values))
        with pytest.raises(MuxObservationUnavailable):
            reader.observe()


def test_registration_change_refuses(registered, monkeypatch):
    def query(*args):
        (registered / "wt-other.port").write_text("12346")
        return "wt-other", 1, 101

    monkeypatch.setattr(reader, "_query", query)
    with pytest.raises(MuxObservationUnavailable):
        reader.observe()


def test_duplicate_reported_pid_refuses_without_pid_sentinels(registered, monkeypatch):
    (registered / "wt-other.pid").unlink()
    (registered / "renamed.port").write_text("12346")
    (registered / "renamed.key").write_text("b" * 16)
    monkeypatch.setattr(
        reader, "_query",
        lambda port, *a: ("wt-other" if port == 12345 else "renamed", 1, 101),
    )
    with pytest.raises(MuxObservationUnavailable, match="identity is ambiguous"):
        reader.observe()


@pytest.mark.parametrize("stale_registry", [False, True])
def test_no_native_servers_proves_absence_even_without_a_registry(tmp_path, monkeypatch, stale_registry):
    root = tmp_path / "absent"
    if stale_registry:
        root.mkdir()
        (root / "exited.port").write_text("12345")
        (root / "exited.key").write_text("a" * 16)
    monkeypatch.setenv("PSMUX_DATA_DIR", str(root))
    monkeypatch.setattr(native, "servers", lambda: {})
    monkeypatch.setattr(reader, "_query", lambda *a: pytest.fail("No live server to query"))
    assert reader.observe() == {}


def test_relative_data_directory_refuses_before_native_observation(monkeypatch):
    monkeypatch.setenv("PSMUX_DATA_DIR", "relative-mux-state")
    monkeypatch.setattr(native, "servers", lambda: pytest.fail("relative root reached native observation"))
    with pytest.raises(MuxObservationUnavailable, match="must be absolute"):
        reader.observe()


@pytest.mark.parametrize("mode,expected", [
    ("server -s fixture", {101: "123"}), ("attach-session -t fixture", {}),
    ("-L example attach-session -t fixture", {}),
    ("-L example new-session -d -s fixture", {}),
    ("-L example set-option -t fixture status on", {}),
])
def test_native_census_distinguishes_clients_without_executing_them(monkeypatch, mode, expected):
    monkeypatch.setattr(native, "_processes", lambda: [(101, "psmux.exe")])
    monkeypatch.setattr(native.locks, "process_start_time", lambda pid: "123")
    monkeypatch.setattr(native.windows_process_census, "command_line", lambda pid: '"C:\\Program Files\\psmux.exe" ' + mode)
    assert native.servers() == expected


@pytest.mark.parametrize("command,times", [
    (None, ["123", "123"]),
    ("psmux server", ["123", "456"]),
    ("psmux -L other server", ["123", "123"]),
])
def test_native_census_refuses_unreadable_reused_or_unknown_processes(monkeypatch, command, times):
    monkeypatch.setattr(native, "_processes", lambda: [(101, "psmux.exe")])
    values = iter(times)
    monkeypatch.setattr(native.locks, "process_start_time", lambda pid: next(values))
    monkeypatch.setattr(native.windows_process_census, "command_line", lambda pid: command)
    with pytest.raises(MuxObservationUnavailable):
        native.servers()


@pytest.mark.parametrize("error", [18, 5])
def test_native_enumeration_checks_completion_and_closes_its_handle(monkeypatch, error):
    closed = []

    def first(handle, pointer):
        pointer._obj.pid = 101
        pointer._obj.image = "PSMux.exe"
        return True

    kernel = SimpleNamespace(
        CreateToolhelp32Snapshot=lambda *a: 17,
        Process32FirstW=first,
        Process32NextW=lambda *a: False,
        CloseHandle=lambda handle: closed.append(handle),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: kernel, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error, raising=False)
    if error == 18:
        assert native._processes() == [(101, "psmux.exe")]
    else:
        with pytest.raises(MuxObservationUnavailable):
            native._processes()
    assert closed == [17]


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows Toolhelp enumeration")
def test_native_enumeration_observes_current_process_without_a_subprocess(monkeypatch):
    monkeypatch.setattr(native, "_IMAGES", {"python.exe"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("native enumeration spawned a child"))
    assert any(pid == os.getpid() for pid, _name in native._processes())


def test_deadline_and_registration_byte_limit_preserve_uncertainty(registered, monkeypatch):
    with pytest.raises(MuxObservationUnavailable):
        reader.observe(timeout=0)
    (registered / "wt-other.key").write_text("a" * 4097)
    with pytest.raises(MuxObservationUnavailable):
        reader.observe()


@pytest.mark.parametrize("reply", [
    b"OK\nwt-fixture:2:101\n", b"OK\r\nwt-fixture:2:101\r\n",
    b"ERROR: Invalid session key\n", b"OK\n", b"wt-fixture:2:101\n",
    b"OK\nwt-fixture:x:101\n", b"OK\nwt-fixture:2:101\nextra\n", b"x" * 4097,
    b"OK\n\xff:2:101\n",
])
def test_fixed_read_only_wire_contract_requires_auth_and_complete_reply(reply, tmp_path, monkeypatch):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(3)
    requests = []
    errors = []

    def serve():
        try:
            with listener:
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(3)
                    request = bytearray()
                    while block := connection.recv(1024):
                        request.extend(block)
                    requests.append(bytes(request))
                    connection.sendall(reply)
        except OSError as exc:
            errors.append(exc)

    worker = threading.Thread(target=serve)
    worker.start()
    try:
        if reply in (b"OK\nwt-fixture:2:101\n", b"OK\r\nwt-fixture:2:101\r\n"):
            (tmp_path / "wt-fixture.port").write_text(str(listener.getsockname()[1]))
            (tmp_path / "wt-fixture.key").write_text("a" * 16)
            monkeypatch.setattr(native, "servers", lambda: {101: "123"})
            assert reader.observe_registrations(data_dir=tmp_path) == {"wt-fixture": ("wt-fixture", 2)}
        else:
            with pytest.raises(MuxObservationUnavailable):
                reader._query(listener.getsockname()[1], "a" * 16, reader.time.monotonic() + 2)
    finally:
        worker.join(timeout=4)
        listener.close()
    assert not worker.is_alive() and not errors
    assert requests == [b"AUTH " + b"a" * 16 + b"\n" + reader._QUERY]
