"""Composed native constructors retain the requested managed interpreter."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

from test_native_helper_python_binding import BASH, _materialized_engine


def _uv(area: Path) -> tuple[Path, Path, Path]:
    trace = area / "uv-calls.jsonl"
    slot = area / "environment"
    source = area / "uv-fixture.py"
    source.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args=sys.argv[1:]\n"
        "with open(os.environ['FIXTURE_TRACE'],'a') as f: f.write(json.dumps(args)+'\\n')\n"
        "if args[:2]==['python','find']:\n"
        "    if os.environ.get('FIXTURE_MODE')=='missing' or 'FIXTURE_MANAGED_CANDIDATE' not in os.environ: sys.exit(1)\n"
        "    print(os.environ['FIXTURE_MANAGED_CANDIDATE']); sys.exit(0)\n"
        "if args[:2]!=['venv',os.environ['FIXTURE_SLOT']]: sys.exit(96)\n"
        "mode=os.environ['FIXTURE_MODE']\n"
        "if mode=='selected' and '--managed-python' not in args: sys.exit(43)\n"
        "if mode=='failure' and '--python' in args: sys.exit(42)\n"
        "root=Path(os.environ['FIXTURE_SLOT'])\n"
        "(root/'bin').mkdir(parents=True,exist_ok=True)\n"
        "(root/'Scripts').mkdir(exist_ok=True)\n"
        "(root/'bin/python').write_text('fixture')\n"
        "(root/'Scripts/python.exe').write_text('fixture')\n"
        "(root/'pyvenv.cfg').write_text('fixture')\n"
        "sys.exit(42 if mode=='partial' else 0)\n",
        encoding="utf-8",
    )
    launcher = area / ("fixture-uv.cmd" if os.name == "nt" else "fixture-uv")
    launcher.write_text(
        f'@echo off\r\n"{sys.executable}" "{source}" %*\r\n' if os.name == "nt" else
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(source))} "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o700)
    return launcher, trace, slot


def _environment(trace: Path, slot: Path, mode: str) -> dict[str, str]:
    return {
        **os.environ, "FIXTURE_TRACE": str(trace), "FIXTURE_SLOT": str(slot),
        "FIXTURE_MODE": mode,
    }


def _assert_construction(result: subprocess.CompletedProcess[str], trace: Path, mode: str) -> None:
    assert result.returncode == (0 if mode == "selected" else 1), result.stdout + result.stderr
    calls = [json.loads(line) for line in trace.read_text().splitlines()]
    construction = [call for call in calls if call[0] == "venv"]
    assert len(construction) == 1, calls
    assert "--managed-python" in construction[0]
    assert construction[0][construction[0].index("--python") + 1] == "3.11"
    assert all(call == [
        "python", "find", "--managed-python", "--no-project", "--system",
        "--no-python-downloads", "3.11",
    ] for call in calls if call[0] != "venv")


@pytest.mark.skipif(sys.platform != "linux" or BASH is None, reason="native Bash constructor")
@pytest.mark.parametrize("mode", ["selected", "failure", "partial"])
def test_bash_constructor_never_falls_back_to_another_python(tmp_path: Path, mode: str) -> None:
    assert BASH is not None
    engine = _materialized_engine(tmp_path)
    uv, trace, slot = _uv(tmp_path)
    result = subprocess.run(
        [BASH, "--noprofile", "--norc", "-c",
         '. "$1"; new_signed_venv "$2" "$3" 3.11', "fixture",
         str(engine / "installer-engine.sh"), str(uv), str(slot)],
        env=_environment(trace, slot, mode), capture_output=True, text=True, timeout=15,
    )
    _assert_construction(result, trace, mode)


def _quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _powershell(host: str, area: Path, script: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    if host == "powershell.exe" and os.name != "nt":
        pytest.skip("Windows PowerShell requires native Windows")
    executable = shutil.which(host)
    if executable is None:
        pytest.skip(f"{host} unavailable")
    entry = area / "fixture.ps1"
    entry.write_text("$ErrorActionPreference='Stop'\n" + script, encoding="utf-8")
    return subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(entry)],
        env=environment, capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )


@pytest.mark.parametrize("host", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("mode", ["selected", "failure", "partial"])
def test_powershell_constructor_never_falls_back_to_another_python(
    tmp_path: Path, host: str, mode: str,
) -> None:
    engine = _materialized_engine(tmp_path)
    uv, trace, slot = _uv(tmp_path)
    result = _powershell(host, tmp_path, f"""
. {_quote(engine / 'installer-engine.ps1')}
function Get-SignedBasePython {{ throw 'A caller must not replace managed selection with legacy discovery' }}
function Write-Warn {{ param($Message) }}
$accepted=New-SignedVenv -VenvDir {_quote(slot)} -VenvPython {_quote(slot / 'Scripts/python.exe')} `
    -PythonVersion '3.11' -UvCommand {_quote(uv)} -WithoutPip
if (-not $accepted) {{ exit 1 }}
""", _environment(trace, slot, mode))
    _assert_construction(result, trace, mode)


@pytest.mark.parametrize("host", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("selection", ["missing", "unsigned", "signed", "below-minimum"])
def test_signed_base_selection_only_inspects_the_requested_managed_python(
    tmp_path: Path, host: str, selection: str,
) -> None:
    engine = _materialized_engine(tmp_path)
    uv, trace, slot = _uv(tmp_path)
    evidence_file = tmp_path / "selection.json"
    candidate = tmp_path / "managed python.exe"
    candidate.touch()
    result = _powershell(host, tmp_path, f"""
. {_quote(engine / 'installer-engine.ps1')}
$env:OS='Windows_NT'
function Get-Command {{ throw 'Ambient Python discovery is forbidden' }}
function Get-AuthenticodeSignature {{
    param($FilePath)
    if ($FilePath -cne {_quote(candidate)}) {{ throw 'Wrong signature candidate' }}
    return [pscustomobject]@{{Status='{('Valid' if selection in {'signed', 'below-minimum'} else 'NotSigned')}'}}
}}
$selected=Get-SignedBasePython -UvCommand {_quote(uv)} -PythonVersion '{'3.10' if selection == 'below-minimum' else '3.11'}' -MinimumVersion '3.11'
@{{Selected=$selected}} | ConvertTo-Json -Compress |
    Set-Content -LiteralPath {_quote(evidence_file)} -Encoding UTF8
""", {
        **_environment(trace, slot, selection), "FIXTURE_MANAGED_CANDIDATE": str(candidate),
    })
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(evidence_file.read_text(encoding="utf-8-sig"))
    assert evidence["Selected"] == (str(candidate) if selection == "signed" else None)
    if selection == "below-minimum":
        assert not trace.exists()
    else:
        assert [json.loads(line) for line in trace.read_text().splitlines()] == [[
            "python", "find", "--managed-python", "--no-project", "--system",
            "--no-python-downloads", "3.11",
        ]]
