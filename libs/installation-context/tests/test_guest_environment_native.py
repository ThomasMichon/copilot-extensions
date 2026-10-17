"""Native entry points reject inherited guest labels without changing state."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

LIB = Path(__file__).resolve().parents[1]
HOSTS = [
    pytest.param(shutil.which(name), id=name)
    for name in ("pwsh", "powershell.exe")
    if name == "pwsh" or os.name == "nt"
]


def _initialized_host(
    executable: str, profile: Path, environment: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ModuleType, Any, dict[str, str]]:
    path = LIB.parents[1] / "libs/payload-invocation/tests/test_command_binding.py"
    spec = importlib.util.spec_from_file_location("guest_native_fixture", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    module.POWERSHELL = executable
    environment = module._catalog_environment({
        "home": profile, "payload": profile, "environment": environment,
    })
    return module, module._PowerShellHost(profile, environment), environment


def _powershell_command(
    executable: str, profile: Path, action: str, **options: str,
) -> list[str]:
    def literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    script = profile / f"invoke-{action}.ps1"
    arguments = " ".join(f"-{name} {literal(value)}" for name, value in options.items())
    script.write_text(
        f"& {literal(str(LIB / 'installation-context.ps1'))} {literal(action)} {arguments}\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8-sig",
    )
    return [executable, "-NoProfile", "-NonInteractive", "-File", str(script)]


@pytest.mark.skipif(os.name != "nt", reason="native Windows platform boundary")
@pytest.mark.installation_context_smoke
@pytest.mark.parametrize("host", HOSTS)
def test_powershell_platform_and_guest_actions_ignore_inherited_os_labels(
    host: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if host is None:
        pytest.skip("PowerShell host unavailable")
    profile = tmp_path / "isolated profile"
    profile.mkdir()
    environment = {
        **os.environ, "USERPROFILE": str(profile), "HOME": str(profile),
        "OS": "inherited-posix-label", "WSL_DISTRO_NAME": "Inherited guest",
    }
    status_command = _powershell_command(host, profile, "guest-status")
    registration_command = _powershell_command(
        host, profile, "guest-register", Distribution="Inherited guest",
    )
    fixture, initialized, _environment = _initialized_host(host, profile, environment, monkeypatch)
    before = fixture._snapshot(profile)
    try:
        for _ in range(2):
            result = initialized.run(status_command)
            assert result.returncode == 0, result.stdout + result.stderr
            status = json.loads(result.stdout)
            assert status["status"] == "ready"
            assert status["environment"]["platform"] == "windows"
            assert status["environment"]["wslDistro"] is None
            assert Path(status["environment"]["homeRealPath"]) == profile.resolve()
            refused = initialized.run(registration_command)
            assert refused.returncode == 1
            assert fixture._snapshot(profile) == before
    finally:
        initialized.close()
        assert fixture._snapshot(profile) == before
    assert "requires a real WSL guest" in "".join(initialized.errors)


@pytest.mark.skipif(os.name != "nt", reason="native Windows reference parity")
@pytest.mark.parametrize("host", HOSTS)
def test_native_host_default_diagnostic_matches_python(
    host: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if host is None:
        pytest.skip("PowerShell host unavailable")
    environment = {**os.environ, "USERPROFILE": str(tmp_path), "HOME": str(tmp_path)}
    commands = (
        [sys.executable, str(LIB / "installation_context.py"), "guest-status"],
        _powershell_command(host, tmp_path, "guest-status"),
    )
    fixture, initialized, environment = _initialized_host(host, tmp_path, environment, monkeypatch)
    before = fixture._snapshot(tmp_path)
    try:
        reference = subprocess.run(
            commands[0], env=environment, capture_output=True, text=True,
            encoding="utf-8", timeout=20, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        result = initialized.run(commands[1])
        for response in (reference, result):
            assert response.returncode == 0, response.stdout + response.stderr
        assert json.loads(result.stdout) == json.loads(reference.stdout)
        assert fixture._snapshot(tmp_path) == before
    finally:
        initialized.close()
        assert fixture._snapshot(tmp_path) == before
