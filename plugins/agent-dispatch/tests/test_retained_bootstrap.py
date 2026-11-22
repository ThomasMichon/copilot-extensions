"""Copied command admission retains the generated managed-Python selector."""
from __future__ import annotations

import os
from pathlib import Path
import runpy
import shlex
import shutil
import subprocess

import pytest

from . import source_publication_fixture as fixture


@pytest.mark.parametrize("shell", ["bash", "powershell", "pwsh"])
def test_binstub_publication_retains_managed_bootstrap(tmp_path: Path, shell: str) -> None:
    if shell == "bash" and os.name == "nt" or shell == "powershell" and os.name != "nt":
        pytest.skip("This control requires the native shell")
    host = shutil.which("powershell.exe" if shell == "powershell" else shell)
    uv = shutil.which("uv")
    if not host or not uv:
        pytest.skip("This control requires uv and the addressed shell")
    selected = subprocess.run(
        [uv, "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", "cpython@3.11.18"],
        capture_output=True, text=True, timeout=15,
    )
    if selected.returncode:
        pytest.skip("This acquisition-free control requires prepared CPython 3.11.18")
    managed_root = subprocess.check_output([uv, "python", "dir"], text=True, timeout=15).strip()
    scripts = tmp_path / "payload" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copytree(
        fixture.SCRIPTS / "installation-context", scripts / "installation-context",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    bootstrap = runpy.run_path(str(fixture.REPO / "libs/payload-invocation/bootstrap_python.py"))
    outputs = bootstrap["expected_files"](
        scripts.parent / "payload-invocation.json",
        {"bootstrapPython": "3.11.18", "bootstrapLauncher": "uv"},
    )
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    root = tmp_path / "retained"
    root.mkdir()
    legacy = tmp_path / "unselected-legacy"
    legacy.mkdir()
    tools = tmp_path / "old-python-first"
    tools.mkdir()
    shutil.copy2(uv, tools / Path(uv).name)
    if os.name == "nt":
        (tools / "python.exe").write_bytes(b"PATH Python must not run")
    else:
        for name in ("python", "python3"):
            (tools / name).write_text("#!/bin/sh\nexit 98\n", encoding="utf-8")
            (tools / name).chmod(0o755)
        for name in ("bash", "dirname", "wc", "cat", "mkdir", "cp", "mv", "tr"):
            executable = shutil.which(name)
            assert executable
            shutil.copy2(executable, tools / name)
    env = {**os.environ, "UV_PYTHON_INSTALL_DIR": managed_root, "UV_PYTHON_DOWNLOADS": "never"}
    for key in ("COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON", "COPILOT_EXTENSIONS_CONTEXT"):
        env.pop(key, None)
    old_python = os.environ.get("TEST_OLD_PYTHON")
    env["PATH"] = os.pathsep.join(filter(None, (
        str(Path(old_python).parent) if old_python else "", str(tools),
    )))
    if shell == "bash":
        entry = scripts / "publish.sh"
        entry.write_text(
            "set -eu\n"
            f"SCRIPT_DIR={shlex.quote(str(scripts))}\n"
            f"INSTALL_DIR={shlex.quote(str(root))}\n"
            f"LEGACY_INSTALL_DIR={shlex.quote(str(legacy))}\n"
            f"LOCAL_BIN={shlex.quote(str(tmp_path / 'local-bin'))}\n"
            "AGENT_DISPATCH_SUPERVISE_MACHINE=fixture\n"
            "_assert_source_runtime() { :; }\n_ok() { :; }\n"
            '_runtime_publication_stage() { printf -v "$2" "%s.stage" "$1"; }\n'
            '_runtime_publication_commit_file() { mv "$2" "$1"; }\n'
            '_runtime_publication_copy() { cp "$1" "$2"; }\n'
            + fixture.function("sh", "_runtime_admission_preflight")
            + fixture.function("sh", "deploy_binstub")
            + '\ndeploy_binstub\ndeploy_binstub\n'
            + 'bash "$INSTALL_DIR/bin/_bootstrap_python/select.sh"\n',
            encoding="utf-8",
        )
        command = [host, str(entry)]
    else:
        def quote(path: Path) -> str:
            return "'" + str(path).replace("'", "''") + "'"

        entry = scripts / "publish.ps1"
        entry.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            f"$InstallDir = {quote(root)}\n$legacyInstallDir = {quote(legacy)}\n"
            f"$LocalBin = {quote(tmp_path / 'local-bin')}\n"
            "$env:AGENT_DISPATCH_SUPERVISE_MACHINE = 'fixture'\n"
            "function Assert-SourceRuntime {}\nfunction Write-Ok {}\n"
            "function Get-InstallDirectoryIdentity { param($Path) return $Path }\n"
            + fixture.function("ps1", "Deploy-SelfProvisioningBinstub")
            + "\nDeploy-SelfProvisioningBinstub\nDeploy-SelfProvisioningBinstub\n"
            + "& (Join-Path $InstallDir 'bin\\_bootstrap_python\\select.ps1')\n",
            encoding="utf-8",
        )
        command = [host, "-NoProfile", "-File", str(entry)]
    result = subprocess.run(
        command, env=env, cwd=tmp_path, capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert Path(result.stdout.strip()).samefile(selected.stdout.strip())
    assert not result.stderr
    for path in outputs:
        assert (root / "bin" / path.relative_to(scripts)).read_bytes() == path.read_bytes()
    assert not (root / "bin/_bootstrap_python/_bootstrap_python").exists()
