from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
INSTALLER = PLUGIN / "scripts" / "install.ps1"
SHELL_INSTALLER = PLUGIN / "scripts" / "install.sh"
ENGINE_PS1 = PLUGIN.parents[1] / "libs" / "installer-engine" / "installer-engine.ps1"
PWSH = shutil.which("pwsh")
# A bare shutil.which("bash") can resolve to a Windows App Execution Alias
# stub or the classic `C:\Windows\System32\bash.exe` WSL launcher (both
# invoke an actual WSL distro rather than running this script in the
# environment under test). Prefer the real Git Bash location when present;
# otherwise filter both known WSL-launcher locations out of PATH before
# falling back to shutil.which, so this never silently selects one. See the
# agent-bridge/agent-codespaces sibling tests for the same pattern.
_GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")


def _resolve_bash() -> str | None:
    if _GIT_BASH.is_file():
        return str(_GIT_BASH)
    path = os.environ.get("PATH")
    if not path:
        return None
    filtered = os.pathsep.join(
        part for part in path.split(os.pathsep)
        if "windowsapps" not in part.lower()
        and part.rstrip("\\").lower() != r"c:\windows\system32"
    )
    return shutil.which("bash", path=filtered)


BASH = _resolve_bash()

pytestmark = pytest.mark.guard


def _function_source(source: str, name: str, next_marker: str) -> str:
    start_marker = f"function {name} {{"
    assert source.count(start_marker) == 1, f"missing unique {start_marker!r}"
    assert next_marker in source, f"missing delimiter {next_marker!r}"

    start = source.index(start_marker)
    end = source.index(next_marker, start + len(start_marker))
    assert end > start, f"{next_marker!r} does not follow {start_marker!r}"
    return source[start:end]


def _shell_function_source(source: str, name: str, next_marker: str) -> str:
    start_marker = f"{name}() {{"
    assert source.count(start_marker) == 1, f"missing unique {start_marker!r}"
    assert next_marker in source, f"missing delimiter {next_marker!r}"

    start = source.index(start_marker)
    end = source.index(next_marker, start + len(start_marker))
    assert end > start, f"{next_marker!r} does not follow {start_marker!r}"
    return source[start:end]


@pytest.mark.skipif(
    os.name != "nt" or PWSH is None,
    reason="Windows PowerShell installer coverage",
)
def test_package_install_falls_back_when_resolved_uv_cannot_launch(
    tmp_path: Path,
) -> None:
    installer = INSTALLER.read_text(encoding="utf-8")
    install_package = _function_source(
        installer,
        "Install-AgentSshPackage",
        "\n$PluginDir =",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "uv.exe").write_bytes(b"not a Windows executable")
    marker = tmp_path / "pip-fallback-ran"
    fake_python = fake_bin / "python.cmd"
    fake_python.write_text(
        f'@echo %*>"{marker}"\n@exit /b 0\n',
        encoding="ascii",
    )
    dependency_a = tmp_path / "dependency-a"
    dependency_b = tmp_path / "dependency-b"
    dependency_a.mkdir()
    dependency_b.mkdir()

    def ps_quote(value: str) -> str:
        return value.replace("'", "''")

    script = tmp_path / "fallback.ps1"
    script.write_text(
        "\n".join(
            [
                "$ErrorActionPreference = 'Stop'",
                "function Write-Step { param([string]$Msg) Write-Host $Msg }",
                "function Write-Warn { param([string]$Msg) Write-Host $Msg }",
                f". '{ps_quote(str(ENGINE_PS1))}'",
                install_package,
                "function Resolve-VenueCopilot { return '__unused__' }",
                (
                    "$ok = Install-AgentSshPackage "
                    f"-Python '{ps_quote(str(fake_python))}' "
                    f"-Source '{ps_quote(str(tmp_path))}' "
                    "-Dependencies @("
                    f"'{ps_quote(str(dependency_a))}',"
                    f"'{ps_quote(str(dependency_b))}'"
                    ") "
                    f"-UvCommand '{ps_quote(str(fake_bin / 'uv.exe'))}'"
                ),
                "if (-not $ok) { throw 'fallback install failed' }",
            ]
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
    }

    proc = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "falling back to python -m pip" in proc.stdout
    assert marker.is_file()
    fallback_args = marker.read_text(encoding="ascii")
    assert str(dependency_a) in fallback_args
    assert str(dependency_b) in fallback_args
    assert str(tmp_path) in fallback_args


@pytest.mark.skipif(
    os.name == "nt" or BASH is None,
    reason="POSIX shell installer coverage",
)
def test_shell_pip_fallback_includes_vendored_dependencies(tmp_path: Path) -> None:
    installer = SHELL_INSTALLER.read_text(encoding="utf-8")
    resolve_vendored_lib = _shell_function_source(
        installer, "_resolve_vendored_lib", "\n_resolve_ssh_manager()",
    )
    install_package = _shell_function_source(
        installer,
        "_install_agent_ssh_package",
        '\nACTION="${AGENT_SSH_ACTION:-install}"',
    )

    plugin = tmp_path / "plugin"
    (plugin / "libs" / "agent-procutil").mkdir(parents=True)
    (plugin / "libs" / "agent-procutil" / "pyproject.toml").write_text("", encoding="utf-8")
    (plugin / "libs" / "dropin-registry").mkdir(parents=True)
    (plugin / "libs" / "ssh-manager").mkdir(parents=True)
    (plugin / "libs" / "ssh-manager" / "pyproject.toml").write_text("", encoding="utf-8")
    (plugin / "libs" / "venue-copilot").mkdir(parents=True)
    (plugin / "libs" / "venue-copilot" / "pyproject.toml").write_text("", encoding="utf-8")
    (plugin / "libs" / "zdd").mkdir(parents=True)
    (plugin / "libs" / "zdd" / "pyproject.toml").write_text("", encoding="utf-8")
    (plugin / "libs" / "remote-login-shell").mkdir(parents=True)
    (plugin / "libs" / "remote-login-shell" / "pyproject.toml").write_text("", encoding="utf-8")
    marker = tmp_path / "pip-fallback-ran"
    fake_python = tmp_path / "python"
    fake_python.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$*" > "$TEST_FALLBACK_MARKER"\n',
        encoding="ascii",
    )
    fake_python.chmod(0o755)

    script = tmp_path / "fallback.sh"
    script.write_text(
        "\n".join(
            [
                "set -euo pipefail",
                "_step() { printf '%s\\n' \"$1\"; }",
                "_fail() { printf '%s\\n' \"$1\" >&2; }",
                resolve_vendored_lib,
                "_resolve_ssh_manager() { _resolve_vendored_lib ssh-manager; }",
                "_resolve_agent_procutil() { _resolve_vendored_lib agent-procutil; }",
                "_resolve_venue_copilot() { _resolve_vendored_lib venue-copilot; }",
                "_resolve_zdd() { _resolve_vendored_lib zdd; }",
                "_resolve_remote_login_shell() { _resolve_vendored_lib remote-login-shell; }",
                install_package,
                "HAVE_UV=0",
                f"VENV_PYTHON='{fake_python}'",
                f"PLUGIN_DIR='{plugin}'",
                "_install_agent_ssh_package",
            ]
        ),
        encoding="utf-8",
    )
    env = {**os.environ, "TEST_FALLBACK_MARKER": str(marker)}

    proc = subprocess.run(
        [BASH, str(script)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    fallback_args = marker.read_text(encoding="ascii")
    assert str(plugin / "libs" / "agent-procutil") in fallback_args
    assert str(plugin / "libs" / "dropin-registry") in fallback_args
    assert str(plugin / "libs" / "ssh-manager") in fallback_args
    assert str(plugin / "libs" / "venue-copilot") in fallback_args
    assert str(plugin / "libs" / "zdd") in fallback_args
    assert str(plugin / "libs" / "remote-login-shell") in fallback_args
    assert str(plugin) in fallback_args


@pytest.mark.skipif(
    os.name == "nt" or BASH is None,
    reason="POSIX shell installer coverage",
)
def test_shell_pip_fallback_resolves_canonical_when_local_copy_absent(tmp_path: Path) -> None:
    """vendor-pointer-generalization effort, Phase 1: ssh-manager and
    agent-procutil are `uv`-editable canonical references, so a real
    dev checkout has NO local `plugin/libs/<lib>` copy for either one --
    the pip fallback must resolve the canonical `../../libs/<lib>` path
    instead (mirroring `install.ps1`'s own `Resolve-VendoredLib`)."""
    installer = SHELL_INSTALLER.read_text(encoding="utf-8")
    resolve_vendored_lib = _shell_function_source(
        installer, "_resolve_vendored_lib", "\n_resolve_ssh_manager()",
    )
    install_package = _shell_function_source(
        installer,
        "_install_agent_ssh_package",
        '\nACTION="${AGENT_SSH_ACTION:-install}"',
    )

    # git-checkout layout: repo_root/plugins/agent-ssh (no local libs/
    # copy) and repo_root/libs/{ssh-manager,agent-procutil,venue-copilot}
    # (canonical).
    repo_root = tmp_path / "repo"
    plugin = repo_root / "plugins" / "agent-ssh"
    plugin.mkdir(parents=True)
    (plugin / "libs" / "dropin-registry").mkdir(parents=True)
    canonical_ssh_manager = repo_root / "libs" / "ssh-manager"
    canonical_ssh_manager.mkdir(parents=True)
    (canonical_ssh_manager / "pyproject.toml").write_text("", encoding="utf-8")
    canonical_agent_procutil = repo_root / "libs" / "agent-procutil"
    canonical_agent_procutil.mkdir(parents=True)
    (canonical_agent_procutil / "pyproject.toml").write_text("", encoding="utf-8")
    canonical_venue_copilot = repo_root / "libs" / "venue-copilot"
    canonical_venue_copilot.mkdir(parents=True)
    (canonical_venue_copilot / "pyproject.toml").write_text("", encoding="utf-8")
    canonical_zdd = repo_root / "libs" / "zdd"
    canonical_zdd.mkdir(parents=True)
    (canonical_zdd / "pyproject.toml").write_text("", encoding="utf-8")
    canonical_remote_login_shell = repo_root / "libs" / "remote-login-shell"
    canonical_remote_login_shell.mkdir(parents=True)
    (canonical_remote_login_shell / "pyproject.toml").write_text("", encoding="utf-8")

    marker = tmp_path / "pip-fallback-ran"
    fake_python = tmp_path / "python"
    fake_python.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$*" > "$TEST_FALLBACK_MARKER"\n',
        encoding="ascii",
    )
    fake_python.chmod(0o755)

    script = tmp_path / "fallback.sh"
    script.write_text(
        "\n".join(
            [
                "set -euo pipefail",
                "_step() { printf '%s\\n' \"$1\"; }",
                "_fail() { printf '%s\\n' \"$1\" >&2; }",
                resolve_vendored_lib,
                "_resolve_ssh_manager() { _resolve_vendored_lib ssh-manager; }",
                "_resolve_agent_procutil() { _resolve_vendored_lib agent-procutil; }",
                "_resolve_venue_copilot() { _resolve_vendored_lib venue-copilot; }",
                "_resolve_zdd() { _resolve_vendored_lib zdd; }",
                "_resolve_remote_login_shell() { _resolve_vendored_lib remote-login-shell; }",
                install_package,
                "HAVE_UV=0",
                f"VENV_PYTHON='{fake_python}'",
                f"PLUGIN_DIR='{plugin}'",
                "_install_agent_ssh_package",
            ]
        ),
        encoding="utf-8",
    )
    env = {**os.environ, "TEST_FALLBACK_MARKER": str(marker), "HOME": str(tmp_path / "home")}

    proc = subprocess.run(
        [BASH, str(script)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    fallback_args = marker.read_text(encoding="ascii")
    assert str(canonical_ssh_manager.resolve()) in fallback_args
    assert str(canonical_agent_procutil.resolve()) in fallback_args
    assert str(canonical_venue_copilot.resolve()) in fallback_args
    assert str(canonical_zdd.resolve()) in fallback_args
    assert str(canonical_remote_login_shell.resolve()) in fallback_args


def test_powershell_installer_resolves_uv_editable_libs_via_shared_helper() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")

    assert ". (Join-Path $PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1')" in installer
    assert "function Resolve-VenueCopilot" in installer
    assert "Resolve-VendoredLib -LibName 'venue-copilot'" in installer
    assert "$venueCopilotDir = Resolve-VenueCopilot" in installer
    assert "$pkgResult = Invoke-UvPipInstallResilient" in installer
    assert "$depResult = Invoke-UvPipInstallResilient" in installer
    assert "--reinstall-package" in installer
    assert "agent-venue-copilot" in installer
    assert "-UvCommand $uvPath" in installer
    assert "function Resolve-Zdd" in installer
    assert "Resolve-VendoredLib -LibName 'zdd'" in installer
    assert "$zddDir = Resolve-Zdd" in installer
