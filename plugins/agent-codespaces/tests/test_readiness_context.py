"""Tests for the readiness-context sessionStart hook (scripts/readiness-context.sh).

An explicit READY means either the payload-local self-provisioning command is
usable or a legacy binstub has a complete runtime. Every other state reports
NOT READY with a next step. The hook must run WITHOUT the plugin's own venv, so
these tests drive the pure-shell script under a throwaway HOME and assert on the
emitted additionalContext JSON.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "readiness-context.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(
    BASH is None or os.name == "nt",
    reason="readiness-context.sh is a bash hook; needs a POSIX bash (skipped on Windows)",
)


def _run(home: Path, *, payload_command: bool = True) -> dict:
    """Run the hook with HOME=<tmp> and a staged plugin dir; return parsed JSON."""
    plugin_dir = home / ".copilot/installed-plugins/copilot-extensions/agent-codespaces"
    plugin_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SCRIPT.parents[1], plugin_dir)
    if not payload_command:
        (plugin_dir / "bin" / "agent-codespaces").unlink()
    env = dict(os.environ, HOME=str(home))
    proc = subprocess.run(
        [BASH, str(plugin_dir / "scripts" / "readiness-context.sh")],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_fresh_install_reports_payload_command_ready(tmp_path: Path):
    """The checked-in payload command is ready to provision on first use."""
    ctx = _run(tmp_path)["additionalContext"]
    assert "READY" in ctx
    assert "NOT READY" not in ctx
    assert "payload-local command available" in ctx
    assert "first use" in ctx


def test_provisioning_in_flight_keeps_payload_command_ready(tmp_path: Path):
    """An incomplete runtime does not block the self-provisioning payload command."""
    inst = tmp_path / ".agent-codespaces"
    inst.mkdir(parents=True)
    (inst / "deploy-manifest.json").write_text("{}")
    (inst / "current-version").write_text("9.9.9")
    ctx = _run(tmp_path)["additionalContext"]
    assert "READY" in ctx
    assert "payload-local command available" in ctx
    assert "first use" in ctx


def test_provisioned_reports_payload_command_ready(tmp_path: Path):
    """A payload command reports the current complete runtime when present."""
    inst = tmp_path / ".agent-codespaces"
    (inst / "versions/9.9.9/bin").mkdir(parents=True)
    (inst / "current-version").write_text("9.9.9")
    py = inst / "versions/9.9.9/bin/python"
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    binroot = tmp_path / ".local/bin"
    binroot.mkdir(parents=True)
    stub = binroot / "agent-codespaces"
    stub.write_text("#!/bin/sh\n")
    stub.chmod(0o755)
    ctx = _run(tmp_path)["additionalContext"]
    assert "READY" in ctx
    assert "NOT READY" not in ctx
    assert "9.9.9" in ctx


def test_missing_payload_command_and_runtime_reports_not_ready(tmp_path: Path):
    """Without either invocation surface, readiness remains fail-closed."""
    ctx = _run(tmp_path, payload_command=False)["additionalContext"]
    assert "NOT READY" in ctx
    assert "not provisioned" in ctx
