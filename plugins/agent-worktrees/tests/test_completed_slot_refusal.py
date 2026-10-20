from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_completed_slot_refusal_precedes_mutation_on_both_platforms():
    ps = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
    ps = ps[ps.index("function Deploy-Venv {"):ps.index("function Deploy-VenvAndPackage {")]
    sh = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
    start = sh.index("\ndeploy_venv() {")
    sh = sh[start:sh.index("\n}\n", start) + 3]
    for body, cleanup in ((ps, "$slotClean ="), (sh, "local slot_clean=")):
        assert body.index("already_complete" if body is sh else "Test-SlotAlreadyComplete") < body.index("cannot be rebuilt in place")
        assert body.index("cannot be rebuilt in place") < body.index(cleanup)


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.skipif(
    os.environ.get("AGENT_WORKTREES_SLOT_ADMISSION_INTEGRATION") != "1",
    reason="explicit native installer admission integration",
)
@pytest.mark.parametrize("platform", ["powershell", "bash"])
def test_real_installer_function_reuses_or_refuses_before_mutation(tmp_path, platform):
    from agent_procutil import no_window_kwargs

    slot = tmp_path / "slot"
    slot.mkdir()
    marker = slot / ".install-complete.json"
    marker.write_text('{"version":"1.2.3","payload_hash":"original"}', encoding="utf-8")
    before = marker.read_bytes()
    if platform == "powershell":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            pytest.skip("PowerShell unavailable")
        source = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
        body = source[source.index("function Deploy-Venv {"):source.index("function Deploy-VenvAndPackage {")]
        activate = source[source.index("function Invoke-VersionedActivateInner {"):source.index("function Invoke-VersionedActivate {")]
        health = tmp_path / "Health-Failure"
        health.touch()
        script = (
            "$ErrorActionPreference='Stop'\n"
            + f"$PluginDir='{str(tmp_path / 'absent').replace(chr(39), chr(39)*2)}'\n"
            + "$SourceRuntimeIdentity=$null\n"
            + f". '{str(SCRIPTS / 'source-runtime.ps1').replace(chr(39), chr(39)*2)}'\n"
            + body
            + "\n" + activate
            + "\n$VersionedRuntime=$true;$SrcVersion='1.2.3';$Force=$true\n"
            + f"$VenvDir='{str(slot).replace(chr(39), chr(39)*2)}'\n"
            "function Wait-ForVersionedSlotLease { return $true }\n"
            "function Write-ServiceErr { }\n"
            "function Invoke-VersionedSlotClean { throw 'mutation reached' }\n"
            "function Test-SlotAlreadyComplete { return $false }\n"
            "if(Deploy-Venv){throw 'changed completed slot accepted'}\n"
            "function Test-SlotAlreadyComplete { return $true }\n"
            "if(-not(Deploy-Venv)){throw 'matching completed slot refused'}\n"
            "if(-not $script:DeployVenvAlreadyComplete){throw 'package reinstall not skipped'}\n"
            f"Set-Location -LiteralPath '{str(tmp_path).replace(chr(39), chr(39)*2)}'\n"
            "$VenvPython='Health-Failure'\n"
            "function Health-Failure { $global:LASTEXITCODE=1; 'missing dependency' }\n"
            "function Write-ServiceChanged { }\n"
            "if(Invoke-VersionedActivateInner){throw 'broken slot activated'}\n"
            "function Test-SlotAlreadyComplete { return $false }\n"
            "if(Deploy-Venv){throw 'failed completed slot rebuilt on retry'}\n"
        )
        path = tmp_path / "admission.ps1"
        path.write_text(script, encoding="utf-8")
        argv = [shell, "-NoProfile", "-NonInteractive", "-File", str(path)]
    else:
        git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        shell = str(git_bash) if os.name == "nt" and git_bash.is_file() else shutil.which("bash")
        if not shell or (os.name == "nt" and "system32" in shell.lower()):
            pytest.skip("conformant Bash unavailable")
        source = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
        start = source.index("\ndeploy_venv() {")
        body = source[start:source.index("\n}\n", start) + 3]
        start = source.index("\n_versioned_activate_inner() {")
        activate = source[start:source.index("\n_versioned_activate() {", start)]
        health = tmp_path / "health-failure.sh"
        health.write_text("#!/bin/sh\nprintf 'missing dependency\\n' >&2\nexit 1\n", encoding="utf-8")
        health.chmod(0o755)
        path = str(slot).replace("\\", "/")
        health_path = str(health).replace("\\", "/")
        if os.name == "nt":
            path = "/" + path[0].lower() + path[2:]
            health_path = "/" + health_path[0].lower() + health_path[2:]
        script = (
            f"PLUGIN_DIR='{path}/absent'\nSOURCE_RUNTIME_IDENTITY=''\n"
            + f". '{(SCRIPTS / 'source-runtime.sh').as_posix()}'\n"
            + body + "\n" + activate + "\nVERSIONED_RUNTIME=1; SRC_VERSION=1.2.3; FORCE=1\n"
            f"VENV_DIR='{path}'\n"
            "err() { :; }\n"
            "_wait_for_versioned_slot_lease() { return 0; }\n"
            "_versioned_slot_clean() { echo 'mutation reached' >&2; return 99; }\n"
            "_test_slot_already_complete() { return 1; }\n"
            "deploy_venv; rc=$?; [[ $rc == 1 ]] || exit 81\n"
            "_test_slot_already_complete() { return 0; }\n"
            "deploy_venv; rc=$?; [[ $rc == 2 ]] || exit 82\n"
            f"VENV_PYTHON='{health_path}'; SCRIPT_DIR='{path}'\n"
            "ok() { :; }\n"
            "_versioned_activate_inner; rc=$?; [[ $rc == 1 ]] || exit 83\n"
            "_test_slot_already_complete() { return 1; }\n"
            "deploy_venv; rc=$?; [[ $rc == 1 ]] || exit 84\n"
        )
        script_path = tmp_path / "admission.sh"
        script_path.write_text(script, encoding="utf-8", newline="\n")
        argv = [shell, str(script_path)]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=15, **no_window_kwargs())
    assert result.returncode == 0, result.stderr
    assert "mutation reached" not in result.stderr
    assert not marker.exists()
    assert (slot / ".install-complete.failed.json").read_bytes() == before
