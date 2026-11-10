"""The shipped Windows entry refuses absent runtimes without provisioning."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from agent_procutil import no_window_kwargs
from .test_invoke_payload_runtime_lock import _bash

PLUGIN = Path(__file__).resolve().parents[1]
HOSTS = [path for name in ("powershell.exe", "pwsh.exe") if (path := shutil.which(name))]
pytestmark = [
    pytest.mark.portfolio_tier("T2"), pytest.mark.effect("process"),
    pytest.mark.contract("agent_machines.configuration_payload_readonly"),
]


@pytest.mark.skipif(os.name != "nt", reason="Native Windows payload entry")
@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("operation", ["configuration", "health-run"])
def test_configuration_payload_entry_never_bootstraps_missing_runtime(tmp_path, host, apply, operation):
    payload, home = tmp_path / "payload", tmp_path / "home"
    scripts = payload / "scripts"
    (scripts / "installation-context").mkdir(parents=True)
    (payload / "bin").mkdir()
    home.mkdir()
    (payload / "plugin.json").write_text('{"name":"agent-machines"}', encoding="utf-8")
    for relative in (
        Path("bin") / "agent-machines.ps1", Path("scripts") / "invoke-payload-runtime.ps1",
    ):
        shutil.copyfile(PLUGIN / relative, payload / relative)
    (scripts / "installation-context" / "installation-context.ps1").write_text(
        "[Console]::Out.WriteLine($env:FIXTURE_RESOLUTION)\nexit 0\n", encoding="utf-8",
    )
    (scripts / "resolve-runtime.ps1").write_text(
        "param([string]$TraceMode)\n"
        "if ($TraceMode -cne 'read-only') { throw 'Read-only selection required' }\n"
        "$AgentRtPy = $null\n",
        encoding="utf-8",
    )
    (scripts / "init.ps1").write_text(
        "[IO.File]::WriteAllText($env:FIXTURE_CANARY,'installer called')\nexit 91\n",
        encoding="utf-8",
    )
    canary = tmp_path / "installer-canary"
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"COPILOT_EXTENSIONS_CONTEXT", "AGENT_MACHINES_NO_SELFPROVISION"}
    }
    environment.update(
        HOME=str(home), USERPROFILE=str(home), COPILOT_PROJECT_DIR=str(tmp_path),
        FIXTURE_CANARY=str(canary), FIXTURE_RESOLUTION=json.dumps({
            "status": "ready", "reason": "policy-default-false",
            "actualMode": "legacy", "desiredMode": "legacy",
        }),
    )
    initialized = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-Command",
         "Get-Process -Id $PID | Out-Null"],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=15,
        **no_window_kwargs(),
    )
    assert initialized.returncode == 0, initialized.stderr
    def snapshot():
        # PowerShell 7 updates its own JIT profile on every native invocation.
        return {str(path.relative_to(home)): path.read_bytes()
                for path in home.rglob("*")
                if path.is_file() and path.name != "StartupProfileData-NonInteractive"}

    before = snapshot()
    arguments = (
        ["health", "run", "--store-dir", str(tmp_path / "store")]
        if operation == "health-run" else ["installation-configuration", "--project", "sample"]
    )
    if apply:
        arguments += ["--apply"]
        if operation == "health-run":
            arguments += ["--expect-policy", "a" * 64]
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(payload / "bin" / "agent-machines.ps1"),
         *arguments],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=15,
        **no_window_kwargs(),
    )
    assert result.returncode == 1, (result.stdout, result.stderr)
    name = "Health collection" if operation == "health-run" else "Configuration"
    assert f"{name} requires an already provisioned runtime" in result.stderr
    assert not canary.exists() and not (home / ".agent-machines").exists()
    assert snapshot() == before


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("operation", ["configuration", "health-run"])
def test_posix_configuration_payload_entry_never_bootstraps_missing_runtime(tmp_path, apply, operation):
    bash = _bash()
    if bash is None:
        pytest.skip("Bash is unavailable")
    payload, home = tmp_path / "payload", tmp_path / "home"
    scripts = payload / "scripts"
    (scripts / "installation-context").mkdir(parents=True)
    (payload / "bin").mkdir()
    home.mkdir()
    (payload / "plugin.json").write_text('{"name":"agent-machines"}', encoding="utf-8")
    for relative in (
        Path("bin") / "agent-machines", Path("scripts") / "invoke-payload-runtime.sh",
        Path("scripts") / "installation-context" / "json-query.awk",
    ):
        shutil.copyfile(PLUGIN / relative, payload / relative)
    (scripts / "installation-context" / "installation-context.sh").write_text(
        'printf "%s\\n" "$FIXTURE_RESOLUTION"\n', encoding="utf-8", newline="\n",
    )
    (scripts / "resolve-runtime.sh").write_text(
        '[[ "${AGENT_RT_RESOLVE_TRACE_MODE:-}" == read-only ]] || return 97\n'
        'AGENT_RT_PY=""\n', encoding="utf-8", newline="\n",
    )
    canary = tmp_path / "installer-canary"
    (scripts / "init.sh").write_text(
        'printf installed > "$FIXTURE_CANARY"\nexit 91\n', encoding="utf-8", newline="\n",
    )
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"COPILOT_EXTENSIONS_CONTEXT", "AGENT_MACHINES_NO_SELFPROVISION"}
    }
    environment.update(
        FIXTURE_HOME=home.as_posix(), FIXTURE_CANARY=canary.as_posix(),
        FIXTURE_RESOLUTION=json.dumps({
            "status": "ready", "reason": "policy-default-false",
            "actualMode": "legacy", "desiredMode": "legacy",
        }),
    )
    arguments = (
        ["health", "run", "--store-dir", str(tmp_path / "store")]
        if operation == "health-run" else ["installation-configuration", "--project", "sample"]
    )
    if apply:
        arguments += ["--apply"]
        if operation == "health-run":
            arguments += ["--expect-policy", "a" * 64]
    result = subprocess.run(
        [bash, "-c",
         'export HOME="$(cd "$FIXTURE_HOME" && pwd)"\n'
         'getent() { printf "fixture:x:%s:0::%s:/bin/bash\\n" "$(id -u)" "$HOME"; }\n'
         'export -f getent\nexec bash "$@"',
         "fixture", str(payload / "bin" / "agent-machines"),
         *arguments],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=15,
        **no_window_kwargs(),
    )
    assert result.returncode == 1, (result.stdout, result.stderr)
    name = "Health collection" if operation == "health-run" else "Configuration"
    assert f"{name} requires an already provisioned runtime" in result.stderr
    assert not canary.exists() and not list(home.iterdir())
