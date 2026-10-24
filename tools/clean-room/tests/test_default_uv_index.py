"""The public runtime index is explicit, overrideable and not host-inherited."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess

import pytest

RIG = Path(__file__).resolve().parents[1]
DEFAULT_INDEX = "https://packagefeedproxy.microsoft.io/pypi/simple/"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
BASH = shutil.which("bash")
CASES = (
    ("default", None, None, DEFAULT_INDEX),
    ("environment", "https://configured.example/simple/", None, "https://configured.example/simple/"),
    ("argument", "https://configured.example/simple/", "https://argument.example/simple/", "https://argument.example/simple/"),
    ("disabled", "https://configured.example/simple/", "", ""),
)


def environment(index):
    env = dict(os.environ, UV_DEFAULT_INDEX="https://host-only.example/simple/")
    env.pop("CR_UV_INDEX", None)
    env.pop("CR_HARNESS_MOUNT_HOST", None)
    if index is not None:
        env["CR_UV_INDEX"] = index
    return env


def scenario(tmp_path):
    path = tmp_path / "scenario"
    path.mkdir()
    (path / "manifest.json").write_text(
        json.dumps({"tier": "P", "auth": {"copilot": "none"}}), encoding="utf-8",
    )
    return path


def assert_index(arguments, expected):
    entries = [arguments[index + 1] for index, value in enumerate(arguments[:-1]) if value == "-e"]
    values = dict(entry.split("=", 1) for entry in entries if "=" in entry)
    assert values["CR_UV_INDEX"] == expected
    if expected:
        assert values["UV_DEFAULT_INDEX"] == expected
    else:
        assert "UV_DEFAULT_INDEX" not in values


def run(argv, env):
    result = subprocess.run(
        argv, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    return result


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
@pytest.mark.parametrize("_name,index,argument,expected", CASES)
def test_powershell_container_index(tmp_path, _name, index, argument, expected):
    fixture = scenario(tmp_path)
    source = (RIG / "run.ps1").read_text(encoding="utf-8")
    prefix = source[:source.index("# --- Until validation")]
    start = source.index("function Start-Container {")
    function = source[start:source.index("\nfunction Ensure-Container", start)]
    quote = lambda path: str(path).replace("'", "''")
    probe = tmp_path / "probe.ps1"
    probe.write_text(
        prefix
        + "\nfunction Resolve-CopilotToken { throw 'unexpected authentication' }\n"
        "function Test-Image { return $true }\n"
        "function docker { if ($args[0] -eq 'run') { $script:captured=@($args) } }\n"
        f"$ScenarioDir='{quote(fixture)}'; $Results='{quote(tmp_path / 'out')}'\n"
        f"$LibDir='{quote(RIG / 'lib')}'\n"
        "$BaseTag='base'; $AuthTag='authed'; $Container='fixture'\n"
        "$PassEnv=@(); $HarnessMount=''; $ScenarioSharedLib=$null\n"
        + function
        + "\nStart-Container\nConvertTo-Json -Compress -InputObject $script:captured\n",
        encoding="utf-8",
    )
    argv = [POWERSHELL, "-NoProfile", "-File", str(probe)]
    if argument is not None:
        argv += ["-UvIndex", argument]
    result = run(argv, environment(index))
    assert_index(json.loads(result.stdout.splitlines()[-1]), expected)


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
@pytest.mark.parametrize("_name,index,argument,expected", CASES)
def test_windows_container_arm_index(tmp_path, _name, index, argument, expected):
    fixture = scenario(tmp_path)
    (fixture / "scenario.ps1").write_text("# native argument-assembly fixture\n")
    partner = tmp_path / "partner"
    partner.mkdir()
    captured = tmp_path / "docker-arguments.json"
    quote = lambda path: str(path).replace("'", "''")
    probe = tmp_path / "windows-arm.ps1"
    probe.write_text(
        "param([AllowEmptyString()][string]$Index, [switch]$UseIndex)\n"
        "$ErrorActionPreference='Stop'\n"
        f"$global:CleanRoomTestCapture='{quote(captured)}'\n"
        "function docker {\n"
        "  $global:LASTEXITCODE=0\n"
        "  if ($args[0] -eq 'images') { return 'fixture-image-id' }\n"
        "  if ($args[0] -eq 'run') {\n"
        "    [IO.File]::WriteAllText($global:CleanRoomTestCapture, "
        "(ConvertTo-Json -Compress -InputObject @($args)))\n"
        "  }\n"
        "}\n"
        f"$options=@{{Os='windows';NoToken=$true;Scenario='{quote(fixture)}';"
        f"PartnerPath='{quote(partner)}';ResultsDir='{quote(tmp_path / 'out')}'}}\n"
        "if ($UseIndex) { $options.UvIndex=$Index }\n"
        f"& '{quote(RIG / 'run.ps1')}' @options\n",
        encoding="utf-8",
    )
    argv = [POWERSHELL, "-NoProfile", "-File", str(probe)]
    if argument is not None:
        argv += ["-UseIndex", "-Index", argument]
    run(argv, environment(index))
    assert_index(json.loads(captured.read_text()), expected)


@pytest.mark.skipif(os.name == "nt" or not BASH, reason="POSIX Bash required")
@pytest.mark.parametrize("_name,index,argument,expected", CASES)
def test_bash_container_index(tmp_path, _name, index, argument, expected):
    fixture = scenario(tmp_path)
    source = (RIG / "run.sh").read_text(encoding="utf-8")
    prefix = source[:source.index("# Resolve the scenario dir")]
    start = source.index("start_container() {")
    function = source[start:source.index("\nensure_container()", start)]
    captured = tmp_path / "docker-arguments"
    quote = lambda path: shlex.quote(str(path))
    probe = tmp_path / "probe.sh"
    probe.write_text(
        prefix
        + "\n_py() { command -v python3 || command -v python; }\n"
        "resolve_token() { echo 'unexpected authentication' >&2; exit 91; }\n"
        "img_exists() { return 0; }\n"
        f'docker() {{ if [ "$1" = run ]; then printf "%s\\0" "$@" > {quote(captured)}; fi; }}\n'
        f"SCENARIO_DIR={quote(fixture)}\nRESULTS={quote(tmp_path / 'out')}\n"
        f"LIB_DIR={quote(RIG / 'lib')}\n"
        "BASE_TAG=base; AUTH_TAG=authed; CONTAINER=fixture; SCENARIO_NAME=fixture\n"
        "CLEAN_ROOM_LABEL=fixture; RESULTS_PREEXISTED=1; SCENARIO_SHARED_LIB=''; HARNESS_MOUNT=''\n"
        + function
        + "\nstart_container\n",
        encoding="utf-8",
    )
    argv = [BASH, str(probe)]
    if argument is not None:
        argv += ["--uv-index", argument]
    run(argv, environment(index))
    assert_index(captured.read_bytes().decode().rstrip("\0").split("\0"), expected)
