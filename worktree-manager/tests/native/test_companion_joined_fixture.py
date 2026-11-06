"""Fixture observation failures cannot manufacture Companion application failures."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event
from types import SimpleNamespace

import pytest

from agent_procutil import no_window_kwargs
from _companion_joined_fixtures import native_state_reader
from test_companion_bridge_codec import OWNER
from test_companion_bridge_session_source import membership_seed, semantic_peer as semantic_peer
from test_companion_joined_tmux import source_entrypoint
from test_companion_joined_psmux import live_reader_pids, owner_commands_settled
from worktree_manager import agenda_transport, companion_bridge_source, mux_companion


@pytest.mark.skipif(os.name != "nt", reason="Native Windows delete-sharing semantics")
def test_native_state_reader_shares_publisher_delete_access_and_complete_reads(
    tmp_path: Path,
) -> None:
    import ctypes
    from ctypes import wintypes

    target, replacement = tmp_path / "state.json", tmp_path / "next.json"
    target.write_text('{"revision":1}', encoding="utf-8")
    replacement.write_text('{"revision":2}', encoding="utf-8")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    publisher = kernel.CreateFileW(str(target), 0x10000, 0x7, None, 3, 0x80, None)
    assert publisher != wintypes.HANDLE(-1).value, ctypes.WinError(ctypes.get_last_error())
    try:
        with pytest.raises(PermissionError):
            target.open("rb")
        with native_state_reader(target) as stream:
            assert json.load(stream) == {"revision": 1}
    finally:
        assert kernel.CloseHandle(publisher)
    replacement.replace(target)
    with native_state_reader(target) as stream:
        assert json.load(stream) == {"revision": 2}


@pytest.mark.parametrize("failure", [
    FileNotFoundError("not published"), PermissionError("access denied"),
    json.JSONDecodeError("invalid state", "", 0),
])
def test_native_state_observation_only_defers_a_missing_publication(
    tmp_path: Path, failure: Exception,
) -> None:
    source = ast.parse(Path(__file__).with_name("test_companion_joined_psmux.py").read_text())
    state = next(node for node in ast.walk(source)
                 if isinstance(node, ast.FunctionDef) and node.name == "app_state")

    def reader(_path):
        raise failure

    namespace = {
        "Any": object, "json": json, "native_state_reader": reader,
        "peer": {"checkout": tmp_path / "checkout"},
    }
    exec(compile(ast.Module(body=[state], type_ignores=[]), "<native state>", "exec"), namespace)
    if isinstance(failure, FileNotFoundError):
        assert namespace["app_state"]() == {}
    else:
        with pytest.raises(type(failure)) as caught:
            namespace["app_state"]()
        assert caught.value is failure


@pytest.mark.parametrize("profile", [False, True])
@pytest.mark.parametrize("failed", [False, True])
def test_membership_seed_diagnostics_preserve_one_call_and_original_result(
    profile: bool, failed: bool,
) -> None:
    manager, result, failure = object(), object(), ValueError("capture-budget-exhausted")
    calls, reports = [], []

    def source_for(actual_manager, owner):
        calls.append((actual_manager, owner))
        if failed:
            raise failure
        return result

    if failed:
        with pytest.raises(ValueError) as caught:
            membership_seed(manager, OWNER, source_for, reports.append if profile else None)
        assert caught.value is failure
    else:
        assert membership_seed(
            manager, OWNER, source_for, reports.append if profile else None,
        ) is result
    assert calls == [(manager, OWNER)]
    assert bool(reports) is profile
    if profile:
        assert len(reports) == 1
        assert ("outcome=error" if failed else "outcome=seeded") in reports[0]
        assert "wall_ms=" in reports[0] and "thread_ms=" in reports[0]


@pytest.mark.parametrize(
    "platform,code,retry",
    [("nt", 32, True), ("nt", 33, True), ("nt", 5, False), ("posix", 32, False)],
)
def test_observer_retries_only_native_sharing_without_advancing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    platform: str, code: int, retry: bool,
) -> None:
    monkeypatch.setattr(mux_companion, "_load_current_worktree", lambda *_args, **_kw: SimpleNamespace(
        worktree={}, sessions=[], bundle={},
    ))
    monkeypatch.setattr(mux_companion.ec, "engine_base_command", lambda: ["fixture"])
    monkeypatch.setattr(companion_bridge_source, "bridge_command", lambda _root: ["fixture"])
    source_entrypoint({"checkout": tmp_path / "checkout"}, monkeypatch)
    parsed = ast.parse((tmp_path / "native-app.py").read_text(encoding="utf-8"))
    mount = next(node for node in parsed.body if isinstance(node, ast.FunctionDef)
                 and node.name == "observed_mount")
    callbacks = []
    timeline = SimpleNamespace(
        size=(80, 20), window=SimpleNamespace(follow_live=True, events=[], sources={}),
        expanded=False, value="membership", query_one=lambda _kind: SimpleNamespace(highlighted=0),
    )
    app = SimpleNamespace(
        size=(80, 24), focused=None, query_one=lambda _kind: timeline,
        set_interval=lambda _seconds, callback: callbacks.append(callback),
        _bridge_attachment=SimpleNamespace(_members={}),
    )
    namespace = {
        "Path": Path, "json": json, "os": SimpleNamespace(name=platform),
        "mount": lambda _app: None, "SubwayTimeline": object,
    }
    exec(compile(ast.Module(body=[mount], type_ignores=[]), "<fixture observer>", "exec"), namespace)
    namespace["observed_mount"](app)
    real_replace, calls = Path.replace, []

    def replace(path: Path, target: Path) -> Path:
        calls.append(path)
        if len(calls) == 1:
            error = PermissionError("synthetic native sharing refusal")
            error.winerror = code
            raise error
        return real_replace(path, target)

    monkeypatch.setattr(Path, "replace", replace)
    target = tmp_path / "native-state.json"
    if not retry:
        with pytest.raises(PermissionError, match="native sharing refusal"):
            callbacks[0]()
        assert not target.exists()
        return
    callbacks[0]()
    assert not target.exists()
    callbacks[0]()
    assert json.loads(target.read_text(encoding="utf-8"))["app_cells"] == [80, 24]
    callbacks[0]()
    assert len(calls) == 2


def test_native_reader_selection_includes_actual_agenda_command_and_rejects_reuse(
    tmp_path: Path,
) -> None:
    agenda = agenda_transport.feed_argv(
        "example", "selected", "head-a", engine_command=[sys.executable, "-m", "agent_worktrees"],
    )
    trace = tmp_path / "children.jsonl"
    trace.write_text("\n".join(map(json.dumps, [
        {"pid": 1, "birth": 101, "argv": ["worktree-observations", "--follow"]},
        {"pid": 2, "birth": 102, "argv": agenda},
        {"pid": 4, "birth": 104, "argv": ["worktree-observation-history"]},
        {"pid": 5, "birth": 1, "argv": agenda},
        {"pid": 6, "birth": 106, "argv": agenda},
    ])), encoding="utf-8")
    requests = [
        {"pid": 3, "birth": 103, "path": "/session-observations/child?follow=true"},
        {"pid": 7, "birth": 107, "path": "/session-observation-history/child"},
    ]
    locks = SimpleNamespace(
        pid_alive=lambda pid: pid != 6, process_start_time=lambda pid: 100 + pid,
    )
    assert live_reader_pids(trace, requests, locks) == {1, 2, 3}


@pytest.mark.parametrize("recorded_birth,pinned_birth,exited,pid_alive,current_birth,settled", [
    ("100", 100, True, True, "100", True),
    ("100", 100, False, True, "100", False),
    ("100", 200, True, True, "100", False),
    ("100", None, False, False, None, True),
    ("100", None, False, True, None, False),
    ("100", None, False, True, "200", True),
    (None, None, False, True, "100", False),
    (None, 100, True, True, "100", False),
])
def test_native_owner_exit_uses_retained_matching_handle_not_pid_existence(
    tmp_path: Path, recorded_birth: str | None, pinned_birth: int | None, exited: bool, pid_alive: bool,
    current_birth: str | None, settled: bool,
) -> None:
    trace = tmp_path / "children.jsonl"
    trace.write_text(json.dumps({"pid": 7, "birth": recorded_birth, "argv": ["agenda-feed"]}) + "\n")
    client = SimpleNamespace(birth=lambda _handle: pinned_birth, exited=lambda _handle: exited)
    locks = SimpleNamespace(pid_alive=lambda _pid: pid_alive, process_start_time=lambda _pid: current_birth)
    pinned = {7: 42} if pinned_birth is not None else {}
    assert owner_commands_settled(trace, locks, client, pinned) is settled


def test_private_bridge_membership_cli_completes_before_client_composition(
    semantic_peer: dict, tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            *companion_bridge_source.bridge_command(str(tmp_path)),
            "observe-owner", "--owner-ref", OWNER, "--contract-version", "1", "--json",
        ],
        env=dict(os.environ, AGENT_BRIDGE_NO_SELFPROVISION="1"),
        capture_output=True, encoding="utf-8", timeout=15, **no_window_kwargs(),
    )
    diagnostic = semantic_peer["diagnostic"]
    assert result.returncode == 0, (
        result.stdout, result.stderr,
        diagnostic.read_text(encoding="utf-8") if diagnostic.exists() else None,
    )
    assert json.loads(result.stdout)["kind"] == "snapshot"
    stream = companion_bridge_source.MembershipCommands(
        OWNER, cwd=str(tmp_path), project_root=str(tmp_path),
    ).frames(Event(), follow=True)
    try:
        assert next(stream)["kind"] == "snapshot"
        assert next(stream)["kind"] == "heartbeat"
    finally:
        stream.close()


@pytest.mark.parametrize("filename", ["test_companion_joined_psmux.py", "test_companion_app_psmux.py"])
@pytest.mark.parametrize("observed_at", [14.9, 15.0, 16.0])
def test_native_client_deadline_is_checked_after_the_blocking_poll(
    filename: str, observed_at: float, tmp_path: Path,
) -> None:
    source = ast.parse(Path(__file__).with_name(filename).read_text(encoding="utf-8"))
    wait = next(node for node in ast.walk(source)
                if isinstance(node, ast.FunctionDef) and node.name == "wait_for")
    clock = [5.0 if filename == "test_companion_joined_psmux.py" else 0.0]
    client = SimpleNamespace(
        process=SimpleNamespace(process=1), output=b"", read_error=None, exited=lambda _handle: False,
    )
    namespace = {
        "time": SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda _delay: None),
        "client": client, "peer": {"failures": []}, "semantic_peer": {
            "failures": [], "diagnostic": tmp_path / "absent",
        }, "screen": lambda: "", "app_state": lambda: {}, "started": tmp_path / "absent",
        "cli_pid": None, "diagnostic": lambda: "late observation", "json": json, "pytest": pytest,
    }
    exec(compile(ast.Module(body=[wait], type_ignores=[]), "<native client wait>", "exec"), namespace)

    def observe() -> bool:
        clock[0] = observed_at
        return True

    if observed_at < 15:
        namespace["wait_for"](
            observe, **({"deadline": 15} if filename == "test_companion_joined_psmux.py" else {}),
        )
    else:
        with pytest.raises(pytest.fail.Exception):
            namespace["wait_for"](
                observe, **({"deadline": 15} if filename == "test_companion_joined_psmux.py" else {}),
            )


def test_native_cleanup_timeout_does_not_query_the_departed_observer(tmp_path: Path) -> None:
    source = ast.parse(Path(__file__).with_name("test_companion_joined_psmux.py").read_text())
    wait = next(node for node in ast.walk(source)
                if isinstance(node, ast.FunctionDef) and node.name == "wait_for")
    clock = [0.0]

    def departed_screen() -> str:
        raise AssertionError("observer pane no longer exists")

    def not_settled() -> bool:
        clock[0] = 15.0
        return False

    namespace = {
        "time": SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda _delay: None),
        "client": SimpleNamespace(
            process=SimpleNamespace(process=1), output=b"", read_error=None, exited=lambda _handle: False,
        ),
        "peer": {"failures": []}, "semantic_peer": {
            "failures": [], "diagnostic": tmp_path / "absent",
        },
        "screen": departed_screen, "app_state": lambda: {}, "started": tmp_path / "absent",
        "cli_pid": None, "json": json, "pytest": pytest,
    }
    exec(compile(ast.Module(body=[wait], type_ignores=[]), "<native cleanup wait>", "exec"), namespace)
    with pytest.raises(pytest.fail.Exception, match='"observer_exited": true'):
        namespace["wait_for"](not_settled, describe=lambda: {
            "observer_exited": True, "owner_commands_settled": False, "bridge_subscribers": 1,
        })


@pytest.mark.parametrize("observed_at", [14.9, 15.0, 16.0])
def test_startup_comparison_cannot_accept_a_late_banner(observed_at: float) -> None:
    source = ast.parse(Path(__file__).with_name(
        "test_companion_python_startup.py",
    ).read_text(encoding="utf-8"))
    loop = next(node for node in ast.walk(source) if isinstance(node, ast.While))
    clock, properties = [0.0], []

    def mux(*args: str) -> SimpleNamespace:
        if args[0] == "capture-pane":
            clock[0] = observed_at
        return SimpleNamespace(
            returncode=0, stdout="INERT PYTHON STARTED" if args[0] == "capture-pane" else "2",
        )

    namespace = {
        "time": SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda _delay: None),
        "deadline": 15, "began": 0, "use_mux": True, "cli_pid": None, "screen": "",
        "session": "example",
        "client": SimpleNamespace(
            process=SimpleNamespace(process=1, pid=1), output=b"", read_error=None,
            exited=lambda _handle: False, pin_descendants=lambda: {2: ("python", 2)},
            birth=lambda _handle: 100, process_state=lambda _pid: {},
        ),
        "pinned": {}, "mux": mux, "started": SimpleNamespace(
            exists=lambda: True, read_text=lambda **_kw: '{"pid":2}',
        ),
        "layer": "mux-venv", "interpreter": "python", "json": json, "pytest": pytest,
        "record_property": lambda *value: properties.append(value),
    }
    code = compile(ast.Module(body=[loop], type_ignores=[]), "<native startup wait>", "exec")
    if observed_at < 15:
        exec(code, namespace)
    else:
        with pytest.raises(pytest.fail.Exception):
            exec(code, namespace)
    assert len(properties) == 1
