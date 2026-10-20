"""First-touch interpreter selection before any installer lifecycle mutation."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
HOSTS = [
    host for name in ("pwsh", "powershell.exe")
    if (host := shutil.which(name))
]
pytestmark = pytest.mark.guard


def _quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@pytest.fixture
def admission(tmp_path: Path):
    scripts = tmp_path / "payload" / "scripts"
    scripts.mkdir(parents=True)
    home = tmp_path / "empty-home"
    home.mkdir()
    receipt = home / "admission.json"
    (scripts / "command_binding.py").write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "Path(os.environ['TEST_RECEIPT']).write_text(json.dumps({"
        "'python':sys.executable,'arguments':sys.argv[1:]}))\n",
        encoding="utf-8",
    )
    source = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
    body = source.split(
        "# === command-lifecycle: admission before staging ===\n", 1,
    )[1].split("# === end command-lifecycle: admission before staging ===", 1)[0]
    program = (
        "param([string]$BootstrapPython='')\n"
        "Set-StrictMode -Version Latest\n"
        "$ErrorActionPreference='Stop'\n$ContextualInstall=$false\n"
        "$Action='update'\n$InstallDir=Join-Path $env:USERPROFILE 'never-created'\n"
        "function Get-Command {\n"
        "param($Name,$CommandType,$ErrorAction,[switch]$All)\n"
        "$map=ConvertFrom-Json $env:TEST_CANDIDATES\n"
        "$property=$map.PSObject.Properties[$Name]\n"
        "if ($property) { foreach($path in $property.Value) {"
        " [pscustomobject]@{Source=$path} }; return }\n"
        "if ($Name -in @('py','python3','python')) { return }\n"
        "Microsoft.PowerShell.Core\\Get-Command @PSBoundParameters\n}\n"
        + body
    )
    script = scripts / "install.ps1"
    script.write_text(program, encoding="utf-8")
    old = tmp_path / "old-python.ps1"
    wrapper = (
        "import os,runpy,sys\n"
        "sys.executable=sys.argv.pop(1)\n"
        "sys.version_info=(3,9,5)\n"
        "args=sys.argv[1:]\n"
        "if '-c' in args:\n"
        " code=args[args.index('-c')+1];exec(code)\n"
        "else:\n"
        " while args and args[0].startswith('-'):\n"
        "  flag=args.pop(0)\n"
        "  if flag=='-X':args.pop(0)\n"
        " sys.argv=args;runpy.run_path(args[0],run_name='__main__')\n"
    )
    old.write_text(
        f"& {_quote(sys.executable)} -I -B -c {_quote(wrapper)} $PSCommandPath @args\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    return tmp_path, script, home, receipt, old


def _run(admission, host: str, candidates: dict, explicit: Path | None = None):
    root, script, home, receipt, _ = admission
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COPILOT_", "AGENT_", "PYTHON"))
    }
    environment.update(
        HOME=str(home), USERPROFILE=str(home),
        APPDATA=str(home / "appdata"), LOCALAPPDATA=str(home / "localappdata"),
        TEST_CANDIDATES=json.dumps(candidates), TEST_RECEIPT=str(receipt),
    )
    result = subprocess.run(
        [host, "-NoLogo", "-NoProfile", "-File", str(script),
         *(["-BootstrapPython", str(explicit)] if explicit else [])],
        cwd=root, env=environment, capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert not (home / "never-created").exists()
    return result, json.loads(receipt.read_text()) if receipt.exists() else None


@pytest.mark.parametrize("host", HOSTS)
def test_old_launcher_uses_compatible_path_python(admission, host):
    result, receipt = _run(
        admission, host, {"py": [str(admission[4])], "python": [sys.executable]},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert receipt["python"] == sys.executable
    assert "--run-lifecycle" in receipt["arguments"]


@pytest.mark.parametrize("host", HOSTS)
def test_no_compatible_python_refuses_before_admission(admission, host):
    result, receipt = _run(
        admission, host, {"py": [str(admission[4])], "python": [str(admission[4])]},
    )
    assert result.returncode != 0
    assert "Python 3.10+" in result.stderr
    assert receipt is None


@pytest.mark.parametrize("host", HOSTS)
def test_valid_launcher_is_preferred(admission, host):
    launcher = admission[0] / "valid-launcher.ps1"
    launcher.write_text(
        f"& {_quote(sys.executable)} @($args | Where-Object {{ $_ -ne '-3' }})\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    result, receipt = _run(admission, host, {"py": [str(launcher)]})
    assert result.returncode == 0, result.stdout + result.stderr
    assert receipt["python"] == sys.executable


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("selector", ["-3.14-64", "-V:PythonCore/3.14"])
def test_old_default_finds_newer_launcher_only_interpreter(admission, host, selector):
    launcher = admission[0] / "registered-launcher.ps1"
    stream = "stderr" if selector.startswith("-3") else "stdout"
    inventory = (
        "import sys;"
        "sys.stderr.write('Installed Pythons found:\\n -3.9-64 *\\n');"
        f"sys.{stream}.write(' {selector} Python 3.14\\n')"
    )
    launcher.write_text(
        "if ($args[0] -eq '-0') {\n"
        f" & {_quote(sys.executable)} -I -B -c {_quote(inventory)}\n"
        " exit $LASTEXITCODE\n}\n"
        f"if ($args[0] -eq {_quote(selector)}) {{\n"
        f" & {_quote(sys.executable)} @($args | Select-Object -Skip 1)\n"
        " exit $LASTEXITCODE\n}\n"
        "if ($args[0] -eq '-3' -or $args[0] -eq '-3.9-64') {\n"
        f" & {_quote(admission[4])} @args\n"
        " exit $LASTEXITCODE\n}\nexit 1\n",
        encoding="utf-8",
    )
    result, receipt = _run(admission, host, {"py": [str(launcher)]})
    assert result.returncode == 0, result.stdout + result.stderr
    assert receipt["python"] == sys.executable
    assert "--run-lifecycle" in receipt["arguments"]


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("compatible", [False, True])
def test_explicit_python_is_validated_without_fallback(admission, host, compatible):
    result, receipt = _run(
        admission, host, {"python": [sys.executable]},
        Path(sys.executable) if compatible else admission[4],
    )
    if compatible:
        assert result.returncode == 0, result.stdout + result.stderr
        assert receipt["python"] == sys.executable
    else:
        assert result.returncode != 0
        assert "bootstrap Python" in result.stderr and "3.10+" in result.stderr
        assert receipt is None


@pytest.mark.parametrize("host", HOSTS)
def test_explicit_missing_python_is_not_ignored(admission, host):
    result, receipt = _run(
        admission, host, {"python": [sys.executable]}, admission[0] / "absent.exe",
    )
    assert result.returncode != 0
    assert "existing absolute executable" in result.stderr
    assert receipt is None


@pytest.mark.parametrize("host", HOSTS)
def test_path_search_skips_alias_and_incompatible_first_match(admission, host):
    alias = admission[0] / "WindowsApps" / "python.ps1"
    alias.parent.mkdir()
    alias.write_text("throw 'WindowsApps alias must not run'\n", encoding="utf-8")
    result, receipt = _run(
        admission, host,
        {"python3": [str(alias), str(admission[4]), sys.executable]},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert receipt["python"] == sys.executable
    assert "WindowsApps alias must not run" not in result.stderr


@pytest.mark.parametrize("host", HOSTS)
def test_exact_admission_floor_preserves_deferred_bootstrap(admission, host):
    supported = admission[0] / "supported-python.ps1"
    supported.write_text(
        admission[4].read_text(encoding="utf-8").replace(
            "sys.version_info=(3,9,5)", "sys.version_info=(3,10,0)",
        ),
        encoding="utf-8",
    )
    script = admission[1]
    script.write_text(
        script.read_text(encoding="utf-8").replace("$Action='update'", "$Action='stamp'"),
        encoding="utf-8",
    )
    result, receipt = _run(admission, host, {}, supported)
    assert result.returncode == 0, result.stdout + result.stderr
    assert receipt["python"] == str(supported)
    assert "--deferred-runtime" in receipt["arguments"]


@pytest.mark.parametrize("host", HOSTS)
def test_discovered_python_preserves_unicode_path_through_native_output(admission, host):
    supported = admission[0] / "caf\u00e9" / "python.ps1"
    supported.parent.mkdir()
    supported.write_text(
        admission[4].read_text(encoding="utf-8").replace(
            "sys.version_info=(3,9,5)", "sys.version_info=(3,10,0)",
        ),
        encoding="utf-8",
    )
    result, receipt = _run(admission, host, {"python": [str(supported)]})
    assert result.returncode == 0, result.stdout + result.stderr
    assert receipt["python"] == str(supported)
    assert "--run-lifecycle" in receipt["arguments"]


@pytest.mark.skipif(os.name != "nt", reason="Native Windows Python launcher")
@pytest.mark.parametrize("host", HOSTS)
def test_real_python39_launcher_first_touch_uses_compatible_alternative(admission, host):
    launcher = shutil.which("py")
    if launcher is None:
        pytest.skip("Python launcher unavailable")
    discovery = subprocess.run(
        [launcher, "-3.9", "-I", "-B", "-c", "import sys; print(sys.executable)"],
        capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if discovery.returncode != 0:
        pytest.skip("Existing Python 3.9 interpreter unavailable")
    old_python = discovery.stdout.strip()
    fixture_launcher = admission[0] / "real-old-launcher.ps1"
    fixture_launcher.write_text(
        f"& {_quote(old_python)} @($args | Where-Object {{ $_ -notmatch '^-3' }})\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    result, receipt = _run(
        admission, host, {"py": [str(fixture_launcher)], "python": [sys.executable]},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert receipt["python"] == sys.executable
    assert "--run-lifecycle" in receipt["arguments"]
