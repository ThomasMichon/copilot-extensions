"""Guards for the fresh-session self-provisioning bootstrap."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.guard


def test_session_start_prefers_payload_bootstrap_with_installed_fallback() -> None:
    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    entries = hooks["hooks"]["sessionStart"]
    bootstrap = next(
        entry
        for entry in entries
        if "bootstrap-check" in entry.get("bash", "")
        or "bootstrap-check" in entry.get("powershell", "")
    )

    assert '$PWD/scripts/bootstrap-check.sh' in bootstrap["bash"]
    assert '$HOME/.agent-worktrees/bin/bootstrap-check.sh' in bootstrap["bash"]
    assert "scripts\\bootstrap-check.ps1" in bootstrap["powershell"]
    assert ".agent-worktrees\\bin\\bootstrap-check.ps1" in bootstrap["powershell"]


def test_bootstrap_stamps_payload_when_runtime_is_unprovisioned() -> None:
    sh = (PLUGIN / "scripts" / "bootstrap-check.sh").read_text(encoding="utf-8")
    ps1 = (PLUGIN / "scripts" / "bootstrap-check.ps1").read_text(encoding="utf-8")

    assert 'bash "$_installer" stamp' in sh
    assert "! _aw_provisioned" in sh
    assert "-File $installer stamp" in ps1
    assert "Test-AwProvisioned" in ps1
    assert "deploy-manifest.json" in sh
    assert "deploy-manifest.json" in ps1


def test_windows_binstub_resolves_complete_slots_and_serializes_provision() -> None:
    ps1 = (PLUGIN / "bin" / "agent-worktrees.ps1").read_text(encoding="utf-8")
    cmd = (PLUGIN / "bin" / "agent-worktrees.cmd").read_text(encoding="utf-8")

    assert "@('current-version', 'last-known-good')" in ps1
    assert ".install-complete.json" in ps1
    assert "Sort-Object LastWriteTimeUtc" in ps1
    assert "System.Threading.Mutex" in ps1
    assert 'agent-worktrees.ps1" %*' in cmd
    assert "current-version" in cmd
    assert "last-known-good" in cmd
    assert ".install-complete.json" in cmd
    assert "versions\\%_VER%\\Scripts\\python.exe" in cmd


def test_posix_binstub_resolves_only_active_or_complete_slots() -> None:
    sh = (PLUGIN / "bin" / "agent-worktrees").read_text(encoding="utf-8")

    assert "for _marker in current-version last-known-good" in sh
    assert ".install-complete.json" in sh
    assert "ls -1t" in sh
    assert '_aw_exec_resolved "$@"' in sh


def test_windows_stamp_reuses_immutable_version_snapshot() -> None:
    installer = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    stamp = installer.split("function Invoke-Stamp", 1)[1].split(
        "switch ($Action)", 1
    )[0]

    assert "if (-not (Test-Path $snapDir))" in stamp
    assert "Snapshot already stamped" in stamp
    assert "Remove-Item $snapDir" not in stamp
