"""Cold-stop proof never relies on a missing registration or a failed CLI."""

from __future__ import annotations

import ctypes
import builtins
import io
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any, NoReturn, Protocol

import pytest

from agent_worktrees import launch_seed_exec, psmux_observation, reclaim, resume_seed, sessions
from agent_remote_driver import mux_process_identity, windows_mux_processes as native
from agent_remote_driver import mux_inventory as reader
from agent_remote_driver.mux_observation import observe_psmux as observe_native_psmux
from agent_remote_driver.mux_observation_types import (
    Observation, ObservationUnavailable as MuxObservationUnavailable,
)


class ProcessEntry(Protocol):
    pid: int
    image: str


class ProcessPointer(Protocol):
    @property
    def _obj(self) -> ProcessEntry: ...


@pytest.fixture
def registered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from agent_remote_driver import mux_observation

    monkeypatch.setattr(
        mux_observation, "_exchange",
        lambda *a, **kw: (
            "P\twt-other\t$7:100\t%2\t0\t1\tC:\\fixture\n"
            "C\twt-other\nI\twt-other\t$7:100\t%2\n"
        ),
    )
    monkeypatch.setenv("PSMUX_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(native, "servers", lambda: {101: "123"})
    (tmp_path / "wt-other.pid").write_text("101:123")
    (tmp_path / "wt-other.port").write_text("12345")
    (tmp_path / "wt-other.key").write_text("a" * 16)
    monkeypatch.setattr(reader, "_query", lambda *a: ("wt-other", 1, 101))
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("PSMux CLI execution"))
    return tmp_path


def test_native_absence_allows_cold_resume_but_live_mux_does_not(
    registered: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(
        argv, 0, "wt-other:1\n", "",
    ))
    monkeypatch.setattr(reclaim, "resolve_bound_copilots", lambda **kw: [])
    for worktree_id, expected_live in (("fixture", False), ("other", True)):
        record = SimpleNamespace(worktree_id=worktree_id, worktree_path=str(registered), sessions=[])
        verdict = sessions.verify_worktree_active(record)
        assert verdict.probes_ok and verdict.mux_probe_ok
        assert verdict.active is False
        assert verdict.mux_live is expected_live
        assert (resume_seed.cold_start_error(verdict) is None) is not expected_live
        assert (launch_seed_exec._final_cold_error(record) is None) is not expected_live


def test_confirmed_mux_absence_does_not_ignore_a_bound_copilot(
    registered: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(
        argv, 0, "wt-other:1\n", "",
    ))
    monkeypatch.setattr(reclaim, "resolve_bound_copilots", lambda **kw: [
        {"session_id": "live-session", "homing": "bare"},
    ])
    record = SimpleNamespace(worktree_id="fixture", worktree_path=str(registered), sessions=[])
    verdict = sessions.verify_worktree_active(record)
    assert verdict.probes_ok and not verdict.mux_live and verdict.bare
    assert resume_seed.cold_start_error(verdict) is not None
    error = launch_seed_exec._final_cold_error(record)
    assert error is not None and "seed remains staged" in error


def test_live_target_does_not_require_unrelated_process_identity(
    registered: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable() -> dict[int, str]:
        raise MuxObservationUnavailable("Native mux process identity is unavailable")

    monkeypatch.setattr(native, "servers", unavailable)
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(
        argv, 0, "wt-other:1\n", "",
    ))
    monkeypatch.setattr(reclaim, "resolve_bound_copilots", lambda **kw: [])
    verdict = sessions.verify_worktree_active(
        SimpleNamespace(worktree_id="other", worktree_path=str(registered), sessions=[]),
    )
    assert not verdict.active and verdict.mux_live and verdict.mux_probe_ok and verdict.probes_ok
    assert not sessions.has_mux_session("missing", strict=True, timeout=0.05)


@pytest.mark.parametrize("failure", [TimeoutError(), ConnectionRefusedError(), OSError()])
def test_failed_native_query_cannot_admit_a_cold_prompt(
    registered: Path, monkeypatch: pytest.MonkeyPatch, failure: OSError,
) -> None:
    def fail(*args: object) -> tuple[str, int, int]:
        raise failure

    monkeypatch.setattr(reader, "_query", fail)
    monkeypatch.setattr(subprocess, "run", fail)
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(reclaim, "resolve_bound_copilots", lambda **kw: [])
    verdict = sessions.verify_worktree_active(
        SimpleNamespace(worktree_id="fixture", worktree_path=str(registered), sessions=[]),
    )
    assert not verdict.probes_ok and not verdict.mux_probe_ok
    error = resume_seed.cold_start_error(verdict)
    assert error is not None and "seed remains staged" in error


@pytest.mark.parametrize("filename,contents", [
    ("wt-other.pid", "101:999"), ("wt-other.pid", "101"),
    ("wt-other.port", None), ("wt-other.port", "0"), ("wt-other.key", None),
    ("wt-other.key", "a" * 16 + "\nkill-server"),
])
def test_missing_or_changed_live_registration_stays_unknown(
    registered: Path, filename: str, contents: str | None,
) -> None:
    path = registered / filename
    if contents is None:
        path.unlink()
    else:
        path.write_text(contents)
    with pytest.raises(MuxObservationUnavailable):
        reader.observe()


def test_legacy_registration_without_pid_allows_confirmed_absence(
    registered: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_remote_driver import mux_observation

    (registered / "wt-other.pid").unlink()
    monkeypatch.setattr(mux_observation, "observe_psmux", observe_native_psmux)
    assert reader.observe_registrations() == {"wt-other": ("wt-other", 1)}
    assert mux_observation.observe_psmux("wt-new", data_dir=registered).status == "absent"
    assert not (registered / "wt-other.pid").exists()


def test_mixed_legacy_and_current_servers_keep_complete_coverage(
    registered: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    registered: Path, monkeypatch: pytest.MonkeyPatch, legacy_live: bool, uncovered_live: bool,
) -> None:
    if legacy_live:
        (registered / "wt-other.pid").unlink()
    servers = {101: "123", 202: "456"} if uncovered_live else {101: "123"}
    monkeypatch.setattr(native, "servers", lambda: servers)
    (registered / "exited.port").write_text("12346")
    (registered / "exited.key").write_text("b" * 16)

    def query(port: int, *args: object) -> tuple[str, int, int]:
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
def test_legacy_registration_changes_never_prove_absence(
    registered: Path, monkeypatch: pytest.MonkeyPatch, changed: str,
) -> None:
    (registered / "wt-other.pid").unlink()

    def query(*args: object) -> tuple[str, int, int]:
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
def test_wrong_or_renamed_server_cannot_establish_absence(
    registered: Path, monkeypatch: pytest.MonkeyPatch, reply: tuple[str, int, int],
) -> None:
    monkeypatch.setattr(reader, "_query", lambda *a: reply)
    with pytest.raises(MuxObservationUnavailable):
        reader.observe()


def test_namespaced_registration_preserves_live_session_name(
    registered: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_remote_driver import mux_observation

    for extension in ("pid", "port", "key"):
        (registered / f"wt-other.{extension}").rename(registered / f"example__wt-fixture.{extension}")
    monkeypatch.setattr(reader, "_query", lambda *a: ("wt-fixture", 1, 101))
    monkeypatch.setattr(
        mux_observation, "_exchange",
        lambda *a, **kw: "P\twt-fixture\t$7:100\t%2\t0\t1\tC:\\fixture\nI\twt-fixture\t$7:100\t%2\n",
    )
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    assert reader.observe_registrations() == {"example__wt-fixture": ("wt-fixture", 1)}
    assert reader.observe() == {"wt-fixture": 1}
    assert mux_observation.observe_psmux_many(["wt-fixture"])["wt-fixture"].status == "unknown"
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(
        argv, 0, "wt-fixture:1\n", "",
    ))
    assert sessions.mux_status_many(["fixture"], strict=True)["fixture"].exists


def test_same_session_name_in_multiple_namespaces_remains_unknown(
    registered: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(native, "servers", lambda: {101: "123", 202: "456"})
    (registered / "example__wt-other.pid").write_text("202:456")
    (registered / "example__wt-other.port").write_text("12346")
    (registered / "example__wt-other.key").write_text("b" * 16)
    monkeypatch.setattr(reader, "_query", lambda port, *a: ("wt-other", 1, 101 if port == 12345 else 202))
    with pytest.raises(MuxObservationUnavailable, match="ambiguous"):
        reader.observe()


def test_namespace_qualified_absence_preserves_complete_coverage_and_legacy_api(
    registered: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
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


def test_orphan_and_new_server_cannot_be_omitted(
    registered: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for snapshots in ([{101: "123", 202: "456"}], [{101: "123"}, {101: "123", 202: "456"}]):
        values = iter(snapshots)
        monkeypatch.setattr(native, "servers", lambda: next(values))
        with pytest.raises(MuxObservationUnavailable):
            reader.observe()


def test_registration_change_refuses(registered: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def query(*args: object) -> tuple[str, int, int]:
        (registered / "wt-other.port").write_text("12346")
        return "wt-other", 1, 101

    monkeypatch.setattr(reader, "_query", query)
    with pytest.raises(MuxObservationUnavailable):
        reader.observe()


def test_duplicate_reported_pid_refuses_without_pid_sentinels(
    registered: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
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
def test_no_native_servers_proves_absence_even_without_a_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stale_registry: bool,
) -> None:
    root = tmp_path / "absent"
    if stale_registry:
        root.mkdir()
        (root / "exited.port").write_text("12345")
        (root / "exited.key").write_text("a" * 16)
    monkeypatch.setenv("PSMUX_DATA_DIR", str(root))
    monkeypatch.setattr(native, "servers", lambda: {})
    monkeypatch.setattr(reader, "_query", lambda *a: pytest.fail("No live server to query"))
    assert reader.observe() == {}


def test_relative_data_directory_refuses_before_native_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PSMUX_DATA_DIR", "relative-mux-state")
    monkeypatch.setattr(native, "servers", lambda: pytest.fail("relative root reached native observation"))
    with pytest.raises(MuxObservationUnavailable, match="must be absolute"):
        reader.observe()


@pytest.mark.parametrize("mode,expected", [
    ("server -s fixture", {101: "123"}), ("attach-session -t fixture", {}),
    ("-L example attach-session -t fixture", {}),
    ("-L example new-session -d -s fixture", {}),
    ("-L example set-option -t fixture status on", {}),
    ('"server" "-s" "fixture"', {101: "123"}),
    ('"-L" "example" "attach-session" "-t" "fixture"', {}),
    ('"-L" "example" "new-session" "-d" "-s" "fixture"', {}),
    ('"-L" "example" "set-option" "-t" "fixture" "status" "on"', {}),
])
def test_native_census_distinguishes_clients_without_executing_them(
    monkeypatch: pytest.MonkeyPatch, mode: str, expected: dict[int, str],
) -> None:
    monkeypatch.setattr(native, "_processes", lambda: [(101, "psmux.exe")])
    monkeypatch.setattr(mux_process_identity, "process_start_time", lambda pid: "123")
    monkeypatch.setattr(mux_process_identity, "command_line", lambda pid: '"C:\\Program Files\\psmux.exe" ' + mode)
    assert native.servers() == expected


@pytest.mark.parametrize("command,times", [
    (None, ["123", "123"]),
    ("psmux server", ["123", "456"]),
    ("psmux -L other server", ["123", "123"]),
    ('"psmux.exe" "-L" "other" "server"', ["123", "123"]),
    ('"psmux.exe" "--unknown" "server"', ["123", "123"]),
])
def test_native_census_refuses_unreadable_reused_or_unknown_processes(
    monkeypatch: pytest.MonkeyPatch, command: str | None, times: list[str],
) -> None:
    monkeypatch.setattr(native, "_processes", lambda: [(101, "psmux.exe")])
    values = iter(times)
    monkeypatch.setattr(mux_process_identity, "process_start_time", lambda pid: next(values))
    monkeypatch.setattr(mux_process_identity, "command_line", lambda pid: command)
    monkeypatch.setattr(mux_process_identity, "access", lambda pid: ("live", 0))
    with pytest.raises(MuxObservationUnavailable, match="pid 101"):
        native.servers()


def _census(
    monkeypatch: pytest.MonkeyPatch, readable: dict[int, str], states: dict[int, tuple[str, int]],
) -> None:
    pids = sorted({*readable, *states})
    monkeypatch.setattr(native, "_processes", lambda: [(pid, "psmux.exe") for pid in pids])
    monkeypatch.setattr(
        mux_process_identity, "process_start_time", lambda pid: "1" if pid in readable else None,
    )
    monkeypatch.setattr(mux_process_identity, "command_line", lambda pid: "psmux " + readable[pid])
    monkeypatch.setattr(mux_process_identity, "access", lambda pid: states[pid])
    monkeypatch.setattr(mux_process_identity, "system_start_times", lambda: {202: "9", 303: "8"})


def test_native_census_skips_processes_that_exited_mid_census(monkeypatch: pytest.MonkeyPatch) -> None:
    _census(monkeypatch, {101: "server -s fixture"}, {202: ("gone", 87), 303: ("gone", 0)})
    assert native.servers() == {101: "1"}


def test_native_census_keeps_inaccessible_processes_as_possible_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A medium-integrity monitor cannot open elevated servers; their snapshot
    # identity must still require registration coverage instead of blanking.
    _census(monkeypatch, {101: "attach-session -t fixture"}, {202: ("denied", 5)})
    assert native.servers() == {202: "9"}


@pytest.mark.parametrize("state,snapshot", [(("unknown", 6), {202: "9"}), (("denied", 5), {})])
def test_native_census_names_the_unclassifiable_process(
    monkeypatch: pytest.MonkeyPatch, state: tuple[str, int], snapshot: dict[int, str],
) -> None:
    _census(monkeypatch, {}, {202: state})
    monkeypatch.setattr(mux_process_identity, "system_start_times", lambda: snapshot)
    with pytest.raises(
        MuxObservationUnavailable, match=rf"pid 202 start time is unreadable \({state[0]}, Win32 error {state[1]}\)",
    ):
        native.servers()


@pytest.mark.parametrize("state,skipped", [(("gone", 87), True), (("live", 0), False)])
def test_native_census_recheck_skips_only_verified_exits(
    monkeypatch: pytest.MonkeyPatch, state: tuple[str, int], skipped: bool,
) -> None:
    monkeypatch.setattr(native, "_processes", lambda: [(101, "psmux.exe")])
    values = iter(["123", None])
    monkeypatch.setattr(mux_process_identity, "process_start_time", lambda pid: next(values))
    monkeypatch.setattr(mux_process_identity, "command_line", lambda pid: "psmux attach -t fixture")
    monkeypatch.setattr(mux_process_identity, "access", lambda pid: state)
    if skipped:
        assert native.servers() == {}
    else:
        with pytest.raises(MuxObservationUnavailable, match="pid 101 start time changed"):
            native.servers()


@pytest.mark.parametrize("opened,error,exit_code,expected", [
    (False, 87, None, ("gone", 87)),
    (False, 5, None, ("denied", 5)),
    (False, 6, None, ("unknown", 6)),
    (True, 0, 259, ("live", 0)),
    (True, 0, 0, ("gone", 0)),
    (True, 31, None, ("unknown", 31)),
])
def test_access_maps_native_errors_without_inferring_absence(
    monkeypatch: pytest.MonkeyPatch, opened: bool, error: int, exit_code: int | None,
    expected: tuple[str, int],
) -> None:
    closed: list[int] = []

    def exit_status(handle: int, pointer: Any) -> bool:
        if exit_code is None:
            return False
        pointer._obj.value = exit_code
        return True

    kernel = SimpleNamespace(
        OpenProcess=lambda *a: 17 if opened else 0,
        GetExitCodeProcess=exit_status,
        CloseHandle=lambda handle: closed.append(handle),
    )
    monkeypatch.setattr(mux_process_identity, "_kernel", lambda: kernel)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error, raising=False)
    assert mux_process_identity.access(101) == expected
    assert closed == ([17] if opened else [])


def test_uncovered_inaccessible_process_still_fails_closed(registered: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(native, "servers", lambda: {101: "123", 202: "9"})
    with pytest.raises(MuxObservationUnavailable, match="missing its registration: pid 202"):
        reader.observe()


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows census")
def test_native_snapshot_start_time_matches_process_start_time() -> None:
    assert mux_process_identity.system_start_times()[os.getpid()] == (
        mux_process_identity.process_start_time(os.getpid())
    )
    assert mux_process_identity.access(os.getpid()) == ("live", 0)


@pytest.mark.parametrize("error", [18, 5])
def test_native_enumeration_checks_completion_and_closes_its_handle(
    monkeypatch: pytest.MonkeyPatch, error: int,
) -> None:
    closed: list[int] = []

    def first(handle: int, pointer: ProcessPointer) -> bool:
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
def test_native_enumeration_observes_current_process_without_a_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(native, "_IMAGES", {"python.exe"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("native enumeration spawned a child"))
    assert any(pid == os.getpid() for pid, _name in native._processes())


def test_deadline_and_registration_byte_limit_preserve_uncertainty(
    registered: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
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
def test_fixed_read_only_wire_contract_requires_auth_and_complete_reply(
    reply: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(3)
    requests: list[bytes] = []
    errors: list[OSError] = []

    def serve() -> None:
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
                reader._query(listener.getsockname()[1], "a" * 16, time.monotonic() + 2)
    finally:
        worker.join(timeout=4)
        listener.close()
    assert not worker.is_alive() and not errors
    assert requests == [b"AUTH " + b"a" * 16 + b"\n" + reader._QUERY]


@pytest.mark.parametrize("clients", [None, 0, 2])
def test_session_counts_require_an_observed_count(
    monkeypatch: pytest.MonkeyPatch, clients: int | None,
) -> None:
    from agent_remote_driver import mux_observation

    monkeypatch.setattr(
        mux_observation, "list_psmux",
        lambda: {"wt-fixture": Observation(status="present", clients=clients)},
    )
    expected = None if clients is None else {"wt-fixture": clients}
    assert psmux_observation.list_session_counts() == expected


@pytest.mark.parametrize("mode", ["--observe", "--describe-contract"])
def test_finite_cli_does_not_prepare_help_or_batch_workers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mode: str,
) -> None:
    import argparse
    from agent_remote_driver import mux_observation

    original = builtins.__import__

    def no_parser(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("finite observation constructed its unused help parser")

    def importing(name: str, *args: Any, **kwargs: Any) -> Any:
        if name in {"argparse", "concurrent.futures", "threading"}:
            pytest.fail("finite observation prepared unused help or batch workers")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(sys, "stdin", io.StringIO('{"version":1,"session":"wt-fixture"}'))
    monkeypatch.setattr(mux_observation, "observe_psmux", lambda session: Observation("present"))
    monkeypatch.setattr(argparse, "ArgumentParser", no_parser)
    with monkeypatch.context() as imports:
        imports.setattr(builtins, "__import__", importing)
        assert mux_observation.main([mode]) == 0
    reply = json.loads(capsys.readouterr().out)
    assert reply["contract"] == mux_observation.CONTRACT and reply["version"] == 1
    if mode == "--observe":
        assert reply["status"] == "present"
    else:
        assert reply["operations"] == ["psmux-observe", "psmux-observe-pinned"]


@pytest.mark.parametrize("arguments", [[], ["--unknown"], ["--observe", "--describe-contract"]])
def test_finite_cli_retains_strict_argument_refusal(arguments: list[str]) -> None:
    from agent_remote_driver import mux_observation

    with pytest.raises(SystemExit) as error:
        mux_observation.main(arguments)
    assert error.value.code == 2


@pytest.mark.parametrize("unknown", [False, True])
def test_strict_status_keeps_confirmed_absence_without_repeating_inventory(
    monkeypatch: pytest.MonkeyPatch, unknown: bool,
) -> None:
    from agent_worktrees import psmux_observation as adapter
    from agent_worktrees.sessions import MuxInfo

    rows = {"cold": MuxInfo(exists=False, clients=0)}
    if unknown:
        rows["pending"] = MuxInfo(observed=False)
    monkeypatch.setattr(adapter, "display_status", lambda *a, **k: rows)
    calls: list[float] = []

    def inventory(*, timeout: float) -> dict[str, int]:
        calls.append(timeout)
        return {"wt-cold": 3, "wt-pending": 1}

    monkeypatch.setattr(adapter, "observe", inventory)
    result = adapter.session_status(list(rows), strict=True)
    assert not result["cold"].exists
    assert result["cold"].clients == 0
    assert len(calls) == int(unknown)
    if unknown:
        assert result["pending"].exists
        assert result["pending"].clients == 1
