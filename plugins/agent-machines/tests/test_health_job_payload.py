"""Real selected-interpreter compatibility entry; no scheduler or live runtime."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from agent_procutil import no_window_kwargs

from .test_invoke_payload_runtime_lock import _bash
from .managed_command_fixture import (
    admission_support, packaged_command, prepare_application, prepared_python,
)

__all__ = ["packaged_command", "prepared_python"]

PLUGIN = Path(__file__).resolve().parents[1]
HOSTS = {name: path for name in ("powershell", "pwsh") if (path := shutil.which(name + ".exe"))}
pytestmark = [
    pytest.mark.portfolio_tier("T2"), pytest.mark.effect("process"),
    pytest.mark.contract("agent_machines.health_job_payload"),
]


@pytest.mark.parametrize("kind", ["powershell", "pwsh", "bash"])
def test_fresh_payload_selection_checks_capability_before_new_syntax_and_rechecks_rollback(
    tmp_path: Path, kind: str, prepared_python: tuple[Path, Path, Path], packaged_command: Path,
) -> None:
    if kind != "bash" and (os.name != "nt" or kind not in HOSTS):
        pytest.skip("native Windows PowerShell required")
    if kind == "bash" and not _bash():
        pytest.skip("Bash unavailable")
    runtime = tmp_path / "runtime"
    uv, application, store = prepared_python
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"COPILOT_EXTENSIONS_CONTEXT", "AGENT_MACHINES_NO_SELFPROVISION"}
    }
    environment.update(
        UV_PYTHON_INSTALL_DIR=str(store), UV_OFFLINE="1", UV_NO_CONFIG="1",
        UV_PYTHON_DOWNLOADS="never",
    )
    python = prepare_application(uv, application, runtime, environment)
    purelib = runtime / ("Lib/site-packages" if os.name == "nt" else "lib/python3.10/site-packages")
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
    admission_support(payload, packaged_command)
    (payload / "bin").mkdir()
    home.mkdir()
    (payload / "plugin.json").write_text('{"name":"agent-machines"}', encoding="utf-8")
    shutil.copyfile(PLUGIN / "scripts" / "health-collection-entry.py", scripts / "health-collection-entry.py")
    extension = "sh" if kind == "bash" else "ps1"
    for relative in (
        Path("scripts") / f"invoke-payload-runtime.{extension}",
        Path("bin") / ("agent-machines" if kind == "bash" else "agent-machines.ps1"),
    ):
        shutil.copyfile(PLUGIN / relative, payload / relative)
    if kind != "bash":
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
            HOSTS[kind], "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(payload / "bin" / "agent-machines.ps1"),
        ]
    else:
        bash = _bash()
        assert bash is not None
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
            bash, "-c",
            'export HOME="$(cd "$FIXTURE_HOME" && pwd)"\n'
            'getent() { printf "fixture:x:%s:0::%s:/bin/bash\\n" "$(id -u)" "$HOME"; }\n'
            'export -f getent\nexec bash "$@"',
            "fixture", str(payload / "bin" / "agent-machines"),
        ]
    canary = tmp_path / "provider-called"
    environment.update(
        HOME=str(home), USERPROFILE=str(home), FIXTURE_HOME=home.as_posix(),
        FIXTURE_PYTHON=python.as_posix() if kind == "bash" else str(python),
        FIXTURE_CANARY=str(canary),
        FIXTURE_RESOLUTION=json.dumps({
            "status": "ready", "reason": "policy-default-false",
            "actualMode": "legacy", "desiredMode": "legacy",
        }),
    )
    declaration = package / "health-collection-v1.json"
    operations = ["preview", "apply"]
    support = {"schema": "agent-machines.health.collection", "version": 1, "operations": operations}
    arguments = ["health", "prepare-job", "--store-dir", str(tmp_path / "store")]

    def invoke(eligible: bool) -> None:
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
    operations.append("prepare-job")
    declaration.write_text(json.dumps(support), encoding="utf-8")
    invoke(True)
    arguments[:] = ["health", "run", "--store-dir", str(tmp_path / "store"), "--apply", "--expect-policy", "a" * 64]
    invoke(True)
    declaration.unlink()
    invoke(False)
