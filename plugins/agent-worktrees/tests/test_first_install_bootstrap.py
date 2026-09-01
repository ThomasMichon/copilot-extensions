"""Guards for the fresh-session self-provisioning bootstrap."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
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

    assert '${COPILOT_PLUGIN_ROOT:-$PWD}' in bootstrap["bash"]
    assert 's="$r/scripts/bootstrap-check.sh"' in bootstrap["bash"]
    assert '$HOME/.agent-worktrees/bin/bootstrap-check.sh' in bootstrap["bash"]
    assert "$env:COPILOT_PLUGIN_ROOT" in bootstrap["powershell"]
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

    assert "resolve-runtime.ps1" in ps1
    assert "System.Threading.Mutex" in ps1
    assert 'agent-worktrees.ps1" %*' in cmd
    assert "powershell" in cmd


def test_posix_binstub_resolves_only_active_or_complete_slots() -> None:
    sh = (PLUGIN / "bin" / "agent-worktrees").read_text(encoding="utf-8")

    assert "resolve-runtime.sh" in sh
    assert '_aw_exec_resolved "$@"' in sh


def test_direct_posix_payload_entrypoints_are_tracked_executable() -> None:
    repo = PLUGIN.parents[1]
    paths = (
        "plugins/agent-worktrees/bin/agent-worktrees",
        "plugins/agent-worktrees/bin/launch-session.sh",
        "plugins/agent-worktrees/bin/pane-wrapper.sh",
        "plugins/agent-worktrees/bin/payload/agent-worktrees",
    )
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "--stage", "--", *paths],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip("Git index metadata is unavailable")
    modes = {
        line.split(maxsplit=1)[1].split("\t", maxsplit=1)[1]: line.split()[0]
        for line in result.stdout.splitlines()
    }
    assert modes == {path: "100755" for path in paths}


def test_lean_provision_deploys_runtime_resolvers() -> None:
    sh = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    sh_provision = sh.split("    provision)", 1)[1].split("    install)", 1)[0]
    sh_deploy = sh.split("deploy_runtime_resolvers() {", 1)[1].split(
        "deploy_wrappers() {", 1
    )[0]
    assert "deploy_runtime_resolvers || exit 1" in sh_provision
    assert 'mktemp "$BIN_DIR/$resolver.XXXXXX"' in sh_deploy
    assert 'chmod +x "$tmp"' in sh_deploy
    assert 'mv -f "$tmp" "$BIN_DIR/$resolver"' in sh_deploy

    ps1 = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    ps1_provision = ps1.split("    'provision' {", 1)[1].split(
        "    'install' {", 1
    )[0]
    ps1_deploy = ps1.split("function Deploy-RuntimeResolvers {", 1)[1].split(
        "function Deploy-Binstub {", 1
    )[0]
    assert "Deploy-RuntimeResolvers" in ps1_provision
    assert "Copy-Item $src $tmp -Force" in ps1_deploy
    assert "Move-Item $tmp $dst -Force" in ps1_deploy


def test_installers_preserve_activation_during_inventory_bootstrap() -> None:
    sh = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    ps1 = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")

    assert "-m agent_worktrees.activation_preservation" in sh
    assert "-m agent_worktrees.activation_preservation" in ps1
    assert "copilot plugin install agent-worktrees@copilot-extensions" not in sh
    assert "copilot plugin install agent-worktrees@copilot-extensions" not in ps1


def test_generated_project_binstubs_use_shared_three_tier_resolvers() -> None:
    sh = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    ps1 = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")

    assert 'source "\\$_root/bin/resolve-runtime.sh"' in sh
    assert "resolve-runtime.ps1" in ps1
    assert "last-known-good" not in sh.split("deploy_binstub() {", 1)[1].split(
        "deploy_global_config()", 1
    )[0]


def test_launch_session_cmd_preserves_windows_powershell_fallback() -> None:
    cmd = (PLUGIN / "bin" / "launch-session.cmd").read_text(encoding="utf-8")

    assert "where pwsh" in cmd
    assert 'set "_PSHOST=powershell"' in cmd
    assert '"%_PSHOST%"' in cmd


def test_windows_stamp_reuses_immutable_version_snapshot() -> None:
    installer = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    stamp = installer.split("function Invoke-Stamp", 1)[1].split(
        "switch ($Action)", 1
    )[0]

    assert "if (-not (Test-Path $snapDir))" in stamp
    assert "Snapshot already stamped" in stamp
    assert "Remove-Item $snapDir" not in stamp


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer integration")
def test_posix_lean_provision_installs_resolver_and_launchers_reenter_runtime(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    payload = (
        home
        / ".copilot"
        / "installed-plugins"
        / "copilot-extensions"
        / "agent-worktrees"
    )
    shutil.copytree(PLUGIN, payload)

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        """#!/usr/bin/env bash
set -eu
case "${1:-}" in
  venv)
    target="$2"
    mkdir -p "$target/bin" "$target/fake-site/agent_worktrees"
    cat > "$target/bin/python" <<'PY'
#!/bin/sh
set -eu
slot_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
if [ "${1:-}" = "-c" ]; then
  case "${2:-}" in
    *"import agent_worktrees, os"*)
      printf '%s\n' "$slot_dir/fake-site/agent_worktrees"
      exit 0
      ;;
    *"import agent_worktrees"*) exit 0 ;;
  esac
fi
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "agent_worktrees" ]; then
  shift 2
  printf 'fake-agent-worktrees %s\n' "$*"
  exit 0
fi
exec "$REAL_PYTHON" "$@"
PY
    chmod +x "$target/bin/python"
    ;;
  pip) ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "REAL_PYTHON": sys.executable,
            "COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": "0",
        }
    )
    provision = subprocess.run(
        ["bash", str(payload / "scripts" / "install.sh"), "provision"],
        cwd=home,
        env=env,
        capture_output=True,
        text=True,
    )
    assert provision.returncode == 0, provision.stderr

    runtime_bin = home / ".agent-worktrees" / "bin"
    resolver = runtime_bin / "resolve-runtime.sh"
    assert resolver.read_bytes() == (payload / "scripts" / resolver.name).read_bytes()
    assert resolver.stat().st_uid == os.getuid()
    assert resolver.stat().st_mode & stat.S_IXUSR

    launchers = [
        home / ".local" / "bin" / "agent-worktrees",
        payload / "bin" / "payload" / "agent-worktrees",
    ]
    for launcher in launchers:
        launched = subprocess.run(
            [str(launcher), "--version"],
            cwd=home,
            env={**env, "AGENT_WORKTREES_NO_SELFPROVISION": "1"},
            capture_output=True,
            text=True,
        )
        assert launched.returncode == 0, launched.stderr
        assert launched.stdout.strip() == "fake-agent-worktrees --version"
