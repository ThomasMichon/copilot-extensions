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
import subprocess

from agent_bridge import __main__ as m


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

    ok = m._spawn_via_wmi_broker([r"C:\py.exe", "-m", "agent_bridge", "start"])

    assert ok is True
    assert captured["cmd"][0] == "powershell"
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


def test_spawn_detached_escalates_to_wmi_when_breakaway_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(m.sys, "platform", "win32")
    monkeypatch.setattr(m, "_daemon_launch_argv", lambda: ["py", "-m", "agent_bridge", "start"])

    # Every Popen (the breakaway attempt AND any plain fallback) raises, as a
    # kill-on-close job would for CREATE_BREAKAWAY_FROM_JOB.
    def _popen_raises(*a, **k):
        raise OSError("job forbids breakaway")

    monkeypatch.setattr(subprocess, "Popen", _popen_raises)

    calls = {"wmi": 0}

    def _fake_wmi(argv):
        calls["wmi"] += 1
        return True  # broker succeeds -> no plain-detached fallback

    monkeypatch.setattr(m, "_spawn_via_wmi_broker", _fake_wmi)

    # Must not raise: breakaway OSError -> WMI broker succeeds -> return.
    m._spawn_detached_daemon()
    assert calls["wmi"] == 1


def test_spawn_detached_happy_path_is_breakaway(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(m, "_daemon_launch_argv", lambda: ["py", "-m", "agent_bridge", "start"])

    seen = {"flags": None, "wmi": 0}

    def _popen_ok(argv, **kwargs):
        seen["flags"] = kwargs.get("creationflags")
        return object()

    monkeypatch.setattr(subprocess, "Popen", _popen_ok)
    monkeypatch.setattr(
        m,
        "windowless_daemon_kwargs",
        lambda **_kwargs: {"creationflags": 0x09000000},
    )
    monkeypatch.setattr(m, "_spawn_via_wmi_broker", lambda argv: seen.__setitem__("wmi", seen["wmi"] + 1) or True)

    m._spawn_detached_daemon()
    # Breakaway succeeded -> the WMI broker is never reached.
    assert seen["wmi"] == 0
    assert seen["flags"] == 0x09000000


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
