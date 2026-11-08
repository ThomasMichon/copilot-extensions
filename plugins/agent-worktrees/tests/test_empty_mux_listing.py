"""No default tmux server is empty evidence, not a failed enumeration."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

from agent_worktrees import sessions, status_monitor_runtime
from agent_remote_driver import mux_observation
from agent_worktrees.mux_list_result import (
    MuxObservationUnavailable,
    confirmed_has_session,
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


@pytest.mark.parametrize("message", [NO_SERVER, NO_SOCKET])
def test_strict_presence_recognizes_absence_at_subprocess_boundary(
    monkeypatch: pytest.MonkeyPatch, message: str,
) -> None:
    monkeypatch.setattr(sessions.platform, "system", lambda: "Linux")
    calls = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 1, b"", (message + "\n").encode())

    monkeypatch.setattr(subprocess, "run", run)
    assert sessions.has_mux_session("fixture", strict=True, timeout=0.25) is False
    command, kwargs = calls[0]
    assert command == ["tmux", "has-session", "-t", "=wt-fixture"]
    assert kwargs["timeout"] == 0.25
    assert kwargs["capture_output"] is True
    assert len(calls) == 1


@pytest.mark.parametrize("message", [
    "error connecting to /tmp/example (Permission denied)",
    "error connecting to /tmp/example (Connection refused)",
    NO_SOCKET + "\nunknown failure",
    "",
])
def test_strict_presence_keeps_unavailable_probes_unknown(
    monkeypatch: pytest.MonkeyPatch, message: str,
) -> None:
    monkeypatch.setattr(sessions.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, b"", message.encode()),
    )
    with pytest.raises(RuntimeError, match="unavailable"):
        sessions.has_mux_session("fixture", strict=True, timeout=0.25)


@pytest.mark.parametrize("code,message", [
    (1, b""),
    (2, b""),
    (1, b"access denied"),
    (1, b"session not found: wt-fixture"),
])
def test_strict_psmux_presence_keeps_silent_failure_unknown(
    monkeypatch: pytest.MonkeyPatch, code: int, message: bytes,
) -> None:
    with pytest.raises(RuntimeError, match="unavailable"):
        confirmed_has_session(
            "wt-fixture", subprocess.CompletedProcess(["psmux"], code, b"", message), binary="psmux",
        )


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


@pytest.mark.parametrize("suffix", ["bad line", "wt-other:x", "wt-example:2", ":1"])
def test_partial_session_listing_does_not_confirm_absence(
    monkeypatch: pytest.MonkeyPatch, suffix: str,
) -> None:
    monkeypatch.setattr(sessions.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, f"wt-example:1\n{suffix}\n", "",
        ),
    )
    assert sessions._list_mux_sessions() is None
    with pytest.raises(ValueError, match="absence is unconfirmed"):
        sessions._list_mux_sessions(strict=True)


@pytest.mark.parametrize("suffix", [
    "bad line", "wt-other:x:$2:456", "wt-example:2:$2:456",
    ":1:$2:456", "wt-other:1::456", "wt-other:1:$2:invalid",
    "wt-other:1:not-a-session-id:456",
])
def test_partial_monitor_listing_does_not_confirm_absence(
    monkeypatch: pytest.MonkeyPatch, suffix: str,
) -> None:
    monkeypatch.setattr(status_monitor_runtime, "_core_helper", lambda _name, local: local)
    monkeypatch.setattr(
        subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, f"wt-example:1:$1:123\n{suffix}\n", "",
        ),
    )
    assert status_monitor_runtime._monitor_list_sessions("tmux") is None


def test_success_with_diagnostics_is_not_a_complete_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(status_monitor_runtime, "_core_helper", lambda _name, local: local)
    monkeypatch.setattr(sessions.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, "", "one server could not be queried",
        ),
    )
    assert status_monitor_runtime._monitor_list_sessions("tmux") is None
    assert sessions._list_mux_sessions() is None


def test_partial_batch_and_failed_presence_cannot_become_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sessions.platform, "system", lambda: "Linux")

    def run(command, **kwargs):
        if command[1] == "list-sessions":
            return subprocess.CompletedProcess(command, 0, "wt-other:1\nbad line\n", "")
        return subprocess.CompletedProcess(command, 1, b"", b"Permission denied")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(MuxObservationUnavailable, match="observation failed"):
        sessions.mux_status_many(["fixture"])


@pytest.mark.parametrize("error", [
    OSError("synthetic spawn failure"),
    subprocess.TimeoutExpired(["tmux"], 5),
    RuntimeError("synthetic unconfirmed presence"),
])
def test_passive_posix_fallback_normalizes_unavailable_evidence(monkeypatch, error):
    monkeypatch.setattr(sessions.platform, "system", lambda: "Linux")
    monkeypatch.setattr(sessions, "_list_mux_sessions", lambda **kw: None)

    def unavailable(*args, **kwargs):
        raise error

    monkeypatch.setattr(sessions, "has_mux_session", unavailable)
    with pytest.raises(MuxObservationUnavailable):
        sessions.mux_status_many(["fixture"])
    with pytest.raises(type(error)) as raised:
        sessions.mux_status_many(["fixture"], strict=True)
    assert raised.value is error


def test_passive_psmux_observation_never_executes(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import psmux_observation

    def unknown(**kwargs):
        raise MuxObservationUnavailable("PSMux presence is unknown")

    monkeypatch.setattr(psmux_observation, "observe", unknown)
    monkeypatch.setattr(status_monitor_runtime, "_core_helper", lambda _name, local: local)
    monkeypatch.setattr(sessions.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: pytest.fail("passive PSMux CLI execution"),
    )
    monkeypatch.setattr(mux_observation, "list_psmux", lambda: None)
    monkeypatch.setattr(
        mux_observation, "observe_psmux",
        lambda *a, **kw: mux_observation.Observation("unknown", "native-query-unavailable"),
    )
    assert status_monitor_runtime._monitor_list_sessions(r"C:\Tools\PSMux.exe") is None
    assert sessions._list_mux_sessions() is None
    assert sessions._mux_session_activity() == {}
    with pytest.raises(MuxObservationUnavailable):
        sessions.mux_status_many(["fixture"])
    assert not sessions.mux_status_for_display(["fixture"])["fixture"].observed
    with pytest.raises(MuxObservationUnavailable, match="presence is unknown"):
        sessions.mux_status_many(["fixture"], strict=True)


def test_unconfirmed_psmux_cannot_admit_a_cold_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    from agent_worktrees import launch_seed_exec, psmux_observation, reclaim

    monkeypatch.setattr(sessions.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        mux_observation, "observe_psmux",
        lambda *a, **kw: mux_observation.Observation("unknown", "native-query-unavailable"),
    )
    monkeypatch.setattr(reclaim, "resolve_bound_copilots", lambda **kw: [])

    def unavailable(**kwargs):
        raise MuxObservationUnavailable("native server coverage unavailable")

    monkeypatch.setattr(psmux_observation, "observe", unavailable)
    error = launch_seed_exec._final_cold_error(SimpleNamespace(worktree_id="fixture", sessions=[]))
    assert error is not None and "seed remains staged" in error


def test_explicit_strict_presence_uses_native_psmux_evidence(monkeypatch) -> None:
    from agent_worktrees import psmux_observation

    monkeypatch.setattr(sessions.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: pytest.fail("strict PSMux CLI execution"),
    )
    monkeypatch.setattr(psmux_observation, "observe", lambda **kw: {"wt-fixture": 2})
    info = sessions.mux_status_many(["fixture"], strict=True)["fixture"]
    assert info.exists is True and info.clients == 2
    assert sessions.has_mux_session("fixture", strict=True)
    assert not sessions.has_mux_session("missing", strict=True)


def test_explicit_and_display_presence_use_driver_counts_without_cli(monkeypatch) -> None:
    monkeypatch.setattr(sessions.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: pytest.fail("passive PSMux CLI"),
    )
    monkeypatch.setattr(
        mux_observation, "observe_psmux",
        lambda *a, **kw: mux_observation.Observation(
            "present", clients=3, incarnation="$1:100",
            panes=(mux_observation.Pane("%2", False, True, "fixture"),),
        ),
    )
    info = sessions.mux_status_many(["fixture"])["fixture"]
    assert info.exists is True and info.clients == 3
    assert sessions.mux_status_for_display(["fixture"])["fixture"].clients == 3
    assert sessions.has_mux_session("fixture")
    assert sessions.has_mux_session_named("wt-fixture", mux="psmux")
    assert sessions.mux_active_pane_named("wt-fixture", mux="psmux") == "%2"


def test_unknown_native_results_are_display_only_and_never_admit_reclaim(monkeypatch):
    from agent_worktrees import reclaim

    monkeypatch.setattr(sessions.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        mux_observation, "observe_psmux",
        lambda name, **kw: mux_observation.Observation("unknown", "timeout")
        if name == "wt-fixture" else mux_observation.Observation("present", clients=2),
    )
    with pytest.raises(MuxObservationUnavailable):
        sessions.mux_status_many(["fixture", "other"])
    display = sessions.mux_status_for_display(["fixture", "other"])
    assert not display["fixture"].observed
    assert display["other"].observed and display["other"].clients == 2
    target = {"pid": 101, "homing": "mux", "worktree_id": "fixture"}
    monkeypatch.setattr(
        reclaim, "mux_ancestors_of",
        lambda *a: pytest.fail("unknown mux admitted detached-server teardown"),
    )
    assert reclaim._mux_reachability(["fixture"]) == {"fixture": None}
    assert reclaim.filter_stop_unreachable([target], table={}) == []
    assert reclaim.teardown_detached_mux([target], table={}) == []


def test_monitor_uses_driver_identity_and_preserves_partial_unknown(monkeypatch) -> None:
    monkeypatch.setattr(status_monitor_runtime, "_core_helper", lambda _name, local: local)
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: pytest.fail("passive PSMux CLI"),
    )
    observations = {"wt-fixture": mux_observation.Observation(
        "present", clients=3, incarnation="$1:100",
    )}
    monkeypatch.setattr(mux_observation, "list_psmux", lambda: observations)
    assert status_monitor_runtime._monitor_list_sessions("psmux") == {"wt-fixture": (3, "$1:100")}
    observations["wt-other"] = mux_observation.Observation("unknown", "timeout")
    assert status_monitor_runtime._monitor_list_sessions("psmux") is None

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
    presence = subprocess.run(
        [binary, "-S", socket.name, "has-session", "-t", "=wt-fixture"],
        capture_output=True, text=True, cwd=tmp_path, timeout=5, **no_window_kwargs(),
    )
    assert confirmed_has_session("wt-fixture", presence) is False
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
