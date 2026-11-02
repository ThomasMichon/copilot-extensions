"""Execute the installer's embedded Python without starting a service."""

from __future__ import annotations

from pathlib import Path
import sys
import base64
import json
from types import ModuleType, SimpleNamespace

import pytest


INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "install.ps1"


def _bootstrap() -> str:
    source = INSTALLER.read_text(encoding="utf-8-sig")
    start = source.index("function Start-DispatchDetachedLogonLauncher {")
    body = source[start:source.index("\nfunction ", start + 1)]
    return body.split("$spawn = @'\n", 1)[1].split("\n'@", 1)[0]


def _fixture(monkeypatch, tmp_path, *, marker=True, payload=None, returncode=0):
    runtime = tmp_path / "runtime with spaces"
    runtime.mkdir()
    launcher = runtime / "supervise-service.ps1"
    launcher.write_text("# agent-dispatch-supervisor-launch-v1\n" if marker else "# legacy\n")
    command = ["powershell.exe", "-NoProfile", "-File", str(launcher),
               "-EnvFile", str(runtime / "profile's environment.env")]
    description = {"version": 1, "executable": sys.executable,
                   "arguments": ["-m", "agent_dispatch", "supervise", "serve", "--legacy-env"],
                   "log_path": str(runtime / "supervise-service.log")}
    if payload is not None:
        description.update(payload)
    calls, preflight = [], []
    module = ModuleType("agent_procutil")
    module.spawn_daemon = lambda *args, **kwargs: calls.append((args, kwargs))
    owned = ModuleType("agent_procutil.owned_process")
    def describe(argv, **kwargs):
        preflight.append((argv, kwargs))
        return SimpleNamespace(returncode=returncode, stderr="fixture description refused",
                               stdout=base64.b64encode(json.dumps(description).encode()).decode("ascii"))
    owned.run_caller_owned = describe
    monkeypatch.setitem(sys.modules, "agent_procutil", module)
    monkeypatch.setitem(sys.modules, "agent_procutil.owned_process", owned)
    monkeypatch.setattr(sys, "argv", ["-c", str(runtime), *command])
    return runtime, command, description, calls, preflight


def test_logon_launcher_preserves_argv_and_uses_durable_logged_spawn(monkeypatch, tmp_path):
    runtime, command, description, calls, preflight = _fixture(monkeypatch, tmp_path)
    exec(compile(_bootstrap(), str(INSTALLER), "exec"), {})
    assert preflight == [([*command, "-DescribeLaunch"], {
        "cwd": str(runtime), "timeout": 30, "max_stdout_bytes": 512 * 1024,
        "max_stderr_bytes": 65536,
    })]
    assert calls == [(([sys.executable, "-X", "agent_dispatch_install_dir=" + str(runtime),
                        *description["arguments"]],), {
        "cwd": str(runtime), "log_path": description["log_path"],
        "env_overrides": {"PYTHONUTF8": "1", "AGENT_DISPATCH_INSTALL_DIR": str(runtime)},
        "console_children": True,
    })]


@pytest.mark.parametrize("payload", [
    {"version": True}, {"executable": "python.exe"}, {"arguments": "not argv"},
    {"arguments": [123]}, {"log_path": None}, {"unexpected": 1},
])
def test_logon_launcher_refuses_invalid_description(monkeypatch, tmp_path, payload):
    _, _, _, calls, _ = _fixture(monkeypatch, tmp_path, payload=payload)
    with pytest.raises(ValueError, match="Invalid supervisor launch description"):
        exec(compile(_bootstrap(), str(INSTALLER), "exec"), {})
    assert calls == []


@pytest.mark.parametrize("failure", ["legacy", "preflight", "spawn"])
def test_logon_launcher_does_not_hide_failures(monkeypatch, tmp_path, failure):
    _, _, _, calls, preflight = _fixture(
        monkeypatch, tmp_path, marker=failure != "legacy",
        returncode=78 if failure == "preflight" else 0,
    )
    error = RuntimeError("fixture daemon lifetime could not be established")
    if failure == "spawn":
        def refuse(*args, **kwargs):
            raise error
        sys.modules["agent_procutil"].spawn_daemon = refuse
    with pytest.raises((ValueError, RuntimeError)) as caught:
        exec(compile(_bootstrap(), str(INSTALLER), "exec"), {})
    if failure == "spawn":
        assert caught.value is error
    if failure == "preflight":
        assert "fixture description refused" in str(caught.value)
    if failure == "legacy":
        assert preflight == [], "legacy launcher must not be executed"
    assert calls == []


@pytest.mark.parametrize("observation", ["matching", "reused", "lookup-failed"])
def test_native_fixture_rejects_unverified_cleanup_handles(observation):
    from .test_supervisor_logon_lifetime_native import _pin_process

    closed = []
    kernel = SimpleNamespace(
        OpenProcess=lambda *args: "fixture-handle",
        CloseHandle=closed.append,
    )

    def birth(api, handle):
        assert api is kernel
        assert handle == "fixture-handle"
        if observation == "lookup-failed":
            raise OSError("fixture creation-time lookup failed")
        return 456 if observation == "matching" else 789

    record = {"pid": 123, "birth": 456}
    if observation == "matching":
        assert _pin_process(kernel, record, birth) == "fixture-handle"
        assert closed == []
    else:
        with pytest.raises((AssertionError, OSError)):
            _pin_process(kernel, record, birth)
        assert closed == ["fixture-handle"]
