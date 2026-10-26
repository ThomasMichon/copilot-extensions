"""UV-owned signed environments omit ensurepip; ordinary callers keep it."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "libs" / "installer-engine" / "installer-engine.ps1"


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def run(script, tmp_path, shell, *, timeout=30):
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} unavailable")
    path = tmp_path / "fixture.ps1"
    path.write_text("$ErrorActionPreference='Stop'\n" + script, encoding="utf-8")
    environment = dict(os.environ, PSModulePath=str(Path(executable).parent / "Modules"))
    return subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(path)],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
@pytest.mark.parametrize("without_pip", [False, True])
def test_shared_signed_venv_preserves_default_and_explicit_seed_policy(tmp_path, shell, without_pip):
    slot = tmp_path / "slot"
    script = f"""
. {quote(HELPER)}
function Write-Ok {{ param($Message) }}
function Write-Warn {{ param($Message) }}
function Get-SignedBasePython {{ param($MinimumVersion) return 'Invoke-FixturePython' }}
function Invoke-FixturePython {{
    $script:Observed = @($args)
    New-Item -ItemType Directory -Path {quote(slot / 'Scripts')} -Force | Out-Null
    [IO.File]::WriteAllText({quote(slot / 'Scripts' / 'python.exe')}, 'fixture')
    [IO.File]::WriteAllText({quote(slot / 'pyvenv.cfg')}, 'fixture')
}}
if (-not (New-SignedVenv -VenvDir {quote(slot)} -VenvPython {quote(slot / 'Scripts' / 'python.exe')} {'-WithoutPip' if without_pip else ''})) {{ throw 'Venv creation refused' }}
ConvertTo-Json -InputObject @($script:Observed) -Compress
"""
    result = run(script, tmp_path, shell)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == [
        "-m", "venv", "--copies", *(["--without-pip"] if without_pip else []), str(slot),
    ]


@pytest.mark.parametrize("plugin", [
    "agent-bridge", "agent-logger", "agent-vault", "agent-pull-requests", "agent-ssh",
])
def test_uv_owned_callers_select_unseeded_shared_construction(plugin):
    text = (ROOT / "plugins" / plugin / "scripts" / "install.ps1").read_text(encoding="utf-8")
    if plugin == "agent-bridge":
        text = text.split("function New-SignedVenv {", 1)[1].split("\n}", 1)[0]
        assert "-WithoutPip" in text
    else:
        calls = [line for line in text.splitlines() if "(New-SignedVenv " in line]
        assert calls and all("-WithoutPip" in line for line in calls)


@pytest.mark.skipif(os.name != "nt", reason="native signed Windows copies")
@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_actual_uv_install_works_in_signed_unseeded_environment(tmp_path, shell, monkeypatch):
    from wheel_input_fixture import wheel_bytes

    uv = shutil.which("uv")
    if not uv:
        pytest.skip("native UV unavailable")
    if shell == "powershell" and (foreign := shutil.which("pwsh")):
        monkeypatch.setenv("PSModulePath", str(Path(foreign).parent / "Modules"))
    slot = tmp_path / "slot"
    wheel = tmp_path / "fixture-1.0-py3-none-any.whl"
    wheel.write_bytes(wheel_bytes(
        name="fixture", prefix="fixture-1.0.dist-info/",
        metadata=b"Metadata-Version: 2.3\nName: fixture\nVersion: 1.0\n\n",
    ))
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import importlib.util, sys\n"
        "from importlib.metadata import version\n"
        "if sys.argv[1] == 'before':\n"
        "    assert importlib.util.find_spec('pip') is None\n"
        "    assert importlib.util.find_spec('setuptools') is None\n"
        "else:\n"
        "    assert version('fixture') == '1.0'\n",
        encoding="utf-8",
    )
    base = Path(sys._base_executable)
    script = f"""
. {quote(HELPER)}
function Write-Ok {{ param($Message) }}
function Write-Warn {{ param($Message) }}
function Get-SignedBasePython {{ param($MinimumVersion) return {quote(base)} }}
if ((Get-AuthenticodeSignature {quote(base)}).Status -ne 'Valid') {{ throw 'Fixture base is not signed' }}
$slot = {quote(slot)}
$python = Join-Path $slot 'Scripts\\python.exe'
if (-not (New-SignedVenv -VenvDir $slot -VenvPython $python -WithoutPip)) {{ throw 'Unseeded construction failed' }}
foreach ($name in @('python.exe', 'pythonw.exe')) {{
    if ((Get-AuthenticodeSignature (Join-Path $slot "Scripts\\$name")).Status -ne 'Valid') {{
        throw 'Copied interpreter lost its signature'
    }}
}}
& $python -I {quote(probe)} before
if ($LASTEXITCODE -ne 0) {{ throw 'Unneeded packages were seeded' }}
& {quote(uv)} --no-config pip install --python $python --no-index --no-deps {quote(wheel)}
if ($LASTEXITCODE -ne 0) {{ throw 'UV fixture install failed' }}
& $python -I {quote(probe)} after
if ($LASTEXITCODE -ne 0) {{ throw 'Installed fixture cannot be read' }}
"""
    result = run(script, tmp_path, shell, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
