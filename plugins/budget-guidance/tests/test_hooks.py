from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
PWSH = shutil.which("pwsh")

pytestmark = pytest.mark.guard


def test_session_start_is_bootstrap_only_and_output_free():
    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    entries = hooks["hooks"]["sessionStart"]
    assert len(entries) == 1
    entry = entries[0]
    assert "bootstrap-check" in entry["bash"]
    assert "bootstrap-check" in entry["powershell"]
    assert "invoke-context-contributor" not in json.dumps(entry)

    declaration = json.loads(
        (PLUGIN / "session-context.json").read_text(encoding="utf-8")
    )
    assert declaration["contributors"] == []
    assert declaration["sessionStart"] == {
        "sideEffects": "restart-safe-idempotent",
        "context": "none",
    }


def _python_disabled_environment(tmp_path: Path) -> dict[str, str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in ("python", "python3"):
        command = fake_bin / name
        command.write_text("#!/bin/sh\nexit 127\n", encoding="utf-8")
        command.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    # A project dir for COPILOT_PROJECT_DIR: the reconcile opt-in gate (added
    # alongside the copilot-extensions-wide hardening pass) resolves the
    # opt-in file relative to this, not to pytest's own (unpredictable, maybe
    # shared) cwd.
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
        "COPILOT_PLUGIN_ROOT": str(PLUGIN),
        "COPILOT_PROJECT_DIR": str(project),
        "COPILOT_EXTENSIONS_TEST_CONTAINED": "1",
        "COMPUTERNAME": "TEST-HOST",
    }


def _catalog_from_output(output: str) -> dict:
    outer = json.loads(output)
    match = re.search(r"```json\n(.*?)\n```", outer["additionalContext"], re.S)
    assert match
    return json.loads(match.group(1))


@pytest.mark.skipif(BASH is None or os.name == "nt", reason="POSIX hook coverage")
def test_posix_fresh_host_stamps_and_advertises_without_python(tmp_path: Path):
    env = _python_disabled_environment(tmp_path)

    bootstrap = subprocess.run(
        [BASH, str(PLUGIN / "scripts" / "bootstrap-check.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert bootstrap.returncode == 0, bootstrap.stdout + bootstrap.stderr
    assert (Path(env["HOME"]) / ".local" / "bin" / "budget-guidance").is_file()

    catalog = subprocess.run(
        [BASH, str(PLUGIN / "scripts" / "emit-command-catalog.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert catalog.returncode == 0, catalog.stdout + catalog.stderr
    value = _catalog_from_output(catalog.stdout)
    assert value["plugin"] == "budget-guidance"
    assert value["commands"][0]["availability"] == "ready"


@pytest.mark.skipif(PWSH is None or os.name == "nt", reason="portable pwsh hook coverage")
def test_powershell_catalog_remains_available_without_python(tmp_path: Path):
    env = _python_disabled_environment(tmp_path)

    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(PLUGIN / "scripts" / "emit-command-catalog.ps1"),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    value = _catalog_from_output(result.stdout)
    assert value["plugin"] == "budget-guidance"
    assert value["commands"][0]["availability"] == "ready"


@pytest.mark.skipif(BASH is None or os.name == "nt", reason="POSIX hook coverage")
def test_posix_update_reconciles_old_runtime_without_python(tmp_path: Path):
    env = _python_disabled_environment(tmp_path)
    # This test proves the reconcile itself works without python (the whole
    # point of the 'pythonless' family) -- it must still explicitly opt in to
    # the background-reconcile gate to actually reach that code path.
    project = Path(env["COPILOT_PROJECT_DIR"])
    (project / ".copilot-extensions").mkdir(parents=True, exist_ok=True)
    (project / ".copilot-extensions" / "config.yaml").write_text(
        "background_reconcile_budget-guidance: true\n", encoding="utf-8"
    )
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(PLUGIN / "scripts" / "bootstrap-check.sh", scripts)
    (payload / "pyproject.toml").write_text(
        '[project]\nversion = "0.1.0-dev5"\n',
        encoding="utf-8",
    )
    installer = scripts / "install.sh"
    installer.write_text(
        """#!/usr/bin/env bash
set -eu
root="$HOME/.budget-guidance"
mkdir -p "$root/snapshots/0.1.0-dev5" "$root/versions/0.1.0-dev5/bin"
printf '%s\n' "$root/snapshots/0.1.0-dev5" > "$root/payload-dir"
printf '%s\n' provisioned > "$root/snapshots/0.1.0-dev5/result"
printf '%s\n' 0.1.0-dev5 > "$root/current-version"
cat > "$root/deploy-manifest.json" <<'EOF'
{
  "schema_version": 3,
  "source": {
    "version": "0.1.0-dev5"
  }
}
EOF
""",
        encoding="utf-8",
    )
    installer.chmod(0o755)

    root = Path(env["HOME"]) / ".budget-guidance"
    old_slot = root / "versions" / "0.1.0-dev4"
    (old_slot / "bin").mkdir(parents=True)
    old_python = old_slot / "bin" / "python"
    old_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    old_python.chmod(0o755)
    (old_slot / ".install-complete.json").write_text(
        '{"version": "0.1.0-dev4", "completed_at": "2030-01-01T00:00:00Z", "pid": 1}',
        encoding="utf-8",
    )
    (root / "current-version").write_text("0.1.0-dev4\n", encoding="utf-8")
    (root / "deploy-manifest.json").write_text(
        """{
  "schema_version": 3,
  "source": {
    "version": "0.1.0-dev4"
  }
}
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [BASH, str(scripts / "bootstrap-check.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        manifest = (root / "deploy-manifest.json").read_text(encoding="utf-8")
        if '"version": "0.1.0-dev5"' in manifest:
            break
        time.sleep(0.05)
    assert '"version": "0.1.0-dev5"' in manifest
    assert (root / "snapshots" / "0.1.0-dev5" / "result").is_file()
    assert (root / "current-version").read_text(encoding="utf-8").strip() == "0.1.0-dev5"


def test_powershell_bootstrap_version_reconciliation_is_python_independent():
    source = (PLUGIN / "scripts" / "bootstrap-check.ps1").read_text(encoding="utf-8")
    assert "ConvertFrom-Json" in source
    assert "Get-Command python" not in source
