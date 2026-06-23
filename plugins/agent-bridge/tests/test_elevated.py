"""Smoke tests for the elevated sub-daemon launcher helpers (no UAC/Task Sched)."""

from __future__ import annotations

import socket

from agent_bridge import elevated


def test_is_up_false_on_closed_port():
    # Pick an almost-certainly-free high port and confirm is_up reports down.
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    free_port = s.getsockname()[1]
    s.close()
    assert elevated.is_up(free_port, timeout=0.5) is False


def test_elevated_dir_under_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    ed = elevated.elevated_dir()
    assert ed == tmp_path / "elevated"
    assert ed.is_dir()


def test_read_token_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    assert elevated.read_token() is None


def test_constants():
    assert elevated.ELEVATED_PORT == 9281
    assert elevated.TASK_NAME == "agent-bridge-elevated"


def test_relay_spawn_command_shape():
    cmd = elevated.relay_spawn_command("SPO.Core", token="tok123", port=9281)
    assert cmd[1:] == [
        "-m", "agent_bridge", "acp-connect",
        "ws://127.0.0.1:9281/acp/SPO.Core", "--token", "tok123", "--stdio",
    ]
    # First element is the interpreter that hosts agent_bridge.
    assert cmd[0].lower().endswith(("python", "python.exe", "python3"))


def test_relay_applicable_false_when_not_requires_admin():
    assert elevated.relay_applicable(False) is False


def test_relay_applicable_off_windows(monkeypatch):
    import sys as _sys

    monkeypatch.setattr(_sys, "platform", "linux")
    assert elevated.relay_applicable(True) is False


def test_relay_applicable_true_on_windows_non_elevated(monkeypatch):
    import sys as _sys

    monkeypatch.setattr(_sys, "platform", "win32")
    monkeypatch.setattr(elevated, "is_process_elevated", lambda: False)
    assert elevated.relay_applicable(True) is True


def test_relay_not_applicable_when_already_elevated(monkeypatch):
    import sys as _sys

    monkeypatch.setattr(_sys, "platform", "win32")
    monkeypatch.setattr(elevated, "is_process_elevated", lambda: True)
    assert elevated.relay_applicable(True) is False


# -- Headless start / stop (scheduled-task lifecycle) ------------------------


def _stub_start(monkeypatch, tmp_path):
    """Stub disk/health side effects so ensure_running can run logic-only.

    is_up is driven by a mutable ``state["up"]`` flag (starts down; the test's
    _run_task/_run_elevated stub flips it up to satisfy the readiness poll), and
    read_token yields a token. _end_task is stubbed to a no-op so the zombie
    pre-clear never touches schtasks. Returns the shared state dict.
    """
    state = {"up": False}
    monkeypatch.setattr(elevated, "is_up", lambda *a, **k: state["up"])
    monkeypatch.setattr(elevated, "read_token", lambda: "subtok")
    monkeypatch.setattr(elevated, "_seed_config", lambda port: tmp_path)
    monkeypatch.setattr(
        elevated, "_write_launcher", lambda ed, port: tmp_path / "launcher.cmd"
    )
    monkeypatch.setattr(elevated, "_end_task", lambda: 0)
    return state


def test_ensure_running_returns_token_when_already_up(monkeypatch):
    monkeypatch.setattr(elevated, "is_up", lambda *a, **k: True)
    monkeypatch.setattr(elevated, "read_token", lambda: "tok")
    called = {"elev": False, "run": False}
    monkeypatch.setattr(elevated, "_run_elevated", lambda s: called.__setitem__("elev", True))
    monkeypatch.setattr(elevated, "_run_task", lambda: called.__setitem__("run", True))
    assert elevated.ensure_running() == "tok"
    assert called == {"elev": False, "run": False}


def test_ensure_running_headless_when_task_registered(monkeypatch, tmp_path):
    state = _stub_start(monkeypatch, tmp_path)
    monkeypatch.setattr(elevated, "_task_registered", lambda: True)
    calls = []

    def _run():
        calls.append("run")
        state["up"] = True
        return 0

    monkeypatch.setattr(elevated, "_run_task", _run)
    monkeypatch.setattr(
        elevated, "_run_elevated",
        lambda s: calls.append("elevated") or 0,
    )
    tok = elevated.ensure_running(wait=2.0)
    assert tok == "subtok"
    # Headless: schtasks /run only, NO elevated UAC bootstrap.
    assert calls == ["run"]


def test_ensure_running_clears_zombie_task(monkeypatch, tmp_path):
    # Task registered but port down (zombie): the stale instance must be ended
    # (reaping the orphaned relay) before /run, and with no UAC bootstrap.
    state = _stub_start(monkeypatch, tmp_path)
    monkeypatch.setattr(elevated, "_task_registered", lambda: True)
    order = []
    monkeypatch.setattr(elevated, "_end_task", lambda: order.append("end") or 0)

    def _run():
        order.append("run")
        state["up"] = True
        return 0

    monkeypatch.setattr(elevated, "_run_task", _run)
    monkeypatch.setattr(
        elevated, "_run_elevated",
        lambda s: order.append("elevated") or 0,
    )
    tok = elevated.ensure_running(wait=2.0)
    assert tok == "subtok"
    assert order == ["end", "run"]


def test_ensure_running_registers_when_task_absent(monkeypatch, tmp_path):
    state = _stub_start(monkeypatch, tmp_path)
    monkeypatch.setattr(elevated, "_task_registered", lambda: False)
    calls = []
    monkeypatch.setattr(elevated, "_run_task", lambda: calls.append("run") or 0)

    def _elev(s):
        calls.append("elevated")
        state["up"] = True
        return 0

    monkeypatch.setattr(elevated, "_run_elevated", _elev)
    monkeypatch.setattr(
        elevated, "_write_bootstrap",
        lambda ed, launcher, action: tmp_path / "bootstrap.cmd",
    )
    tok = elevated.ensure_running(wait=2.0)
    assert tok == "subtok"
    # First time: one elevated registration bootstrap, no headless /run.
    assert calls == ["elevated"]


def test_stop_is_headless_by_default(monkeypatch):
    calls = []
    monkeypatch.setattr(elevated, "_end_task", lambda: calls.append("end") or 0)
    monkeypatch.setattr(elevated, "_run_elevated", lambda s: calls.append("elevated") or 0)
    elevated.stop()
    assert calls == ["end"]  # no UAC


def test_stop_deregister_runs_elevated(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(elevated, "_end_task", lambda: calls.append("end") or 0)
    monkeypatch.setattr(elevated, "_run_elevated", lambda s: calls.append("elevated") or 0)
    monkeypatch.setattr(elevated, "elevated_dir", lambda: tmp_path)
    monkeypatch.setattr(
        elevated, "_write_bootstrap",
        lambda ed, launcher, action: tmp_path / "bootstrap.cmd",
    )
    elevated.stop(deregister=True)
    assert calls == ["end", "elevated"]


def test_launcher_passes_idle_shutdown(monkeypatch, tmp_path):
    monkeypatch.setattr(elevated, "_venv_python", lambda: "py.exe")
    monkeypatch.setattr(elevated, "elevated_dir", lambda: tmp_path)
    launcher = elevated._write_launcher(tmp_path, 9281)
    body = launcher.read_text()
    assert f"--idle-shutdown {elevated.IDLE_SHUTDOWN_SECONDS}" in body
    assert "-m agent_bridge start --port 9281" in body
