"""Shell installer scaffolds defer branch policy to the existing config layers."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from agent_worktrees import config, repos


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh", "bash"])
def test_shell_scaffold_preserves_branch_authority(tmp_path, monkeypatch, shell):
    executable = shutil.which(shell)
    if not executable or (shell == "bash" and os.name == "nt"):
        pytest.skip(f"Native {shell} is unavailable")

    anchor = tmp_path / "proj"
    project = tmp_path / "project-config"
    runtime = tmp_path / "runtime"
    for directory in (anchor, project, runtime):
        directory.mkdir()
    env = {
        **os.environ,
        "TEST_REPO": str(anchor),
        "TEST_PROJECT": str(project),
        "TEST_RUNTIME": str(runtime),
    }
    if shell == "bash":
        source = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
        functions = source.split("deploy_global_config() {", 1)[1].split(
            "write_deploy_manifest() {", 1
        )[0]
        script = (
            'set -euo pipefail\n'
            'changed() { :; }\nskipped() { :; }\n'
            'REPO_DIR="$TEST_REPO"\nPROJECT_DIR="$TEST_PROJECT"\n'
            'INSTALL_DIR="$TEST_RUNTIME"\nPROJECT_NAME=proj\nFORCE=false\n'
            "deploy_global_config() {" + functions +
            '\ndeploy_config test-machine linux || test "$?" = 1\n'
        )
        argv = [executable, "-c", script]
    else:
        source = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
        function = "function Deploy-Config {" + source.split(
            "function Deploy-Config {", 1
        )[1].split("function Deploy-TerminalScripts", 1)[0]
        script = (
            "$ErrorActionPreference = 'Stop'\n"
            "Set-StrictMode -Version Latest\n"
            "function Write-ServiceChanged {}\nfunction Write-ServiceSkipped {}\n"
            "$RepoDir = $env:TEST_REPO\n$ProjectDir = $env:TEST_PROJECT\n"
            "$InstallDir = $env:TEST_RUNTIME\n$ProjectName = 'proj'\n"
            "$Force = $false\n" + function + "\nDeploy-Config test-machine\n"
        )
        script_path = tmp_path / "scaffold.ps1"
        script_path.write_text(script, encoding="utf-8")
        argv = [executable, "-NoProfile", "-File", str(script_path)]

    def generate():
        proc = subprocess.run(
            argv, env=env, capture_output=True, text=True, timeout=20
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr

    generate()
    path = project / "config.yaml"
    generated = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "default_branch" not in generated["repos"]["proj"]
    assert generated["repos"]["proj"]["anchor"] == str(anchor)

    entry = repos.RepoEntry(name="proj", repo_class="worktree")
    monkeypatch.setattr(
        repos, "read_registry", lambda: repos.ReposRegistry(repos={"proj": entry})
    )
    repo_config = anchor / ".copilot-extensions" / "agent-worktrees" / "config.yaml"
    repo_config.parent.mkdir(parents=True)
    for branch in ("main", "master", "trunk"):
        entry.default_branch = branch
        assert config.load_config(path).default_repo.default_branch == branch
        repo_config.write_text("default_branch: integration\n", encoding="utf-8")
        assert config.load_config(path).default_repo.default_branch == "integration"
        repo_config.unlink()

    generated["repos"]["proj"]["default_branch"] = "release"
    path.write_text(yaml.safe_dump(generated), encoding="utf-8")
    before = path.read_bytes()
    generate()
    assert path.read_bytes() == before
    assert config.load_config(path).default_repo.default_branch == "release"
