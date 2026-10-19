from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import installer_engine_ref as ier
import materialize_main as mm


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


def _release_payload(tmp_path: Path) -> Path:
    """Keep dependency admission real, with a publisher-owned local source graph."""
    payload = tmp_path / "payload"
    shutil.copytree(
        PLUGIN, payload,
        ignore=shutil.ignore_patterns("tests", "__pycache__", ".pytest_cache", ".venv"),
    )
    for materializer in (
        mm.materialize_uv_editable_ref_into,
        mm.materialize_installer_engine_ref_into,
    ):
        log = materializer(
            source_consumer_dir=PLUGIN, dest_consumer_dir=payload,
            canonical_root=PLUGIN.parents[1],
        )
        assert not any(entry.startswith("SKIP") for entry in log), log
    return payload


@pytest.mark.skipif(
    os.name != "nt" or PWSH is None,
    reason="Windows PowerShell installer coverage",
)
def test_package_install_refuses_broken_uv_without_unrestricted_fallback(
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
    venv = tmp_path / "venv"
    subprocess.run(
        [sys._base_executable, "-I", "-m", "venv", "--without-pip", str(venv)],
        capture_output=True, text=True, check=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    python = venv / "Scripts/python.exe"
    payload = _release_payload(tmp_path)
    dependency_a = payload / "libs" / "ssh-manager"
    dependency_b = payload / "libs" / "agent-procutil"

    def ps_quote(value: str) -> str:
        return value.replace("'", "''")

    script = tmp_path / "fallback.ps1"
    script.write_text(
        "\n".join(
            [
                "$ErrorActionPreference = 'Stop'",
                "function Write-Step { param([string]$Msg) Write-Host $Msg }",
                "function Write-Warn { param([string]$Msg) Write-Host $Msg }",
                "function Write-Fail { param([string]$Msg) Write-Host $Msg }",
                f". '{ps_quote(str(payload / 'scripts' / 'installer-engine.ps1'))}'",
                install_package,
                "function Resolve-VenueCopilot { return '__unused__' }",
                (
                    "$ok = Install-AgentSshPackage "
                    f"-Python '{ps_quote(str(python))}' "
                    f"-Source '{ps_quote(str(payload))}' "
                    "-Dependencies @("
                    f"'{ps_quote(str(dependency_a))}',"
                    f"'{ps_quote(str(dependency_b))}'"
                    ") "
                    f"-UvCommand '{ps_quote(str(fake_bin / 'uv.exe'))}'"
                ),
                "if ($ok) { throw 'broken UV produced a successful dependency install' }",
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
        cwd=payload,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Selected dependency installation failed" in proc.stdout
    assert "configuration probe failed" in proc.stdout
    assert not marker.exists()
    assert not list((venv / "Lib/site-packages").glob("pyyaml*"))


@pytest.mark.skipif(os.name != "nt" or PWSH is None, reason="Windows PowerShell installer coverage")
def test_uv_receives_the_complete_local_dependency_graph_in_one_call(tmp_path: Path) -> None:
    install_package = _function_source(
        INSTALLER.read_text(encoding="utf-8"), "Install-AgentSshPackage", "\n$PluginDir =",
    )
    script = tmp_path / "batch.ps1"
    script.write_text("\n".join([
        "$ErrorActionPreference = 'Stop'",
        "$script:calls = @()",
        install_package,
        "function Resolve-VenueCopilot { return 'local-venue' }",
        "function Invoke-UvPipInstallResilient {",
        "  param($UvCommand, $Arguments, $PayloadDirToScrub)",
        "  $script:calls += ,@($Arguments)",
        "  return [pscustomobject]@{ ExitCode = 0; Output = '' }",
        "}",
        "$ok = Install-AgentSshPackage -Python 'python' -Source 'plugin' "
        "-Dependencies @('local-procutil','local-venue','local-machine-transport') -UvCommand 'uv'",
        "if (-not $ok -or $script:calls.Count -ne 2) { throw 'expected dependency batch and plugin install' }",
        "foreach ($source in @('pyyaml>=6.0.3','local-procutil','local-venue','local-machine-transport')) {",
        "  if ($script:calls[0] -notcontains $source) { throw ('missing dependency ' + $source) }",
        "}",
        "if ($script:calls[0] -notcontains 'agent-venue-copilot') { throw 'lost forced venue reinstall' }",
        "if ($script:calls[1] -contains '--no-deps' -or $script:calls[1] -notcontains 'plugin') {",
        "  throw 'plugin must resolve its closure after the dependency batch'",
        "}",
    ]), encoding="utf-8")
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script)],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(
    os.name == "nt" or BASH is None,
    reason="POSIX shell installer coverage",
)
@pytest.mark.parametrize("have_uv", [False, True])
def test_shell_canonical_install_includes_vendored_dependencies(tmp_path: Path, have_uv: bool) -> None:
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
    (plugin / "libs" / "fleet-contracts").mkdir(parents=True)
    (plugin / "libs" / "fleet-contracts" / "pyproject.toml").write_text("", encoding="utf-8")
    (plugin / "libs" / "machine-transport").mkdir(parents=True)
    (plugin / "libs" / "machine-transport" / "pyproject.toml").write_text("", encoding="utf-8")
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
                'invoke_uv_pip_install_resilient() { printf "%s\\n" "$*" >> "$TEST_FALLBACK_MARKER"; }',
                resolve_vendored_lib,
                "_resolve_ssh_manager() { _resolve_vendored_lib ssh-manager; }",
                "_resolve_agent_procutil() { _resolve_vendored_lib agent-procutil; }",
                "_resolve_venue_copilot() { _resolve_vendored_lib venue-copilot; }",
                "_resolve_zdd() { _resolve_vendored_lib zdd; }",
                "_resolve_remote_login_shell() { _resolve_vendored_lib remote-login-shell; }",
                "_resolve_fleet_contracts() { _resolve_vendored_lib fleet-contracts; }",
                "_resolve_machine_transport() { _resolve_vendored_lib machine-transport; }",
                install_package,
                f"HAVE_UV={1 if have_uv else 0}",
                f"UV_CMD={'fixture-uv' if have_uv else ''}",
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

    if not have_uv:
        assert proc.returncode != 0
        assert "unrestricted pip fallback is not supported" in proc.stderr
        assert not marker.exists()
        return
    assert proc.returncode == 0, proc.stdout + proc.stderr
    fallback_args = marker.read_text(encoding="ascii")
    assert str(plugin / "libs" / "agent-procutil") in fallback_args
    assert str(plugin / "libs" / "dropin-registry") in fallback_args
    assert str(plugin / "libs" / "ssh-manager") in fallback_args
    assert str(plugin / "libs" / "venue-copilot") in fallback_args
    assert str(plugin / "libs" / "zdd") in fallback_args
    assert str(plugin / "libs" / "remote-login-shell") in fallback_args
    assert str(plugin / "libs" / "fleet-contracts") in fallback_args
    assert str(plugin / "libs" / "machine-transport") in fallback_args
    assert str(plugin) in fallback_args


@pytest.mark.skipif(
    os.name == "nt" or BASH is None,
    reason="POSIX shell installer coverage",
)
@pytest.mark.parametrize("have_uv", [False, True])
def test_shell_canonical_install_resolves_sources_when_local_copy_absent(tmp_path: Path, have_uv: bool) -> None:
    """vendor-pointer-generalization effort, Phase 1: ssh-manager and
    agent-procutil are `uv`-editable canonical references, so a real
    dev checkout has NO local `plugin/libs/<lib>` copy for either one --
    the canonical install must resolve the `../../libs/<lib>` path
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
    canonical_fleet_contracts = repo_root / "libs" / "fleet-contracts"
    canonical_fleet_contracts.mkdir(parents=True)
    (canonical_fleet_contracts / "pyproject.toml").write_text("", encoding="utf-8")
    canonical_machine_transport = repo_root / "libs" / "machine-transport"
    canonical_machine_transport.mkdir(parents=True)
    (canonical_machine_transport / "pyproject.toml").write_text("", encoding="utf-8")

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
                'invoke_uv_pip_install_resilient() { printf "%s\\n" "$*" >> "$TEST_FALLBACK_MARKER"; }',
                resolve_vendored_lib,
                "_resolve_ssh_manager() { _resolve_vendored_lib ssh-manager; }",
                "_resolve_agent_procutil() { _resolve_vendored_lib agent-procutil; }",
                "_resolve_venue_copilot() { _resolve_vendored_lib venue-copilot; }",
                "_resolve_zdd() { _resolve_vendored_lib zdd; }",
                "_resolve_remote_login_shell() { _resolve_vendored_lib remote-login-shell; }",
                "_resolve_fleet_contracts() { _resolve_vendored_lib fleet-contracts; }",
                "_resolve_machine_transport() { _resolve_vendored_lib machine-transport; }",
                install_package,
                f"HAVE_UV={1 if have_uv else 0}",
                f"UV_CMD={'fixture-uv' if have_uv else ''}",
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

    if not have_uv:
        assert proc.returncode != 0
        assert "unrestricted pip fallback is not supported" in proc.stderr
        assert not marker.exists()
        return
    assert proc.returncode == 0, proc.stdout + proc.stderr
    fallback_args = marker.read_text(encoding="ascii")
    assert str(canonical_ssh_manager.resolve()) in fallback_args
    assert str(canonical_agent_procutil.resolve()) in fallback_args
    assert str(canonical_venue_copilot.resolve()) in fallback_args
    assert str(canonical_zdd.resolve()) in fallback_args
    assert str(canonical_remote_login_shell.resolve()) in fallback_args
    assert str(canonical_fleet_contracts.resolve()) in fallback_args
    assert str(canonical_machine_transport.resolve()) in fallback_args


def test_powershell_installer_resolves_uv_editable_libs_via_shared_helper() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")

    ref = ier.find_engine_ref(INSTALLER, "ps1")
    assert ref is not None, "installer must source exactly one shared engine"
    if (PLUGIN / "scripts" / "installer-engine.ps1").is_file():
        assert ier.is_local_ref(ref, PLUGIN), "release must source its embedded engine"
    else:
        assert ier.is_canonical_ref(ref, PLUGIN, repo_root=PLUGIN.parents[1])
    assert ref.resolved().read_bytes() == ENGINE_PS1.read_bytes()
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
    assert "function Resolve-FleetContracts" in installer
    assert "Resolve-VendoredLib -LibName 'fleet-contracts'" in installer
    assert "$fleetContractsDir = Resolve-FleetContracts" in installer
    assert "Resolve-VendoredLib -LibName 'machine-transport'" in installer
    assert "$machineTransportDir = Resolve-MachineTransport" in installer
