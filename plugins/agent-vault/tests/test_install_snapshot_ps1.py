"""PowerShell installer regressions for agent-vault's stamped snapshot path."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_PS1 = _PLUGIN_ROOT / "scripts" / "install.ps1"
_HARNESS_TIMEOUT_SECONDS = 20
_UV_INDEX_BLOCK_START = "function Test-UvConfiguredIndex {"
_UV_INDEX_BLOCK_END = "\n# === install-contract:v3 source-kind -- keep byte-identical across plugins ==="


def _isolated_install_env(home: Path) -> dict[str, str]:
    """Mirror the runner's containment roots for direct installer subprocesses."""
    env = os.environ.copy()
    roots = {
        "HOME": home,
        "USERPROFILE": home,
        "APPDATA": home / "AppData" / "Roaming",
        "LOCALAPPDATA": home / "AppData" / "Local",
        "PROGRAMDATA": home / "ProgramData",
        "XDG_CONFIG_HOME": home / ".config",
        "XDG_CACHE_HOME": home / ".cache",
        "XDG_DATA_HOME": home / ".local" / "share",
        "XDG_STATE_HOME": home / ".local" / "state",
        "XDG_RUNTIME_DIR": home / "run",
        "TEMP": home / "tmp",
        "TMP": home / "tmp",
        "TMPDIR": home / "tmp",
        "COPILOT_HOME": home / ".copilot",
        "AGENT_HOME": home,
        "AGENT_VAULT_HOME": home / ".agent-vault",
    }
    for path in roots.values():
        path.mkdir(parents=True, exist_ok=True)
    env.update({name: str(path) for name, path in roots.items()})
    env["COPILOT_PLUGIN_INSTALL_STAGED"] = "1"
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        env.pop(name, None)
    return env


def _stage_payload(tmp_path: Path) -> Path:
    payload = tmp_path / "plugins" / "agent-vault"
    payload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        _PLUGIN_ROOT,
        payload,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            "tests",
        ),
    )
    staged_libs = tmp_path / "libs"
    staged_libs.mkdir(parents=True, exist_ok=True)
    for lib in ("installer-engine", "zdd", "agent-procutil", "single-instance-lease"):
        shutil.copytree(_PLUGIN_ROOT.parents[1] / "libs" / lib, staged_libs / lib)
    return payload


def _host_pip_index_url() -> str | None:
    candidates = (
        Path(r"C:\ProgramData\pip\pip.ini"),
        Path("/etc/pip.conf"),
        Path("/etc/xdg/pip/pip.conf"),
    )
    for candidate in candidates:
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        match = re.search(r"(?m)^\s*index-url\s*=\s*(\S+)\s*$", text)
        if match:
            return match.group(1)
    return None


def _extract_uv_index_block() -> str:
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    return _UV_INDEX_BLOCK_START + text.split(_UV_INDEX_BLOCK_START, 1)[1].split(_UV_INDEX_BLOCK_END, 1)[0]


def test_ensure_uv_index_bridges_pip_only_config_to_uv(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("POSIX shim harness")
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    home = tmp_path / "home"
    env = _isolated_install_env(home)
    env.pop("UV_DEFAULT_INDEX", None)
    env.pop("UV_INDEX_URL", None)

    shim_dir = tmp_path / "bin"
    shim_dir.mkdir(parents=True)
    pip_shim = shim_dir / "pip"
    index_url = "https://example.invalid/simple"
    pip_shim.write_text(
        "#!/usr/bin/env sh\n"
        "if [ \"$1\" = config ] && [ \"$2\" = get ] && [ \"$3\" = global.index-url ]; then\n"
        f"  printf '%s\\n' '{index_url}'\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    pip_shim.chmod(0o755)
    env["PATH"] = str(shim_dir) + os.pathsep + env.get("PATH", "")

    script = f"""
function Write-Step {{ param([string]$Message) }}
{_extract_uv_index_block()}
Ensure-UvIndex
if (-not $env:UV_DEFAULT_INDEX) {{ exit 1 }}
[Console]::Write($env:UV_DEFAULT_INDEX)
"""
    proc = subprocess.run(
        [pwsh, "-NoProfile", "-Command", script],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=_HARNESS_TIMEOUT_SECONDS,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == index_url


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer behavior")
def test_posix_stamp_wrapper_fails_when_provision_reports_success_without_runtime(
    tmp_path: Path,
) -> None:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("native POSIX bash is unavailable")

    home = tmp_path / "home"
    env = _isolated_install_env(home)
    install_dir = home / ".agent-vault"
    stamp = subprocess.run(
        [
            bash,
            str(_PLUGIN_ROOT / "scripts" / "install.sh"),
            "stamp",
            "--install-dir",
            str(install_dir),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert stamp.returncode == 0, stamp.stderr

    fake_snapshot = tmp_path / "fake-snapshot"
    installer = fake_snapshot / "scripts" / "install.sh"
    installer.parent.mkdir(parents=True, exist_ok=True)
    installer.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    installer.chmod(0o755)
    (install_dir / "payload-dir").write_text(str(fake_snapshot), encoding="utf-8")

    invoke = subprocess.run(
        [str(home / ".local" / "bin" / "agent-vault"), "--version"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert invoke.returncode == 1, invoke.stderr
    assert "provisioning completed without a resolvable runtime" in invoke.stderr


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is required")
def test_stamp_supports_first_use_provision_from_snapshot_only_ps1(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("POSIX snapshot-provision harness")
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    payload = _stage_payload(tmp_path)
    home = tmp_path / "home"
    version = next(
        line.split('"')[1]
        for line in (_PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines()
        if line.startswith("version = ")
    )
    env = _isolated_install_env(home)
    internal_index = _host_pip_index_url()
    if internal_index and not (env.get("UV_DEFAULT_INDEX") or env.get("UV_INDEX_URL")):
        env["UV_DEFAULT_INDEX"] = internal_index
    system_root = home / "systemroot"
    where_exe = system_root / "System32" / "where.exe"
    powershell_exe = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    where_exe.parent.mkdir(parents=True, exist_ok=True)
    powershell_exe.parent.mkdir(parents=True, exist_ok=True)
    where_exe.write_text(
        "#!/usr/bin/env sh\n"
        "if [ \"$1\" = pwsh ]; then\n"
        f"  printf '%s\\n' '{pwsh}'\n"
        "fi\n",
        encoding="utf-8",
    )
    where_exe.chmod(0o755)
    powershell_exe.write_text(
        "#!/usr/bin/env sh\n"
        f"exec '{pwsh}' \"$@\"\n",
        encoding="utf-8",
    )
    powershell_exe.chmod(0o755)
    env["SystemRoot"] = str(system_root)

    stamp = [
        pwsh,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(payload / "scripts" / "install.ps1"),
        "stamp",
    ]

    stamped = subprocess.run(
        stamp,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert stamped.returncode == 0, stamped.stderr

    snapshot = Path((home / ".agent-vault" / "payload-dir").read_text(encoding="utf-8").strip())
    assert (snapshot / "scripts" / "installer-engine.sh").is_file()
    assert (snapshot / "scripts" / "installer-engine.ps1").is_file()
    assert '. "$SCRIPT_DIR/installer-engine.sh"' in (
        snapshot / "scripts" / "install.sh"
    ).read_text(encoding="utf-8")
    assert ". (Join-Path $PSScriptRoot 'installer-engine.ps1')" in (
        snapshot / "scripts" / "install.ps1"
    ).read_text(encoding="utf-8")
    assert (snapshot / "libs" / "zdd" / "pyproject.toml").is_file()
    assert (snapshot / "libs" / "agent-procutil" / "pyproject.toml").is_file()
    assert (snapshot / "libs" / "single-instance-lease" / "pyproject.toml").is_file()

    shutil.rmtree(payload)
    shutil.rmtree(tmp_path / "libs")

    provision = subprocess.run(
        [
            "bash",
            str(snapshot / "scripts" / "install.sh"),
            "provision",
            "--no-service",
            "--install-dir",
            str(home / ".agent-vault"),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=420,
        check=False,
    )
    assert provision.returncode == 0, provision.stderr

    invoke = subprocess.run(
        [str(home / ".local" / "bin" / "agent-vault"), "--version"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert invoke.returncode == 0, invoke.stderr
    assert f"agent-vault {version.replace('-dev', '.dev')}" in invoke.stdout
