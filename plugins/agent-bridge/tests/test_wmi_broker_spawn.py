"""On-demand daemon boot escapes the caller's job/session (dotfiles#1713/#227).

A daemon booted by a CLI call (or by install/update) must never be *owned* by the
caller: if it stays in the caller's Windows Job object, it is reaped when the CLI
call or the host SSH session exits. ``_spawn_detached_daemon`` uses
``CREATE_BREAKAWAY_FROM_JOB``; when a kill-on-close job forbids that (raising
OSError), it escalates on Windows to a **WMI broker** launch that re-parents the
daemon to ``WmiPrvSE`` -- outside the caller's job/session, with no elevation --
rather than a plain detached child that would be reaped.
"""

from __future__ import annotations

import base64
import os
import subprocess
from unittest.mock import Mock

import pytest

from agent_bridge import __main__ as m
from agent_bridge import service_process_cli


def test_wmi_broker_invokes_encoded_powershell_win32_process_create(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))
    captured: dict = {}

    class _Out:
        returncode = 0
        stdout = "1234\n"

    def _run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Out()

    monkeypatch.setattr(subprocess, "run", _run)
    # Deterministic host resolution: simulate pwsh (PowerShell 7) present on
    # PATH, which is the preferred host over legacy Windows PowerShell 5.1.
    # Reset the memoization cache so this test doesn't inherit a resolution
    # from an earlier test in the same process.
    monkeypatch.setattr(service_process_cli, "_powershell_host_cache", None)
    monkeypatch.setattr(
        service_process_cli.shutil, "which", lambda name: r"C:\pwsh.exe" if name == "pwsh" else None
    )

    ok = m._spawn_via_wmi_broker([r"C:\py.exe", "-m", "agent_bridge", "start"])

    assert ok is True
    assert captured["cmd"][0] == r"C:\pwsh.exe"
    assert "-EncodedCommand" in captured["cmd"]
    encoded = captured["cmd"][captured["cmd"].index("-EncodedCommand") + 1]
    decoded = base64.b64decode(encoded).decode("utf-16-le")
    # The daemon is created via WMI (re-parented off the caller) ...
    assert "Win32_Process" in decoded
    assert "Create" in decoded
    assert "conhost.exe --headless cmd.exe" in decoded
    # ... and the real daemon argv + log redirection are carried through.
    assert "agent_bridge" in decoded
    assert "agent-bridge.log" in decoded


def test_wmi_broker_returns_false_on_nonzero_create(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))

    class _Out:
        returncode = 8  # WMI Create ReturnValue != 0
        stdout = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Out())
    assert m._spawn_via_wmi_broker(["py", "-m", "agent_bridge", "start"]) is False


def test_wmi_broker_returns_false_on_launch_error(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))

    def _boom(*a, **k):
        raise OSError("no powershell")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert m._spawn_via_wmi_broker(["py", "-m", "agent_bridge", "start"]) is False


def _breakaway_denied() -> OSError:
    exc = OSError(13, "Access is denied")
    exc.winerror = 5  # ERROR_ACCESS_DENIED for CREATE_BREAKAWAY_FROM_JOB
    return exc


@pytest.fixture()
def breakaway_refused(monkeypatch, tmp_path):
    """Every Popen raises as a kill-on-close Job does for breakaway."""
    import agent_procutil

    calls: list = []

    def _popen(argv, **kwargs):
        calls.append(kwargs.get("creationflags", 0))
        if kwargs.get("creationflags", 0) & 0x01000000:
            raise _breakaway_denied()
        return Mock(pid=77)

    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(m.sys, "platform", "win32")
    monkeypatch.setattr(agent_procutil, "_is_windows", lambda: True)
    monkeypatch.setattr(agent_procutil.subprocess, "Popen", _popen)
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.setattr(m, "_daemon_launch_argv", lambda: ["py", "-m", "agent_bridge", "start"])
    return calls


def test_spawn_detached_escalates_to_wmi_when_breakaway_refused(monkeypatch, breakaway_refused):
    calls = {"wmi": 0}

    def _fake_wmi(argv):
        calls["wmi"] += 1
        return 1234  # broker succeeds -> no contained fallback

    monkeypatch.setattr(m, "_spawn_via_wmi_broker_pid", _fake_wmi)

    # Must not raise: breakaway denied -> WMI broker succeeds -> return.
    m._spawn_detached_daemon()
    assert calls["wmi"] == 1
    assert len(breakaway_refused) == 1, "no contained spawn once the broker succeeded"


def test_spawn_detached_falls_back_to_contained_when_wmi_fails(
    monkeypatch, breakaway_refused, tmp_path
):
    monkeypatch.setattr(m, "_spawn_via_wmi_broker_pid", lambda argv: None)

    m._spawn_detached_daemon()

    first, second = breakaway_refused
    assert first & 0x01000000
    assert not second & 0x01000000, "the last resort runs inside the caller's Job"
    err = (tmp_path / "agent-bridge-err.log").read_text(encoding="utf-8")
    assert "daemon not started" in err
    assert "contained: breakaway denied and the WMI broker failed" in err


def test_passive_generation_never_falls_back_to_contained(monkeypatch, breakaway_refused):
    import agent_procutil

    monkeypatch.setattr(m, "_spawn_via_wmi_broker_pid", lambda argv: None)

    with pytest.raises(agent_procutil.DaemonSpawnError):
        m._launch_bridge_daemon(
            ["py", "-m", "agent_bridge", "start", "--passive"],
            display="agent-bridge passive generation",
            contained_last_resort=False,
        )
    assert len(breakaway_refused) == 1


def test_passive_generation_returns_brokered_handle(monkeypatch, breakaway_refused):
    monkeypatch.setattr(m, "_spawn_via_wmi_broker_pid", lambda argv: 4321)

    handle = m._launch_bridge_daemon(
        ["py"], display="agent-bridge passive generation", contained_last_resort=False
    )

    assert isinstance(handle, m._BrokeredProcessHandle)
    assert handle.pid == 4321


def test_other_spawn_errors_are_not_masked_by_the_broker(monkeypatch, tmp_path):
    import agent_procutil

    def _missing(*_a, **_k):
        raise FileNotFoundError(2, "missing executable")

    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(m.sys, "platform", "win32")
    monkeypatch.setattr(agent_procutil.subprocess, "Popen", _missing)
    monkeypatch.setattr(
        m, "_spawn_via_wmi_broker_pid",
        lambda argv: pytest.fail("the broker only handles a refused breakaway"),
    )

    with pytest.raises(FileNotFoundError):
        m._spawn_detached_argv(["missing.exe"])


def test_wmi_broker_carries_root_overrides(monkeypatch, tmp_path):
    from agent_bridge.install_paths import CONFIG_DIR_ENV, INSTALL_DIR_ENV

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(m, "_INSTALL_DIR", "root")
    monkeypatch.setenv(INSTALL_DIR_ENV, "root")
    monkeypatch.delenv(CONFIG_DIR_ENV, raising=False)
    captured: dict = {}

    class _Out:
        returncode = 0
        stdout = "99\n"

    def _run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Out()

    monkeypatch.setattr(subprocess, "run", _run)

    assert m._spawn_via_wmi_broker_pid(["py", "-m", "agent_bridge", "start"]) == 99
    encoded = captured["cmd"][captured["cmd"].index("-EncodedCommand") + 1]
    decoded = base64.b64decode(encoded).decode("utf-16-le")
    root = str(tmp_path / "root")
    # WMI children inherit the provider's environment, not the caller's.
    assert f'set "{INSTALL_DIR_ENV}={root}" && ' in decoded
    # The working directory and logs are absolute, never WMI-relative.
    assert f"CurrentDirectory = '{root}'" in decoded
    assert os.path.join(root, "agent-bridge-err.log") in decoded


def test_spawn_detached_happy_path_is_breakaway(monkeypatch, tmp_path):
    import agent_procutil

    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(m, "_daemon_launch_argv", lambda: ["py", "-m", "agent_bridge", "start"])
    monkeypatch.setattr(agent_procutil, "_is_windows", lambda: True)
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)

    seen = {"flags": None, "wmi": 0}

    def _popen_ok(argv, **kwargs):
        seen["flags"] = kwargs.get("creationflags")
        seen["cwd"] = kwargs.get("cwd")
        return object()

    monkeypatch.setattr(agent_procutil.subprocess, "Popen", _popen_ok)
    monkeypatch.setattr(
        m, "_spawn_via_wmi_broker_pid",
        lambda argv: seen.__setitem__("wmi", seen["wmi"] + 1) or 1,
    )

    m._spawn_detached_daemon()
    # Breakaway succeeded -> the WMI broker is never reached.
    assert seen["wmi"] == 0
    # A hidden console for console children, plus Job breakaway.
    assert seen["flags"] == 0x08000000 | 0x01000000
    # Never the caller's ambient cwd -- this is a long-lived supervised
    # daemon rooted at its own durable install dir instead.
    assert seen["cwd"] == str(tmp_path)


def test_watchdog_replacement_uses_delayed_versioned_start(monkeypatch):
    captured = {}
    monkeypatch.setattr(m.sys, "executable", r"C:\venv\python.exe")
    monkeypatch.setattr(
        m, "_spawn_detached_argv", lambda argv: captured.setdefault("argv", argv)
    )

    m._spawn_watchdog_replacement(
        delay=1.5,
        start_args=["start", "--passive", "--idle-shutdown", "30"],
    )

    argv = captured["argv"]
    assert argv[0] == r"C:\venv\python.exe"
    assert argv[1] == "-c"
    assert "agent_bridge" in argv[2]
    assert argv[3] == "1.5"
    assert argv[4:] == ["start", "--passive", "--idle-shutdown", "30"]


def test_watchdog_replacement_promotes_active_passive_generation(monkeypatch):
    captured = {}
    monkeypatch.setattr(m.sys, "executable", r"C:\venv\python.exe")
    monkeypatch.setattr(
        m, "_spawn_detached_argv", lambda argv: captured.setdefault("argv", argv)
    )

    m._spawn_watchdog_replacement(
        start_args=["start", "--port", "51103", "--passive"],
        active_port=51103,
    )

    assert captured["argv"][4:] == ["start", "--port", "51103"]


def test_watchdog_dead_schedules_replacement_before_exit(monkeypatch):
    from agent_bridge import watchdog

    calls = []
    monkeypatch.setattr(
        m,
        "_spawn_watchdog_replacement",
        lambda **_kwargs: calls.append("replacement"),
    )
    monkeypatch.setattr(
        watchdog, "_force_exit", lambda reason: calls.append(("exit", reason))
    )

    m._watchdog_dead("listener stopped")

    assert calls == ["replacement", ("exit", "listener stopped")]


def test_watchdog_dead_still_exits_when_replacement_scheduling_crashes(
    monkeypatch,
):
    from agent_bridge import watchdog

    calls = []

    def crash(**_kwargs):
        raise RuntimeError("unexpected helper failure")

    monkeypatch.setattr(m, "_spawn_watchdog_replacement", crash)
    monkeypatch.setattr(
        watchdog, "_force_exit", lambda reason: calls.append(("exit", reason))
    )

    m._watchdog_dead("listener stopped")

    assert calls == [("exit", "listener stopped")]
