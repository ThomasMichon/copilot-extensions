"""Shell installer scaffolds defer branch policy to the existing config layers."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from agent_worktrees import config, repos
from runtime_install_fixture import run_native_installer


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _powershell_path(path: Path, shell: str) -> str:
    raw = str(path)
    if os.name == "nt" or Path(shell).name.lower() != "powershell.exe":
        return raw
    converted = subprocess.run(
        ["wslpath", "-w", raw],
        capture_output=True,
        text=True,
        check=False,
    )
    return converted.stdout.strip() or raw


def test_native_windows_powershell_path_does_not_require_wslpath(tmp_path, monkeypatch):
    with monkeypatch.context() as native:
        native.setattr(os, "name", "nt")
        native.setattr(
            subprocess, "run", lambda *_args, **_kwargs: pytest.fail("native paths need no wslpath"),
        )
        assert _powershell_path(tmp_path, "powershell.exe") == str(tmp_path)


def _native_shell(shell):
    executable = shutil.which(shell)
    if not executable or (shell == "bash" and os.name == "nt"):
        pytest.skip(f"Native {shell} is unavailable")
    if shell == "powershell.exe" and config.detect_platform() == "wsl":
        pytest.skip("powershell.exe rewrites WSL paths into Windows-only UNC forms")
    return executable


@pytest.mark.real_installer_processes
@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh", "bash"])
def test_runtime_only_suppresses_existing_project_inference_without_changing_defaults(
    tmp_path, monkeypatch, shell,
):
    executable = _native_shell(shell)
    home = tmp_path / "home"
    anchor = tmp_path / "example"
    project = home / ".example"
    project.mkdir(parents=True)
    anchor.mkdir()
    configuration = project / "config.yaml"
    configuration.write_text(f"repos:\n  example:\n    anchor: {anchor}\n", encoding="utf-8")
    before = configuration.read_bytes()
    monkeypatch.chdir(anchor)
    env = {
        **os.environ, "HOME": str(home), "USERPROFILE": _powershell_path(home, shell),
        "TEST_ANCHOR": _powershell_path(anchor, shell),
    }
    if shell == "bash":
        source = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
        function = "_aw_resolve_installer_project_name() {" + source.split(
            "_aw_resolve_installer_project_name() {", 1,
        )[1].split("__aw_uninstall_native_path()", 1)[0]
        selection = source.split("# Priority: --project-name arg > existing config", 1)[1].split(
            'REPO_DIR=""', 1,
        )[0]
        script = (
            "set -euo pipefail\n" + function + "\nPROJECT_NAME_ARG=''\n"
            "for __aw_runtime_only in false true true; do\n" + selection +
            '\nprintf "PROJECT=%s\\n" "$PROJECT_NAME"\ndone\n'
        )
        argv = [executable, "-c", script]
    else:
        source = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
        function = "function Resolve-InstallerProjectName {" + source.split(
            "function Resolve-InstallerProjectName {", 1,
        )[1].split("# === install-contract:v4 payload-copy", 1)[0]
        selection = "# RepoDir: detect" + source.split(
            "# RepoDir: detect", 1,
        )[1].split("$DeploySourcePaths", 1)[0]
        script = (
            "$ErrorActionPreference = 'Stop'\nSet-StrictMode -Version Latest\n"
            "Set-Location -LiteralPath $env:TEST_ANCHOR\n" + function +
            "\nforeach ($RuntimeOnly in @($false, $true, $true)) {\n"
            "$ProjectName = $null\n" + selection +
            '\nWrite-Output "PROJECT=$ProjectName"\n}\n'
        )
        script_path = tmp_path / "runtime-selection.ps1"
        script_path.write_text(script, encoding="utf-8")
        argv = [executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path)]
    result = run_native_installer(argv, env=env, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert [line for line in result.stdout.splitlines() if line.startswith("PROJECT=")] == [
        "PROJECT=example", "PROJECT=", "PROJECT=",
    ]
    assert configuration.read_bytes() == before
    for name in (".agent-worktrees", ".local", ".copilot"):
        assert not (home / name).exists()


@pytest.mark.real_installer_processes
@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh", "bash"])
@pytest.mark.parametrize("conflict", ["project", "action"])
def test_runtime_only_frontdoor_refuses_before_native_admission(tmp_path, shell, conflict):
    executable = _native_shell(shell)
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "USERPROFILE": _powershell_path(home, shell)}
    action = "install" if conflict == "project" else "provision"
    if shell == "bash":
        argv = [executable, str(SCRIPTS / "install.sh"), action, "--runtime-only"]
        if conflict == "project":
            argv.extend(["--project-name", "example"])
    else:
        argv = [
            executable, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(SCRIPTS / "install.ps1"), action, "-RuntimeOnly",
        ]
        if conflict == "project":
            argv.extend(["-ProjectName", "example"])
    result = run_native_installer(argv, env=env, timeout=20)
    assert result.returncode != 0
    assert "requires install/update without" in result.stdout + result.stderr
    for name in (".agent-worktrees", ".example", ".local", ".copilot"):
        assert not (home / name).exists()


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh", "bash"])
def test_shell_scaffold_preserves_branch_authority(tmp_path, monkeypatch, shell):
    executable = shutil.which(shell)
    if not executable or (shell == "bash" and os.name == "nt"):
        pytest.skip(f"Native {shell} is unavailable")
    if shell == "powershell.exe" and config.detect_platform() == "wsl":
        pytest.skip("powershell.exe rewrites WSL paths into Windows-only UNC forms")

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
            f"$RepoDir = '{_powershell_path(anchor, shell)}'\n"
            f"$ProjectDir = '{_powershell_path(project, shell)}'\n"
            f"$InstallDir = '{_powershell_path(runtime, shell)}'\n"
            "$ProjectName = 'proj'\n"
            "$Force = $false\n" + function + "\nDeploy-Config test-machine\n"
        )
        script_path = tmp_path / "scaffold.ps1"
        script_path.write_text(script, encoding="utf-8")
        argv = [
            executable,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
        ]

    def generate():
        proc = subprocess.run(
            argv, env=env, capture_output=True, text=True, timeout=20
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr

    generate()
    path = project / "config.yaml"
    generated = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "default_branch" not in generated["repos"]["proj"]
    assert generated["repos"]["proj"]["anchor"] == _powershell_path(anchor, shell)

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
