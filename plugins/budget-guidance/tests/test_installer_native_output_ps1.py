"""Qualify actual installer output without provisioning a runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from test_installers import PLUGIN, VERSION, _copy_payload

INSTALLER = PLUGIN / "scripts" / "install.ps1"
HOSTS = [
    pytest.param(shutil.which(name), id=name)
    for name in ("pwsh", "powershell.exe")
    if name == "pwsh" or os.name == "nt"
]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows producer encoding")


def _quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _producer_setup(directory: Path) -> str:
    for name in ("installer-human-output.ps1", "versioned_runtime_native.ps1"):
        shutil.copyfile(INSTALLER.parent / name, directory / name)
    text = INSTALLER.read_text(encoding="utf-8")
    start = text.index("Set-StrictMode -Version 2.0")
    end = text.index("# === install-contract:test-persistent-environment", start)
    return text[start:end]


def _run(
    host: str | None, script: Path, env: dict[str, str],
) -> subprocess.CompletedProcess[bytes]:
    if host is None:
        pytest.skip("PowerShell host unavailable")
    return subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(script)],
        env=env, capture_output=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("native_exit", [0, 23])
def test_construction_shell_stderr_uses_requested_producer_encoding(
    host: str | None, native_exit: int, tmp_path: Path,
) -> None:
    text = INSTALLER.read_text(encoding="utf-8")
    start = text.index("function Invoke-VersionedSlotClean {")
    end = text.index("\nfunction Invoke-VersionedMarkComplete", start)
    native = tmp_path / "versioned_runtime.py"
    native.write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "Path(os.environ['BUDGET_NATIVE_RECEIPT']).write_text("
        "json.dumps([sys.flags.isolated,sys.argv[1:]]), encoding='utf-8')\n"
        "sys.stderr.buffer.write(b'native detail caf\\xe9\\n')\n"
        "print(\"function Start-RuntimeConstruction { "
        "param($Python,$Helper,$Root,$Version,$PayloadHash,$LinkName) "
        "[IO.File]::WriteAllText($env:BUDGET_NATIVE_CALLED,'called'); return $true }\")\n"
        f"sys.exit({native_exit})\n",
        encoding="ascii",
    )
    script = tmp_path / "construction-output.ps1"
    root = tmp_path / "runtime with spaces"
    script.write_text(
        "[Console]::OutputEncoding = [Text.Encoding]::GetEncoding(1252)\n"
        "$OutputEncoding = [Text.ASCIIEncoding]::new()\n"
        "$Action = 'install'\n"
        + _producer_setup(tmp_path)
        + f"""
function Test-SourceRuntimeUnchanged {{}}
function Get-BootstrapPython {{ {_quote(sys.executable)} }}
function Get-PayloadHash {{ 'fixture-hash' }}
$VersionedRuntime = $true
$InstallDir = {_quote(root)}
$LinkDir = Join-Path $InstallDir '.venv'
$SrcVersion = '2.0.0'
{text[start:end]}
try {{
    Invoke-VersionedSlotClean
    [Console]::Out.WriteLine('completed')
}} catch {{
    [Console]::Out.WriteLine('caught=' + $_.Exception.Message)
}}
if ([Console]::OutputEncoding.CodePage -ne 1252 -or $OutputEncoding.CodePage -ne 20127) {{
    throw 'native decoding or stdin changed'
}}
exit 0
""",
        encoding="ascii",
    )
    receipt = tmp_path / "native-argv.json"
    called = tmp_path / "called"
    result = _run(host, script, {
        **os.environ, "COPILOT_EXTENSIONS_HUMAN_UTF8": "1",
        "BUDGET_NATIVE_RECEIPT": str(receipt), "BUDGET_NATIVE_CALLED": str(called),
    })
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(receipt.read_text(encoding="utf-8")) == [
        1, ["--root", str(root), "construction-shell", "--shell", "ps1"],
    ]
    assert "native detail caf\u00e9" in result.stderr.decode("utf-8")
    if native_exit == 0:
        assert called.read_text(encoding="utf-8-sig") == "called"
        assert result.stdout.decode("utf-8").strip() == "completed"
    else:
        assert not called.exists()
        assert "Canonical construction adapter is unavailable" in result.stdout.decode("utf-8")


@pytest.mark.parametrize("host", HOSTS)
def test_real_staged_stamp_retains_utf8_and_literal_arguments(
    host: str | None, tmp_path: Path,
) -> None:
    profile = tmp_path / "profile with spaces"
    profile.mkdir()
    payload = profile / ".copilot" / "installed-plugins" / "fixture" / "budget-guidance"
    _copy_payload(payload)
    root = tmp_path / "runtime caf\u00e9 'quoted'"
    script = tmp_path / "staged-output.ps1"
    script.write_text(
        "[Console]::OutputEncoding = [Text.Encoding]::GetEncoding(1252)\n"
        f"& {_quote(payload / 'scripts' / 'install.ps1')} "
        "-Action stamp -InstallDir $env:BUDGET_STAMP_ROOT -Force\n"
        "exit $LASTEXITCODE\n",
        encoding="ascii",
    )
    env = {
        **os.environ, "HOME": str(profile), "USERPROFILE": str(profile),
        "COPILOT_EXTENSIONS_TEST_CONTAINED": "1",
        "COPILOT_EXTENSIONS_HUMAN_UTF8": "1", "BUDGET_STAMP_ROOT": str(root),
    }
    for name in ("COPILOT_PLUGIN_INSTALL_STAGED", "COPILOT_PLUGIN_STAGED_FROM",
                 "COPILOT_PLUGIN_INSTALL_SMOKE", "BUDGET_GUIDANCE_TEST_ARGUMENT_CAPTURE"):
        env.pop(name, None)
    result = _run(host, script, env)
    assert result.returncode == 0, result.stdout + result.stderr
    output = result.stdout.decode("utf-8")
    assert str(root) in output
    assert "Stamped: budget-guidance" in output
    assert not result.stderr, result.stderr
    assert "#< CLIXML" not in output
    stages = list((profile / ".budget-guidance" / ".install-stage").iterdir())
    assert len(stages) == 1
    staged = stages[0] / "budget-guidance" / "scripts" / "install.ps1"
    assert staged.read_bytes() == (payload / "scripts" / "install.ps1").read_bytes()
    snapshot = root / "snapshots" / VERSION
    assert Path((root / "payload-dir").read_text(encoding="utf-8-sig")) == snapshot
    assert (snapshot / "scripts" / "install.ps1").is_file()
    assert not (root / "versions").exists()
