"""No default tmux server is empty evidence, not a failed enumeration."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

from agent_worktrees import sessions, status_monitor_runtime
from agent_worktrees.mux_list_result import (
    is_empty_tmux_listing,
    parse_monitor_sessions,
    parse_session_counts,
)

pytestmark = pytest.mark.guard

NO_SERVER = "no server running on /tmp/tmux-1000/default"
NO_SOCKET = "error connecting to /tmp/tmux-1000/default (No such file or directory)"


@pytest.mark.parametrize("binary", ["tmux", "/usr/bin/tmux", r"C:\Tools\tmux.exe"])
@pytest.mark.parametrize("message", [NO_SERVER, NO_SOCKET])
def test_known_absence_is_an_empty_listing(binary: str, message: str) -> None:
    result = subprocess.CompletedProcess([binary, "list-sessions"], 1, "", message + "\n")
    assert is_empty_tmux_listing(binary, result)


@pytest.mark.parametrize(
    ("binary", "code", "stdout", "stderr"),
    [
        ("tmux", 1, "", "error connecting to /tmp/example (Permission denied)"),
        ("tmux", 1, "", "error connecting to /tmp/example (Connection refused)"),
        ("tmux", 1, "", "server exited unexpectedly"),
        ("tmux", 127, "", NO_SERVER),
        ("tmux", 1, "wt-example:0\n", NO_SERVER),
        ("tmux", 1, "", "invalid option: example\n" + NO_SERVER),
        ("tmux", 1, "", ""),
        ("psmux", 1, "", NO_SERVER),
        ("other-program", 1, "", NO_SOCKET),
    ],
)
def test_unavailable_or_partial_evidence_is_not_empty(
    binary: str,
    code: int,
    stdout: str,
    stderr: str,
) -> None:
    result = subprocess.CompletedProcess([binary, "list-sessions"], code, stdout, stderr)
    assert not is_empty_tmux_listing(binary, result)


@pytest.mark.parametrize("message", [NO_SERVER, NO_SOCKET])
def test_monitor_and_liveness_agree_on_healthy_absence(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
) -> None:
    monkeypatch.setattr(status_monitor_runtime, "_core_helper", lambda _name, local: local)
    monkeypatch.setattr(sessions.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", message),
    )
    assert status_monitor_runtime._monitor_list_sessions("/usr/bin/tmux") == {}
    assert sessions._list_mux_sessions() == {}


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("mux executable is missing"),
        PermissionError("mux executable cannot be started"),
        subprocess.TimeoutExpired("tmux", 5),
    ],
)
def test_probe_failures_remain_unconfirmed(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    monkeypatch.setattr(status_monitor_runtime, "_core_helper", lambda _name, local: local)
    monkeypatch.setattr(sessions.platform, "system", lambda: "Linux")

    def fail(*args: object, **kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(subprocess, "run", fail)
    assert status_monitor_runtime._monitor_list_sessions("tmux") is None
    assert sessions._list_mux_sessions() is None


def test_unknown_server_error_remains_unconfirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(status_monitor_runtime, "_core_helper", lambda _name, local: local)
    monkeypatch.setattr(sessions.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            1,
            "",
            "error connecting to /tmp/example (Permission denied)",
        ),
    )
    assert status_monitor_runtime._monitor_list_sessions("tmux") is None
    assert sessions._list_mux_sessions() is None


def test_successful_session_data_is_still_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(status_monitor_runtime, "_core_helper", lambda _name, local: local)
    monkeypatch.setattr(sessions.platform, "system", lambda: "Linux")

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        text = "wt-example:1:$1:123\n" if "session_created" in command[-1] else "wt-example:1\n"
        return subprocess.CompletedProcess(command, 0, text, "")

    monkeypatch.setattr(subprocess, "run", run)
    assert status_monitor_runtime._monitor_list_sessions("tmux") == {"wt-example": (1, "$1:123")}
    assert sessions._list_mux_sessions() == {"wt-example": 1}


def test_real_missing_socket_probe_does_not_start_a_server(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("Unix socket probe requires POSIX tmux, not a Windows compatibility shim")
    binary = shutil.which("tmux")
    if binary is None:
        pytest.skip("native tmux is unavailable")
    socket = tmp_path / "unstarted.sock"
    result = subprocess.run(
        [binary, "-S", socket.name, "list-sessions"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=5,
        **no_window_kwargs(),
    )
    assert is_empty_tmux_listing(binary, result), result.stderr
    assert not socket.exists()


def test_parsers_preserve_names_incarnations_and_legacy_bad_count_behavior() -> None:
    assert parse_session_counts("wt:example:1\nbad line\nwt-other:invalid\n") == {
        "wt:example": 1,
        "wt-other": 0,
    }
    assert parse_monitor_sessions("wt:example:1:$1:123\nbad line\nwt-other:x:$2:456\n") == {
        "wt:example": (1, "$1:123"),
        "wt-other": (0, "$2:456"),
    }


def test_both_probes_preserve_captured_headless_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(status_monitor_runtime, "_core_helper", lambda _name, local: local)
    monkeypatch.setattr(sessions.platform, "system", lambda: "Linux")
    calls: list[dict[str, object]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    assert status_monitor_runtime._monitor_list_sessions("tmux") == {}
    assert sessions._list_mux_sessions() == {}
    assert len(calls) == 2
    for kwargs in calls:
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert all(kwargs[key] == value for key, value in no_window_kwargs().items())
