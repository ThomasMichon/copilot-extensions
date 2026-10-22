"""Execute the installer's embedded Python without starting a service."""

from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType

import pytest


INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "install.ps1"


def _bootstrap() -> str:
    source = INSTALLER.read_text(encoding="utf-8-sig")
    start = source.index("function Start-DispatchDetachedLogonLauncher {")
    body = source[start:source.index("\nfunction ", start + 1)]
    return body.split("$spawn = @'\n", 1)[1].split("\n'@", 1)[0]


def test_logon_launcher_preserves_argv_and_uses_durable_logged_spawn(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime with spaces"
    command = [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(runtime / "supervise-service.ps1"), "-EnvFile",
        str(runtime / "profile's environment.env"),
    ]
    calls = []
    module = ModuleType("agent_procutil")
    module.spawn_daemon = lambda *args, **kwargs: calls.append((args, kwargs))
    monkeypatch.setitem(sys.modules, "agent_procutil", module)
    monkeypatch.setattr(sys, "argv", ["-c", str(runtime), *command])

    exec(compile(_bootstrap(), str(INSTALLER), "exec"), {})

    assert calls == [
        (
            (command,),
            {
                "cwd": str(runtime),
                "log_path": runtime / "supervise-launcher.log",
                "console_children": True,
            },
        )
    ]


def test_logon_launcher_does_not_hide_failed_escape(monkeypatch, tmp_path):
    failure = RuntimeError("fixture daemon lifetime could not be established")

    def refuse(*args, **kwargs):
        raise failure

    module = ModuleType("agent_procutil")
    module.spawn_daemon = refuse
    monkeypatch.setitem(sys.modules, "agent_procutil", module)
    monkeypatch.setattr(sys, "argv", ["-c", str(tmp_path), "powershell.exe", "-File", "fixture.ps1"])

    with pytest.raises(RuntimeError) as caught:
        exec(compile(_bootstrap(), str(INSTALLER), "exec"), {})

    assert caught.value is failure
