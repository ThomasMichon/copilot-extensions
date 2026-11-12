"""Readiness dispatch uses installed modules without importing project code."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Any

import pytest

from test_adopter_publication import _adopter
from test_command_binding import ROOT, binding_fixture as _binding_fixture
from test_native_publication import _completed_slot

pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.effect("process")]
binding_fixture = _binding_fixture


@pytest.mark.parametrize(("plugin", "qualified"), [
    (plugin, qualified)
    for plugin in (
        "agent-machines", "agent-worktrees", "agent-bridge", "agent-dispatch", "agent-mcp",
    )
    for qualified in (False, True)
])
@pytest.mark.parametrize("command", ("installer-readiness", "invocation-contract"))
@pytest.mark.parametrize("shell", ("bash", "powershell", "pwsh"))
def test_readiness_dispatcher_preserves_project_without_importing_it(
    binding_fixture: dict[str, Any], plugin: str, qualified: bool, command: str, shell: str,
) -> None:
    if shell == "bash" and os.name == "nt":
        pytest.skip("POSIX dispatcher requires native POSIX Python")
    host = shutil.which(shell)
    if not host:
        pytest.skip(f"{shell} is unavailable")
    environment = _adopter(binding_fixture, plugin)
    payload, home = binding_fixture["payload"], binding_fixture["home"]
    shutil.copytree(ROOT / "plugins" / plugin / "bin", payload / "bin")
    scripts = payload / "scripts"
    project = home / "selected-project"
    project.mkdir()
    marker = home / "checkout-import"
    package = plugin.replace("-", "_")
    (project / f"{package}.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
        "raise RuntimeError('checkout module ran')\n", encoding="utf-8",
    )
    python = _completed_slot(binding_fixture)
    declaration = json.dumps({
        "schema": "copilot-extensions.readiness-isolation", "version": 1,
        "plugin": plugin, "module": package,
    })
    (payload / "readiness-isolation.json").write_text(declaration, encoding="utf-8")
    descriptor = (python.parent.parent / "share" / "copilot-extensions" /
                  package / "readiness-isolation.json")
    if qualified:
        descriptor.parent.mkdir(parents=True)
        descriptor.write_text(declaration, encoding="utf-8")
    site = (
        python.parent.parent / "Lib/site-packages" if os.name == "nt"
        else python.parent.parent / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    )
    provider_marker = home / "provider-executed"
    (site / f"{package}.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        f"Path({str(provider_marker)!r}).touch()\n"
        "print(json.dumps({'args':sys.argv[1:],'cwd':os.getcwd(),"
        "'isolated':sys.flags.isolated,'no_bytecode':sys.flags.dont_write_bytecode,"
        "'utf8':sys.flags.utf8_mode,'no_site':sys.flags.no_site}))\n",
        encoding="utf-8",
    )
    environment.update({
        plugin.upper().replace("-", "_") + "_PAYLOAD_ROOT": str(payload),
        "TEST_RUNTIME_PYTHON": str(python), "PYTHONPATH": str(project),
        "TEST_STATUS": json.dumps({
            "status": "ready", "reason": "policy-default-false",
            "actualMode": "legacy", "desiredMode": "legacy",
            "legacy": {"root": str(binding_fixture["legacy"])},
        }),
    })
    if shell == "bash":
        (scripts / "installation-context/installation-context.sh").write_text(
            'printf "%s\\n" "$TEST_STATUS"\n', encoding="utf-8",
        )
        (scripts / "resolve-runtime.sh").write_text(
            '# Trace contract: normal/read-only/ready-v1\n'
            'AGENT_RT_PY="$TEST_RUNTIME_PYTHON"\n', encoding="utf-8",
        )
        entry = "invoke-payload-runtime" if plugin in {"agent-machines", "agent-worktrees"} else "runtime-gate"
        argv = [host, str(scripts / f"{entry}.sh"), command]
    else:
        (scripts / "installation-context/installation-context.ps1").write_text(
            "$env:TEST_STATUS\nexit 0\n", encoding="utf-8",
        )
        (scripts / "resolve-runtime.ps1").write_text(
            "# Trace contract: normal/read-only/ready-v1\n"
            "$AgentRtPy=$env:TEST_RUNTIME_PYTHON\n", encoding="utf-8",
        )
        entry = "invoke-payload-runtime" if plugin in {"agent-machines", "agent-worktrees"} else "runtime-gate"
        argv = [host, "-NoProfile", "-NonInteractive", "-File",
                str(scripts / f"{entry}.ps1"), command]
    before = {str(p.relative_to(payload)): p.read_bytes() for p in payload.rglob("*") if p.is_file()}
    result = subprocess.run(
        argv, env=environment, cwd=project, capture_output=True, text=True,
        encoding="utf-8", timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if qualified:
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout) == {
            "args": [command], "cwd": str(project),
            "isolated": 1, "no_bytecode": 1, "utf8": 1, "no_site": 0,
        }
        assert provider_marker.exists()
    else:
        assert result.returncode == 126, result.stdout + result.stderr
        assert json.loads(result.stdout)["status"] == "unknown"
        assert json.loads(result.stdout)["recovery"] == "update-owning-installation"
        assert not provider_marker.exists()
    assert not marker.exists()
    assert not list(project.rglob("*.pyc"))
    assert not list(site.rglob("*.pyc"))
    assert before == {str(p.relative_to(payload)): p.read_bytes() for p in payload.rglob("*") if p.is_file()}
