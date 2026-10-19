"""Installer cleanup must not discover an unrelated construction owner."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import agent_procutil
import pytest


@pytest.mark.parametrize("owned", [False, True])
@pytest.mark.parametrize("fails", [False, True])
def test_cleanup_only_calls_a_loaded_construction_handler(
    tmp_path: Path, owned: bool, fails: bool,
) -> None:
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")
    installer = Path(__file__).resolve().parents[1] / "scripts" / "install.ps1"
    source = installer.read_text(encoding="utf-8")
    dispatch = "\ntry {\n    switch ($Action) {"
    assert source.count(dispatch) == 1
    tail = dispatch + source.split(dispatch, 1)[1]
    modules = tmp_path / "modules"
    module = modules / "UnrelatedConstruction"
    module.mkdir(parents=True)
    (module / "UnrelatedConstruction.psd1").write_text(
        "@{RootModule='UnrelatedConstruction.psm1';ModuleVersion='1.0.0';"
        "FunctionsToExport=@('Stop-RuntimeConstruction')}\n",
        encoding="utf-8",
    )
    (module / "UnrelatedConstruction.psm1").write_text(
        "[Console]::WriteLine('AMBIENT_IMPORTED')\n"
        "function Stop-RuntimeConstruction {[Console]::WriteLine('AMBIENT_CLEANUP')}\n",
        encoding="utf-8",
    )
    failure = "throw 'fixture stamp failure'" if fails else ""
    handler = (
        "function Stop-RuntimeConstruction {[Console]::WriteLine('OWNED_CLEANUP')}\n"
        if owned else ""
    )
    script = tmp_path / "cleanup.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop';$PSModuleAutoLoadingPreference='All';$Action='stamp'\n"
        f"function Invoke-Stamp {{[Console]::WriteLine('STAMP');{failure}}}\n"
        "function Exit-InstallLock {[Console]::WriteLine('UNLOCK')}\n"
        + handler + tail,
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PSModulePath"] = str(modules)
    result = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-File", str(script)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10,
        **agent_procutil.no_window_kwargs(),
    )
    assert result.returncode == (1 if fails else 0), result.stderr
    assert result.stdout.strip().splitlines() == [
        "STAMP", *(["OWNED_CLEANUP"] if owned else []), "UNLOCK",
    ]
