"""Maintenance must leave replaceable payloads before it waits or builds."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")
GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")
BASH = str(GIT_BASH) if os.name == "nt" and GIT_BASH.is_file() else shutil.which("bash")


def _block(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(f"# === install-contract:v4 {name}")
    end_marker = f"# === end install-contract:v4 {name.split(' (')[0]} ==="
    end = text.index(end_marker, start) + len(end_marker)
    return text[start:end]


def _env(home: Path) -> dict[str, str]:
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COPILOT_", "AGENT_"))
    }
    env.update({
        "HOME": home.as_posix(),
        "USERPROFILE": str(home),
        "COPILOT_PLUGIN_INSTALL_STAGED": "1",
        "COPILOT_PLUGIN_STAGED_FROM": "unrelated-parent-payload",
        "COPILOT_PLUGIN_INSTALL_SMOKE": "1",
        "COPILOT_PLUGIN_INSTALL_SMOKE_SLEEP": "8",
        "COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": "20",
        "COPILOT_EXTENSIONS_TEST_CONTAINED": "1",
    })
    return env


def _entry(tmp_path: Path, extension: str, contextual: bool = False) -> tuple[Path, Path]:
    payload = tmp_path / ".copilot" / "installed-plugins" / "example" / "agent-example"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    (payload / "plugin.json").write_text('{"name":"agent-example"}\n', encoding="utf-8")
    canonical = ROOT / "plugins" / "agent-worktrees" / "scripts" / f"install.{extension}"
    header = "param([string]$Action = 'install')\n" if extension == "ps1" else "#!/usr/bin/env bash\nset -e\n"
    staging = _block(canonical, "self-stage")
    if contextual:
        text = canonical.read_text(encoding="utf-8")
        marker = (
            "    # The standard self-stage block is byte-identical"
            if extension == "ps1"
            else "    # The standard self-stage block is intentionally byte-identical"
        )
        staging = text[text.index(marker):text.index("# === install-contract:v4 self-stage")]
        if extension == "ps1":
            header += (
                "$ErrorActionPreference = 'Stop'\n"
                "$contextPayload = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path\n"
                "$InstallDir = Join-Path $env:USERPROFILE 'runtime'\n"
                "$hostExe = (Get-Process -Id $PID).Path\n"
                "if ($true) {\n"
            )
        else:
            header += (
                'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
                'PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"\n'
                '__aw_install_root="$HOME/runtime"\n'
                'mkdir -p "$__aw_install_root"\n'
                'if true; then\n'
            )
    entry = scripts / f"install.{extension}"
    entry.write_text(
        header + staging + "\n"
        + _block(canonical, "smoke seam (test-only)") + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return payload, entry


def _command(entry: Path, extension: str) -> list[str]:
    if extension == "ps1":
        assert PWSH
        return [PWSH, "-NoProfile", "-File", str(entry)]
    assert BASH
    return [BASH, entry.as_posix()]


def _reported_path(value: str) -> Path:
    if os.name == "nt" and value.startswith("/") and BASH:
        result = subprocess.run(
            [BASH, "-c", 'cygpath -w "$1"', "bash", value],
            capture_output=True, text=True, check=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        value = result.stdout.strip()
    return Path(value).resolve()


@pytest.mark.parametrize("extension", ["ps1", "sh"])
@pytest.mark.parametrize("stage_flag", ["", "1", "context-install"])
@pytest.mark.parametrize("contextual", [False, True], ids=["legacy", "context"])
def test_inherited_stage_flag_cannot_pin_payload(
    tmp_path: Path, extension: str, stage_flag: str, contextual: bool,
) -> None:
    if (extension == "ps1" and not PWSH) or (extension == "sh" and not BASH):
        pytest.skip(f"{extension} interpreter unavailable")
    home = tmp_path / "home"
    home.mkdir()
    payload, entry = _entry(tmp_path, extension, contextual)
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    # MSYS retains its launch CWD in an emulated-exec supervisor. Windows
    # launchers must supply HOME up front; native POSIX exercises relocation.
    launch_cwd = home if os.name == "nt" and extension == "sh" else payload
    env = _env(home)
    env.update({"TEMP": str(home), "TMP": str(home), "TMPDIR": str(home)})
    env["COPILOT_PLUGIN_INSTALL_STAGED"] = stage_flag
    proc = subprocess.Popen(
        _command(entry, extension), cwd=launch_cwd, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kwargs,
    )
    try:
        receipt = home / ".agent-example" / "smoke.json"
        deadline = time.monotonic() + 15
        while not receipt.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if not receipt.exists():
            stdout, stderr = proc.communicate(timeout=20)
            pytest.fail(f"no staged receipt: {proc.returncode}\n{stdout}\n{stderr}")
        data = json.loads(receipt.read_text(encoding="utf-8-sig"))
        assert data["staged"] is True
        assert _reported_path(data["ran_from"]).is_relative_to(home.resolve())
        assert _reported_path(data["working_dir"]) == home.resolve()
        assert data["staged_from"].replace("\\", "/").endswith("/example/agent-example")
        if contextual:
            assert not (home / ".agent-worktrees").exists()
        assert proc.poll() is None
        backup = payload.with_name("replaced-payload")
        payload.rename(backup)
        payload.mkdir()
        assert proc.poll() is None
        stdout, stderr = proc.communicate(timeout=20)
        assert proc.returncode == 0, (stdout, stderr)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=25)


@pytest.mark.parametrize("extension", ["ps1", "sh"])
def test_self_stage_watchdog_still_times_out(tmp_path: Path, extension: str) -> None:
    if (extension == "ps1" and not PWSH) or (extension == "sh" and not BASH):
        pytest.skip(f"{extension} interpreter unavailable")
    home = tmp_path / "home"
    home.mkdir()
    _, entry = _entry(tmp_path, extension)
    env = _env(home)
    env["COPILOT_PLUGIN_INSTALL_DEADLINE_SEC"] = "1"
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run(
        _command(entry, extension), cwd=home, env=env,
        capture_output=True, text=True, timeout=15, **kwargs,
    )
    assert result.returncode == 124, (result.stdout, result.stderr)
    assert "WATCHDOG-KILL" in (home / ".agent-example" / "reconcile.err.log").read_text()


@pytest.mark.parametrize("extension", ["ps1", "sh"])
def test_unavailable_home_refuses_in_place_install(tmp_path: Path, extension: str) -> None:
    if (extension == "ps1" and not PWSH) or (extension == "sh" and not BASH):
        pytest.skip(f"{extension} interpreter unavailable")
    payload, entry = _entry(tmp_path, extension)
    home = tmp_path / "not-a-directory"
    home.write_text("file", encoding="utf-8")
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    proc = subprocess.run(
        _command(entry, extension), cwd=payload, env=_env(home),
        capture_output=True, text=True, timeout=20, **kwargs,
    )
    assert proc.returncode != 0
    assert "self-stage failed" in proc.stderr
    assert "running in place" not in proc.stdout + proc.stderr


def test_all_reconcile_launchers_choose_home() -> None:
    for script in (ROOT / "plugins").glob("*/scripts/bootstrap-check.ps1"):
        text = script.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "Start-Process -FilePath 'conhost.exe'" in line:
                assert "-WorkingDirectory $env:USERPROFILE" in line, script
    for script in (ROOT / "plugins").glob("*/scripts/bootstrap-check.sh"):
        text = script.read_text(encoding="utf-8")
        for line in text.splitlines():
            if re.search(r"^\s*(?:\(.*)?nohup bash ", line):
                assert '(cd "$HOME" && exec nohup bash ' in line, script


@pytest.mark.skipif(os.name != "nt" or not BASH, reason="MSYS Windows launch contract")
def test_windows_bash_refuses_payload_launch_cwd(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    payload, entry = _entry(tmp_path, "sh")
    proc = subprocess.run(
        _command(entry, "sh"), cwd=payload, env=_env(home),
        capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert proc.returncode != 0
    assert "launch from HOME or use install.ps1" in proc.stderr
    assert not (home / ".agent-example" / "smoke.json").exists()
