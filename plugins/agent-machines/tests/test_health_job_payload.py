"""Real selected-interpreter compatibility entry; no scheduler or live runtime."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import venv

import pytest
from agent_procutil import no_window_kwargs

from .test_invoke_payload_runtime_lock import _bash

PLUGIN = Path(__file__).resolve().parents[1]
HOSTS = [path for name in ("powershell.exe", "pwsh.exe") if (path := shutil.which(name))]
pytestmark = [
    pytest.mark.portfolio_tier("T2"), pytest.mark.effect("process"),
    pytest.mark.contract("agent_machines.health_job_payload"),
]


@pytest.mark.parametrize("kind", ["powershell", "bash"])
def test_fresh_payload_selection_checks_capability_before_new_syntax_and_rechecks_rollback(tmp_path, kind):
    if kind == "powershell" and (os.name != "nt" or not HOSTS):
        pytest.skip("native Windows PowerShell required")
    if kind == "bash" and not _bash():
        pytest.skip("Bash unavailable")
    runtime = tmp_path / "runtime"
    venv.EnvBuilder(with_pip=False).create(runtime)
    python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    purelib = runtime / ("Lib/site-packages" if os.name == "nt" else f"lib/python{os.sys.version_info.major}.{os.sys.version_info.minor}/site-packages")
    package = purelib / "agent_machines"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import json, os, pathlib, sys\n"
        "pathlib.Path(os.environ['FIXTURE_CANARY']).write_text('called')\n"
        "print(json.dumps({'arguments': sys.argv[1:]}))\n",
        encoding="utf-8",
    )
    payload, home = tmp_path / "payload", tmp_path / "home"
    scripts = payload / "scripts"
    (scripts / "installation-context").mkdir(parents=True)
    (payload / "bin").mkdir()
    home.mkdir()
    (payload / "plugin.json").write_text('{"name":"agent-machines"}', encoding="utf-8")
    shutil.copyfile(PLUGIN / "scripts" / "health-collection-entry.py", scripts / "health-collection-entry.py")
    extension = "ps1" if kind == "powershell" else "sh"
    for relative in (
        Path("scripts") / f"invoke-payload-runtime.{extension}",
        Path("bin") / ("agent-machines.ps1" if kind == "powershell" else "agent-machines"),
    ):
        shutil.copyfile(PLUGIN / relative, payload / relative)
    if kind == "powershell":
        (scripts / "installation-context" / "installation-context.ps1").write_text(
            "[Console]::Out.WriteLine($env:FIXTURE_RESOLUTION)\nexit 0\n", encoding="utf-8",
        )
        (scripts / "resolve-runtime.ps1").write_text(
            "param([string]$TraceMode)\n"
            "if ($TraceMode -cne 'read-only') { throw 'read-only selection required' }\n"
            "$AgentRtPy = $env:FIXTURE_PYTHON\n",
            encoding="utf-8",
        )
        command = [
            HOSTS[0], "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(payload / "bin" / "agent-machines.ps1"),
        ]
    else:
        shutil.copyfile(
            PLUGIN / "scripts" / "installation-context" / "json-query.awk",
            scripts / "installation-context" / "json-query.awk",
        )
        (scripts / "installation-context" / "installation-context.sh").write_text(
            'printf "%s\\n" "$FIXTURE_RESOLUTION"\n', encoding="utf-8", newline="\n",
        )
        (scripts / "resolve-runtime.sh").write_text(
            '[[ "$AGENT_RT_RESOLVE_TRACE_MODE" == read-only ]] || return 97\n'
            'AGENT_RT_PY="$FIXTURE_PYTHON"\n', encoding="utf-8", newline="\n",
        )
        command = [
            _bash(), "-c",
            'export HOME="$(cd "$FIXTURE_HOME" && pwd)"\n'
            'getent() { printf "fixture:x:%s:0::%s:/bin/bash\\n" "$(id -u)" "$HOME"; }\n'
            'export -f getent\nexec bash "$@"',
            "fixture", str(payload / "bin" / "agent-machines"),
        ]
    canary = tmp_path / "provider-called"
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"COPILOT_EXTENSIONS_CONTEXT", "AGENT_MACHINES_NO_SELFPROVISION"}
    }
    environment.update(
        HOME=str(home), USERPROFILE=str(home), FIXTURE_HOME=home.as_posix(),
        FIXTURE_PYTHON=python.as_posix(), FIXTURE_CANARY=str(canary),
        FIXTURE_RESOLUTION=json.dumps({
            "status": "ready", "reason": "policy-default-false",
            "actualMode": "legacy", "desiredMode": "legacy",
        }),
    )
    declaration = package / "health-collection-v1.json"
    support = {"schema": "agent-machines.health.collection", "version": 1, "operations": ["preview", "apply"]}
    arguments = ["health", "prepare-job", "--store-dir", str(tmp_path / "store")]

    def invoke(eligible):
        result = subprocess.run(
            [*command, *arguments], cwd=tmp_path, env=environment,
            capture_output=True, text=True, timeout=20, **no_window_kwargs(),
        )
        if eligible:
            assert result.returncode == 0, (result.stdout, result.stderr)
            assert json.loads(result.stdout)["arguments"] == arguments
            assert canary.read_text() == "called"
            canary.unlink()
        else:
            assert result.returncode == 2, (result.stdout, result.stderr)
            assert json.loads(result.stderr)["error"] == "collection-contract-unsupported"
            assert not canary.exists()

    invoke(False)
    declaration.write_text(json.dumps(support), encoding="utf-8")
    invoke(False)
    support["operations"].append("prepare-job")
    declaration.write_text(json.dumps(support), encoding="utf-8")
    invoke(True)
    arguments[:] = ["health", "run", "--store-dir", str(tmp_path / "store"), "--apply", "--expect-policy", "a" * 64]
    invoke(True)
    declaration.unlink()
    invoke(False)
