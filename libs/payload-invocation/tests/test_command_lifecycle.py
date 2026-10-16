"""Ownership admission precedes native runtime writes and activation."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import venv
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_command_binding import (
    PLUGIN_ID, POWERSHELL, ROOT, _catalog_environment, _load, _prepare_catalog,
    _resolve, _snapshot, _write, binding_fixture as binding_fixture,
    _publish, _lifecycle_lock_path,
    powershell_host as powershell_host,
)


def _lifecycle(fixture: dict):
    return _load(ROOT / "libs/payload-invocation/command_lifecycle.py", "lifecycle_reference")


@pytest.mark.skipif(os.name != "nt", reason="Windows strict containment")
@pytest.mark.parametrize("parent_exits", [False, True])
def test_command_binding_deadline_stops_tree_before_releasing_admission(
    binding_fixture: dict, parent_exits: bool,
) -> None:
    helper = binding_fixture["payload"] / "libs/agent-procutil/src/agent_procutil/__init__.py"
    helper.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "libs/agent-procutil/src/agent_procutil/__init__.py", helper)
    lifecycle = binding_fixture["binding"]._load_lifecycle()
    marker = binding_fixture["home"] / "child.json"
    worker = (
        "import json,os,time; from pathlib import Path; "
        f"p=Path({str(marker)!r}); "
        "p.write_text(json.dumps({'pid':os.getpid()})); "
        "time.sleep(30); p.write_text('escaped mutation')"
    )
    parent = (
        "import subprocess,sys,time; from pathlib import Path; "
        f"subprocess.Popen([sys.executable,'-c',{worker!r}]); "
        f"marker=Path({str(marker)!r})\n"
        "while not marker.exists(): time.sleep(0.01)\n"
        + ("" if parent_exits else "time.sleep(30)")
    )
    result = lifecycle.run_lifecycle(
        binding_fixture["binding"], binding_fixture["primitive"],
        [sys.executable, "-c", parent], deferred_runtime=True, timeout=3,
        **binding_fixture["arguments"],
    )
    assert result == (0 if parent_exits else 124)
    pid = json.loads(marker.read_text())["pid"]
    assert not binding_fixture["primitive"]._pid_is_live(pid)
    assert not _lifecycle_lock_path(binding_fixture["legacy"]).exists()
    with lifecycle.legacy_lifecycle(
        binding_fixture["binding"], binding_fixture["primitive"], reserve=False,
        **binding_fixture["arguments"],
    ) as lease:
        lease.assert_owned()
    time.sleep(0.1)
    assert json.loads(marker.read_text())["pid"] == pid


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_command_binding_invalid_deadline_never_acquires_admission(
    binding_fixture: dict, timeout: float,
) -> None:
    lifecycle = binding_fixture["binding"]._load_lifecycle()
    with pytest.raises(ValueError, match="finite and positive"):
        lifecycle.run_lifecycle(
            binding_fixture["binding"], binding_fixture["primitive"],
            [sys.executable, "-c", "raise AssertionError('not admitted')"],
            timeout=timeout, **binding_fixture["arguments"],
        )
    assert not _lifecycle_lock_path(binding_fixture["legacy"]).exists()


def test_command_binding_posix_deadline_refuses_before_admission_or_spawn(
    binding_fixture: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle = binding_fixture["binding"]._load_lifecycle()
    before = _snapshot(binding_fixture["home"])
    monkeypatch.setattr(lifecycle, "os", SimpleNamespace(name="posix"))

    def not_spawned(*args, **kwargs):
        pytest.fail("Unsupported bounded lifecycle spawned a child")

    monkeypatch.setattr(lifecycle.subprocess, "Popen", not_spawned)
    with pytest.raises(ValueError, match="Windows Job containment"):
        lifecycle.run_lifecycle(
            binding_fixture["binding"], binding_fixture["primitive"],
            [sys.executable, "-c", "pass"], timeout=1,
            **binding_fixture["arguments"],
        )
    assert _snapshot(binding_fixture["home"]) == before
    assert not _lifecycle_lock_path(binding_fixture["legacy"]).exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows strict containment")
@pytest.mark.parametrize("expired", [False, True])
def test_command_binding_unverified_job_cleanup_preserves_durable_lock(
    binding_fixture: dict, monkeypatch: pytest.MonkeyPatch, expired: bool,
) -> None:
    lifecycle = binding_fixture["binding"]._load_lifecycle()
    calls = []

    def wait(timeout=None):
        if timeout is not None and expired:
            raise subprocess.TimeoutExpired("fixture", timeout)
        return 0

    def terminate(timeout):
        calls.append(("terminate", timeout))
        raise RuntimeError("Job emptiness could not be verified")

    child = SimpleNamespace(wait=wait)
    job = SimpleNamespace(
        terminate_and_wait=terminate, close=lambda: calls.append(("close",)),
    )

    def spawn(command, **kwargs):
        assert kwargs["require_job"] is True
        assert kwargs["allow_breakaway"] is False
        return child, job

    monkeypatch.setattr(
        lifecycle, "_procutil",
        lambda root: SimpleNamespace(spawn_sync_in_kill_on_close_job=spawn),
    )
    with pytest.raises(RuntimeError, match="emptiness"):
        lifecycle.run_lifecycle(
            binding_fixture["binding"], binding_fixture["primitive"],
            [sys.executable, "-c", "pass"], deferred_runtime=True, timeout=1,
            **binding_fixture["arguments"],
        )
    assert _lifecycle_lock_path(binding_fixture["legacy"]).exists()
    assert calls == [("terminate", 2), ("close",)]


def test_command_binding_pending_failure_keeps_owner_and_never_selects(binding_fixture: dict) -> None:
    binding = binding_fixture["binding"]
    lifecycle = _lifecycle(binding_fixture)
    with pytest.raises(RuntimeError, match="fixture failure"):
        with lifecycle.legacy_lifecycle(binding, binding_fixture["primitive"], **binding_fixture["arguments"]):
            (binding_fixture["legacy"] / "runtime-slot").write_text("partial", encoding="utf-8")
            assert _resolve(binding_fixture)["reason"] == "legacy-lifecycle-incomplete"
            raise RuntimeError("fixture failure")
    assert not _lifecycle_lock_path(binding_fixture["legacy"]).exists()
    assert _resolve(binding_fixture)["reason"] == "legacy-lifecycle-incomplete"
    _write(binding_fixture["home"] / ".copilot/settings.json", {
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Other/Plugins"}}
        },
    })
    before = _snapshot(binding_fixture["legacy"])
    with pytest.raises(binding.CommandOwnershipConflict, match="reservation"):
        with lifecycle.legacy_lifecycle(binding, binding_fixture["primitive"], **binding_fixture["arguments"]):
            pytest.fail("Foreign installer reached runtime mutation")
    after = _snapshot(binding_fixture["legacy"])
    assert after.keys() == before.keys()
    assert all(after[path] == value for path, value in before.items() if path != ".")


def test_command_binding_same_owner_retries_pending_and_completes(binding_fixture: dict) -> None:
    binding = binding_fixture["binding"]
    # Use the binding's loader so publication and admission share their context.
    lifecycle = binding._load_lifecycle()
    for _ in range(2):
        with lifecycle.legacy_lifecycle(binding, binding_fixture["primitive"], **binding_fixture["arguments"]):
            assert _resolve(binding_fixture)["status"] == "blocked"
            binding.write_legacy_manifest(
                binding_fixture["primitive"], {"schema_version": 3, "service": PLUGIN_ID},
                **binding_fixture["arguments"],
            )
        assert _resolve(binding_fixture)["status"] == "selected"


def test_command_binding_teardown_lock_survives_runtime_removal(binding_fixture: dict) -> None:
    binding = binding_fixture["binding"]
    primitive = binding_fixture["primitive"]
    lifecycle = binding._load_lifecycle()
    _publish(binding_fixture)
    with lifecycle.legacy_lifecycle(
        binding, primitive, reserve=False, teardown=True, **binding_fixture["arguments"],
    ) as lease:
        assert lease.lock.path == _lifecycle_lock_path(binding_fixture["legacy"])
        assert not lease.lock.path.is_relative_to(binding_fixture["legacy"])
        shutil.rmtree(binding_fixture["legacy"])
        lease.lock.assert_owned()
        with pytest.raises(primitive.InstallationContextError):
            with lifecycle._lock(primitive, binding_fixture["arguments"]):
                pytest.fail("A competing installer entered while teardown still held its lock")
    assert not lease.lock.path.exists()
    assert not binding_fixture["legacy"].exists()


def test_command_binding_teardown_foreign_owner_refuses_without_writes(binding_fixture: dict) -> None:
    _publish(binding_fixture)
    _write(binding_fixture["home"] / ".copilot/settings.json", {
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Other/Plugins"}},
        },
    })
    before = _snapshot(binding_fixture["home"])
    binding = binding_fixture["binding"]
    with pytest.raises(binding.CommandOwnershipConflict, match="transfer"):
        with binding._load_lifecycle().legacy_lifecycle(
            binding, binding_fixture["primitive"], reserve=False, teardown=True,
            **binding_fixture["arguments"],
        ):
            pytest.fail("Foreign teardown reached runtime removal")
    assert _snapshot(binding_fixture["home"]) == before


@pytest.mark.parametrize("interpreter", ["parent", "child"])
def test_command_binding_teardown_refuses_interpreters_in_erased_runtime(
    binding_fixture: dict, monkeypatch: pytest.MonkeyPatch, interpreter: str,
) -> None:
    _publish(binding_fixture)
    binding = binding_fixture["binding"]
    lifecycle = binding._load_lifecycle()
    excluded = binding_fixture["legacy"] / "interpreter"
    command = [sys.executable]
    if interpreter == "parent":
        monkeypatch.setattr(lifecycle.sys, "executable", str(excluded))
    else:
        command = [str(excluded)]
    before = _snapshot(binding_fixture["home"])
    with pytest.raises(ValueError, match="interpreters outside the runtime"):
        lifecycle.run_lifecycle(
            binding, binding_fixture["primitive"], command, teardown=True,
            **binding_fixture["arguments"],
        )
    assert _snapshot(binding_fixture["home"]) == before


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell unavailable")
@pytest.mark.parametrize("inside_runtime", [False, True])
def test_command_binding_teardown_bootstrap_excludes_entire_runtime(
    binding_fixture: dict, inside_runtime: bool,
) -> None:
    root = binding_fixture["legacy"]
    candidate = root / "active/Scripts/python.exe" if inside_runtime else Path(sys.executable)
    if inside_runtime:
        candidate.parent.mkdir(parents=True)
        candidate.write_bytes(b"fixture interpreter must not be selected")
    program = """
$tokens=$null; $errors=$null
$source=Get-Content -LiteralPath $env:TEST_INSTALLER -Raw
$ast=[System.Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
$function=$ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Get-BootstrapPython'
},$true)
if (-not $function) { throw 'Missing real bootstrap function' }
Invoke-Expression $function.Extent.Text
$InstallDir=$env:TEST_RUNTIME
$VenvDir=Join-Path $InstallDir 'versions/next'
$LinkDir=Join-Path $InstallDir 'active'
function Get-Command { $true }
function Invoke-NativeCapture { @{ExitCode=0;Output=$env:TEST_CANDIDATE} }
function Get-ApplicationPath { $env:TEST_CANDIDATE }
$selected=Get-BootstrapPython -ExcludeRuntimeRoot
if ($selected) { Write-Output $selected }
"""
    completed = subprocess.run(
        [POWERSHELL, "-NoLogo", "-NoProfile", "-Command", program],
        env={
            **_catalog_environment(binding_fixture),
            "TEST_INSTALLER": str(ROOT / "plugins/agent-worktrees/scripts/install.ps1"),
            "TEST_RUNTIME": str(root), "TEST_CANDIDATE": str(candidate),
        },
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == ("" if inside_runtime else str(candidate))


def test_command_binding_ticket_presence_is_not_admission(
    binding_fixture: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = binding_fixture["binding"]
    monkeypatch.setenv("COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", "{}")
    before = _snapshot(binding_fixture["home"])
    with pytest.raises(binding.CommandOwnershipConflict, match="ticket"):
        binding._load_lifecycle().borrowed_lease(binding, binding_fixture["primitive"], **binding_fixture["arguments"])
    assert _snapshot(binding_fixture["home"]) == before


def test_command_binding_unattributed_default_preserves_legacy_without_selection(
    binding_fixture: dict, capsys: pytest.CaptureFixture,
) -> None:
    binding = binding_fixture["binding"]
    primitive = binding_fixture["primitive"]
    _write(binding_fixture["home"] / ".copilot/settings.json", {})
    lifecycle = binding._load_lifecycle()
    with lifecycle.legacy_lifecycle(binding, primitive, **binding_fixture["arguments"]):
        result = binding.write_legacy_manifest(
            primitive, {"schema_version": 3, "service": PLUGIN_ID},
            **binding_fixture["arguments"],
        )
        assert result["status"] == "unresolved"
    manifest = json.loads((binding_fixture["legacy"] / "deploy-manifest.json").read_text())
    assert "commandBinding" not in manifest
    assert "commandLifecycle" not in manifest
    assert _resolve(binding_fixture)["status"] == "unresolved"
    assert "command ownership remains unresolved" in capsys.readouterr().err


def test_command_binding_completed_unattributed_install_can_be_qualified_by_update(
    binding_fixture: dict,
) -> None:
    binding = binding_fixture["binding"]
    primitive = binding_fixture["primitive"]
    lifecycle = binding._load_lifecycle()
    _write(binding_fixture["home"] / ".copilot/settings.json", {})
    for _ in range(2):
        with lifecycle.legacy_lifecycle(binding, primitive, **binding_fixture["arguments"]):
            binding.write_legacy_manifest(
                primitive, {"schema_version": 3, "service": PLUGIN_ID},
                **binding_fixture["arguments"],
            )
    _write(binding_fixture["home"] / ".copilot/settings.json", {
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Example/Plugins"}}
        },
    })
    assert _resolve(binding_fixture)["status"] == "unresolved"
    with lifecycle.legacy_lifecycle(binding, primitive, **binding_fixture["arguments"]):
        assert _resolve(binding_fixture)["status"] == "blocked"
        (binding_fixture["legacy"] / "runtime-slot").write_text("qualified", encoding="utf-8")
        binding.write_legacy_manifest(
            primitive, {"schema_version": 3, "service": PLUGIN_ID},
            **binding_fixture["arguments"],
        )
    assert _resolve(binding_fixture)["status"] == "selected"


def test_command_binding_failed_unattributed_install_keeps_its_reservation(binding_fixture: dict) -> None:
    binding = binding_fixture["binding"]
    primitive = binding_fixture["primitive"]
    lifecycle = binding._load_lifecycle()
    _write(binding_fixture["home"] / ".copilot/settings.json", {})
    with pytest.raises(RuntimeError, match="partial"):
        with lifecycle.legacy_lifecycle(binding, primitive, **binding_fixture["arguments"]):
            raise RuntimeError("partial")
    manifest = binding_fixture["legacy"] / "deploy-manifest.json"
    before = manifest.read_bytes()
    other = binding_fixture["home"] / ".copilot/installed-plugins/other" / PLUGIN_ID
    other.mkdir(parents=True)
    with pytest.raises(binding.CommandOwnershipConflict, match="reservation"):
        with lifecycle.legacy_lifecycle(
            binding, primitive, **{**binding_fixture["arguments"], "payload_root": other},
        ):
            pytest.fail("Foreign unattributed lifecycle reached runtime mutation")
    assert manifest.read_bytes() == before


def test_command_binding_unattributed_explicit_policy_never_admits_mutation(binding_fixture: dict) -> None:
    binding = binding_fixture["binding"]
    _write(binding_fixture["home"] / ".copilot/settings.json", {})
    _write(binding_fixture["home"] / ".copilot-extensions/installation-mode.json", {
        "schema": "copilot-extensions.installation-mode", "version": 1,
        "installationMode": {"enabled": True, "marketplaces": {}},
    })
    with pytest.raises(binding.CommandOwnershipConflict, match="mutation refused"):
        with binding._load_lifecycle().legacy_lifecycle(
            binding, binding_fixture["primitive"], **binding_fixture["arguments"],
        ):
            pytest.fail("A policy-enabled unattributed install reached runtime mutation")
    assert not (binding_fixture["legacy"] / "deploy-manifest.json").exists()


@pytest.mark.parametrize("field,value", [
    ("token", "0" * 32), ("pid", 0), ("host", "another-host"),
    ("root", "/another-runtime"), ("owner", {}), ("version", 1),
])
def test_command_binding_inherited_ticket_is_bound_to_its_owner(
    binding_fixture: dict, monkeypatch: pytest.MonkeyPatch, field: str, value: object,
) -> None:
    binding = binding_fixture["binding"]
    lifecycle = binding._load_lifecycle()
    with lifecycle.legacy_lifecycle(binding, binding_fixture["primitive"], **binding_fixture["arguments"]) as lease:
        ticket = {**lease.ticket, field: value}
        token = lifecycle._CURRENT.set(None)
        try:
            monkeypatch.setenv(lifecycle.TICKET_ENV, json.dumps(ticket))
            with pytest.raises((binding.CommandOwnershipConflict, binding_fixture["primitive"].InstallationContextError)):
                lifecycle.borrowed_lease(binding, binding_fixture["primitive"], **binding_fixture["arguments"])
        finally:
            lifecycle._CURRENT.reset(token)


def test_command_binding_inherited_ticket_requires_live_parent(
    binding_fixture: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = binding_fixture["binding"]
    primitive = binding_fixture["primitive"]
    lifecycle = binding._load_lifecycle()
    with lifecycle.legacy_lifecycle(binding, primitive, **binding_fixture["arguments"]) as lease:
        token = lifecycle._CURRENT.set(None)
        try:
            with monkeypatch.context() as patch:
                patch.setenv(lifecycle.TICKET_ENV, json.dumps(lease.ticket))
                patch.setattr(primitive, "_pid_is_live", lambda pid: False)
                with pytest.raises(binding.CommandOwnershipConflict, match="admission changed"):
                    lifecycle.borrowed_lease(binding, primitive, **binding_fixture["arguments"])
        finally:
            lifecycle._CURRENT.reset(token)


def _native_fixture(fixture: dict, shell: str, action: str = "provision") -> list[str]:
    payload = fixture["payload"]
    _prepare_catalog(fixture)
    if shell == "bash":
        source = (ROOT / "plugins/agent-worktrees/scripts/install.sh").read_text()
        writer = "write_deploy_manifest() {" + source.split(
            "write_deploy_manifest() {", 1
        )[1].split("show_deploy_status() {", 1)[0]
        admission = source.split(
            "# === command-lifecycle: admission before staging ===\n", 1
        )[1].split("# === end command-lifecycle: admission before staging ===", 1)[0]
        dispatch = "case \"$ACTION\" in\n    stamp)" + source.split(
            'case "$ACTION" in\n    stamp)', 1
        )[1].split("    install)\n", 1)[0] + "esac\n"
        program = (
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            "__aw_args=(\"$@\")\nACTION=\"${1:-provision}\"\nCONTEXTUAL_INSTALL=false\n"
            "__aw_action=\"$ACTION\"\n__aw_install_dir_arg=\"$TEST_RUNTIME\"\n"
            "SCRIPT_DIR=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\"\n"
            "PLUGIN_DIR=\"$(dirname \"$SCRIPT_DIR\")\"\n"
            "INSTALL_DIR=\"$TEST_RUNTIME\"\nBIN_DIR=\"$INSTALL_DIR/bin\"\n"
            "LOCAL_BIN=\"$INSTALL_DIR/local-bin\"\nLINK_DIR=\"$INSTALL_DIR/.venv\"\n"
            "VENV_DIR=\"$INSTALL_DIR/versions/fixture\"\nVENV_PYTHON=\"$TEST_PYTHON\"\n"
            "SRC_VERSION=fixture\nSERVICE_NAME=fixture\n"
            "REMOVE_CONFIG=${TEST_REMOVE_CONFIG:-false}\nHAS_PROJECT=false\n"
            "LIB_DIR=\"$INSTALL_DIR/lib\"\nVERSIONED_RUNTIME=1\n"
            "remove_legacy_binstubs() { :; }\nremove_legacy_scripts() { :; }\n"
            "changed() { :; }\nskipped() { :; }\n"
            "_bootstrap_python() { printf '%s\\n' \"$TEST_PYTHON\"; }\n"
            "resolve_executable_command_path() { type -P \"$1\"; }\n"
            "resolve_machine() { printf 'example-host'; }\n"
            "detect_platform() { printf 'linux'; }\n_source_kind() { printf 'marketplace'; }\n"
            "header() { :; }\nok() { :; }\nerr() { printf '%s\\n' \"$*\" >&2; }\n"
            "_ensure_uv() { :; }\n_ensure_uv_index() { :; }\n"
            "deploy_runtime_resolvers() { :; }\ndeploy_tool_binstub() { :; }\n"
            "_deploy_venv_and_package() {\n"
            "  printf '%s' \"$TEST_OWNER\" > \"$INSTALL_DIR/runtime-slot\"\n"
            "  sleep 0.3\n}\n"
            "_versioned_activate() { cp \"$INSTALL_DIR/runtime-slot\" \"$INSTALL_DIR/current-version\"; }\n"
            + writer + "\n" + admission + "\n" + dispatch
        )
        if action == "uninstall":
            uninstall = source.split("    uninstall)\n", 1)[1].split("    start)\n", 1)[0]
            program += "\nrun_uninstall() {\ncase \"$ACTION\" in\nuninstall)\n" + uninstall + "esac\n}\nrun_uninstall\n"
        script = payload / "scripts/install.sh"
        script.write_text(program, encoding="utf-8")
        return [str(Path(subprocess.check_output(["which", "bash"], text=True).strip())), str(script), action]
    source = (ROOT / "plugins/agent-worktrees/scripts/install.ps1").read_text()
    writer = "function Write-V3Manifest {" + source.split(
        "function Write-V3Manifest {", 1
    )[1].split("function Test-UvConfiguredIndex {", 1)[0]
    admission = source.split(
        "# === command-lifecycle: admission before staging ===\n", 1
    )[1].split("# === end command-lifecycle: admission before staging ===", 1)[0]
    dispatch = "switch ($Action) {\n    'provision' {" + source.split(
        "    'provision' {", 1
    )[1].split("    'install' {", 1)[0] + "}\n"
    stamp = "function Copy-StampDirectory {" + source.split(
        "function Copy-StampDirectory {", 1
    )[1].split("if (-not $ContextualInstall -and $Action -in", 1)[0]
    dispatch = dispatch.replace("switch ($Action) {", "switch ($Action) {\n'stamp' { Invoke-Stamp }")
    program = (
        "param([string]$Action='provision',[string]$BootstrapPython='')\n$ErrorActionPreference='Stop'\n"
        "$ContextualInstall=$false\n$PluginDir=Split-Path -Parent $PSScriptRoot\n"
        "$InstallDir=$env:TEST_RUNTIME\n$BinDir=Join-Path $InstallDir 'bin'\n"
        "$LocalBin=Join-Path $InstallDir 'local-bin'\n"
        "$LinkDir=Join-Path $InstallDir '.venv'\n$VenvPython=$env:TEST_PYTHON\n"
        "$SrcVersion='fixture'\n$ServiceName='fixture'\n"
        "function Get-BootstrapPython { param([switch]$ExcludeVenvDir); $env:TEST_PYTHON }\n"
        "function Get-SourceKind { param($PluginPath); 'marketplace' }\n"
        "function Write-ServiceHeader {}\nfunction Write-ServiceOk {}\n"
        "function Write-ServiceSkipped {}\nfunction Write-ServiceChanged {}\n"
        "function Ensure-Uv { $true }\nfunction Ensure-UvIndex {}\n"
        "function Deploy-RuntimeResolvers { $true }\nfunction Deploy-GlobalBinstub {}\n"
        "function Ensure-InstallDir { param($Dir); [void][IO.Directory]::CreateDirectory($Dir) }\n"
        "function Invoke-NativeCapture { param($Action); $text=& $Action;"
        "@{ExitCode=$LASTEXITCODE;Output=($text -join \"`n\")} }\n"
        "function Deploy-VenvAndPackage {\n"
        "  [IO.File]::WriteAllText((Join-Path $InstallDir 'runtime-slot'),$env:TEST_OWNER)\n"
        "  Start-Sleep -Milliseconds 300\n}\n"
        "function Invoke-VersionedActivate {\n"
        "  Copy-Item (Join-Path $InstallDir 'runtime-slot') (Join-Path $InstallDir 'current-version')\n"
        "  $true\n}\n"
        + writer + "\n" + stamp + "\n" + admission + "\n" + dispatch
    )
    if action == "uninstall":
        program = program.replace(
            "param([switch]$ExcludeVenvDir)",
            "param([switch]$ExcludeVenvDir,[switch]$ExcludeRuntimeRoot)",
        )
        uninstall = source.split("    'uninstall' {\n", 1)[1].split("    'start' {\n", 1)[0]
        program += (
            "\n$RemoveConfig=($env:TEST_REMOVE_CONFIG -eq 'true')\n"
            "$VersionedRuntime=$true\n$LibDir=Join-Path $InstallDir 'lib'\n"
            "$ProjectName='example'\n$ProjectDir=Join-Path $InstallDir 'projects/example'\n"
            "$env:LOCALAPPDATA=Join-Path $InstallDir 'profile-cache'\n"
            "function Remove-Binstub {}\nfunction Remove-LegacyBinstubs {}\n"
            "switch ($Action) {\n'uninstall' {\n" + uninstall + "}\n"
        )
    script = payload / "scripts/install.ps1"
    script.write_text(program, encoding="utf-8")
    return [POWERSHELL, "-NoLogo", "-NoProfile", "-File", str(script), "-Action", action]


@pytest.mark.parametrize("shell", [
    pytest.param("bash", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX installer")),
    pytest.param("powershell", marks=pytest.mark.skipif(POWERSHELL is None, reason="PowerShell unavailable")),
])
@pytest.mark.parametrize("location", ["installed", "local-copy"])
def test_command_binding_real_installer_refuses_before_self_staging(
    binding_fixture: dict, shell: str, location: str, powershell_host,
) -> None:
    _prepare_catalog(binding_fixture)
    payload = binding_fixture["payload"]
    suffix = "sh" if shell == "bash" else "ps1"
    installer = payload / f"scripts/install.{suffix}"
    shutil.copyfile(ROOT / f"plugins/agent-worktrees/scripts/install.{suffix}", installer)
    shutil.copyfile(
        ROOT / f"plugins/agent-worktrees/scripts/installer-watchdog.{suffix}",
        payload / f"scripts/installer-watchdog.{suffix}",
    )
    _write(payload / "plugin.json", {"name": PLUGIN_ID})
    if location == "local-copy":
        copied_payload = binding_fixture["home"] / "local-payload"
        shutil.copytree(payload, copied_payload)
        installer = copied_payload / f"scripts/install.{suffix}"
    _publish(binding_fixture)
    stage = binding_fixture["legacy"] / ".install-stage/retained-0"
    stage.mkdir(parents=True)
    (stage / "retained").write_text("must not be reaped by foreign source", encoding="utf-8")
    environment = {
        **_catalog_environment(binding_fixture),
        "COPILOT_PLUGIN_INSTALL_SMOKE": "1", "COPILOT_PLUGIN_INSTALL_SMOKE_SLEEP": "0",
        "COPILOT_PLUGIN_INSTALL_STAGED": "1",
        "COPILOT_PLUGIN_STAGED_FROM": str(payload),
    }
    host = powershell_host(binding_fixture, environment) if shell == "powershell" else None
    settings = binding_fixture["home"] / ".copilot/settings.json"
    original = settings.read_bytes()
    if location == "installed":
        _write(settings, {"extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Other/Plugins"}},
        }})
    before = _snapshot(binding_fixture["home"])
    for action in ("install", "update", "provision", "stamp", "uninstall"):
        command = (
            ["bash", str(installer), action] if shell == "bash" else
            [POWERSHELL, "-NoLogo", "-NoProfile", "-File", str(installer), action]
        )
        refused = host.run(command) if host else subprocess.run(
            command, env=environment, cwd=binding_fixture["home"],
            capture_output=True, text=True, timeout=30,
        )
        assert refused.returncode != 0, refused.stdout + refused.stderr
        assert "Command ownership" in refused.stdout + refused.stderr
        assert _snapshot(binding_fixture["home"]) == before
    if host:
        host.close()
        assert _snapshot(binding_fixture["home"]) == before
    settings.write_bytes(original)
    if location == "local-copy":
        binding = binding_fixture["binding"]
        with binding._load_lifecycle().legacy_lifecycle(
            binding, binding_fixture["primitive"], reserve=False,
            **binding_fixture["arguments"],
        ) as lease:
            command = (
                ["bash", str(installer), "stamp"] if shell == "bash" else
                [POWERSHELL, "-NoLogo", "-NoProfile", "-File", str(installer), "stamp"]
            )
            admitted = subprocess.run(
                command, env={
                    **environment, "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE": json.dumps(lease.ticket),
                },
                cwd=binding_fixture["home"], capture_output=True, text=True, timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            assert admitted.returncode == 0, admitted.stdout + admitted.stderr
            smoke = json.loads((binding_fixture["legacy"] / "smoke.json").read_text())
            assert Path(smoke["ran_from"]) == installer.parent
            assert Path(smoke["staged_from"]) == payload
            lease.lock.assert_owned()
    # The unchanged self-stage block still runs under the admitted parent lease.
    installer = payload / f"scripts/install.{suffix}"
    command = (
        ["bash", str(installer), "stamp"] if shell == "bash" else
        [POWERSHELL, "-NoLogo", "-NoProfile", "-File", str(installer), "stamp"]
    )
    completed = subprocess.run(
        command, env=environment, cwd=binding_fixture["home"], capture_output=True,
        text=True, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    smoke = json.loads((binding_fixture["legacy"] / "smoke.json").read_text())
    assert smoke["staged"] is True
    assert Path(smoke["staged_from"]) == payload
    assert ".install-stage" in smoke["ran_from"]
    assert not _lifecycle_lock_path(binding_fixture["legacy"]).exists()


@pytest.mark.parametrize("shell", [
    pytest.param("bash", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX installer")),
    pytest.param("powershell", marks=pytest.mark.skipif(POWERSHELL is None, reason="PowerShell unavailable")),
])
@pytest.mark.parametrize("remove_config", [False, True])
def test_command_binding_native_teardown_refuses_foreign_then_removes_owned_runtime(
    binding_fixture: dict, shell: str, remove_config: bool,
) -> None:
    command = _native_fixture(binding_fixture, shell, "uninstall")
    _publish(binding_fixture)
    runtime = binding_fixture["legacy"]
    for relative in ("versions/fixture/retained", "lib/retained", "bin/session-options.sh"):
        path = runtime / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("retained", encoding="utf-8")
    environment = {
        **_catalog_environment(binding_fixture),
        "TEST_RUNTIME": str(runtime), "TEST_OWNER": "example",
        "TEST_REMOVE_CONFIG": str(remove_config).lower(), "COMPUTERNAME": "example-host",
        "TEST_PYTHON": sys.executable if os.name == "nt" else
        str(binding_fixture["home"] / "test-bin/python3"),
    }
    settings = binding_fixture["home"] / ".copilot/settings.json"
    original = settings.read_bytes()
    _write(settings, {"extraKnownMarketplaces": {
        "example": {"source": {"source": "github", "repo": "Other/Plugins"}},
    }})
    before = _snapshot(runtime)
    refused = subprocess.run(
        command, env=environment, cwd=binding_fixture["home"], capture_output=True,
        text=True, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert refused.returncode != 0, refused.stdout + refused.stderr
    assert "Command ownership" in refused.stdout + refused.stderr
    assert _snapshot(runtime) == before
    settings.write_bytes(original)
    completed = subprocess.run(
        command, env=environment, cwd=binding_fixture["home"], capture_output=True,
        text=True, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert runtime.exists() is not remove_config
    assert not (runtime / "deploy-manifest.json").exists()
    assert not (runtime / "versions").exists()
    assert not (runtime / "lib").exists()
    assert not _lifecycle_lock_path(runtime).exists()


@pytest.mark.parametrize("shell", [
    pytest.param("bash", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX installer")),
    pytest.param("powershell", marks=pytest.mark.skipif(POWERSHELL is None, reason="PowerShell unavailable")),
])
def test_command_binding_native_stamp_defers_without_stuck_reservation(
    binding_fixture: dict, shell: str,
) -> None:
    command = _native_fixture(binding_fixture, shell, "stamp")
    environment = {
        **_catalog_environment(binding_fixture),
        "TEST_RUNTIME": str(binding_fixture["legacy"]), "TEST_OWNER": "example",
        "TEST_PYTHON": sys.executable if os.name == "nt" else
        str(binding_fixture["home"] / "test-bin/python3"),
        "COMPUTERNAME": "example-host",
    }
    stamped = subprocess.run(
        command, env=environment, cwd=binding_fixture["home"],
        capture_output=True, text=True, timeout=45,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert stamped.returncode == 0, stamped.stdout + stamped.stderr
    runtime = binding_fixture["legacy"]
    assert not (runtime / "deploy-manifest.json").exists()
    assert not (runtime / "current-version").exists()
    assert _resolve(binding_fixture)["status"] == "unresolved"
    deferred = Path((runtime / "payload-dir").read_text().strip())
    if shell == "bash":
        provision = [command[0], str(deferred / "scripts/install.sh"), "provision"]
    else:
        provision = [
            command[0], "-NoLogo", "-NoProfile", "-File",
            str(deferred / "scripts/install.ps1"), "-Action", "provision",
        ]
    installed = subprocess.run(
        provision, env=environment, cwd=binding_fixture["home"],
        capture_output=True, text=True, timeout=45,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert (runtime / "current-version").read_text() == "example"
    manifest = json.loads((runtime / "deploy-manifest.json").read_text())
    if "commandBinding" in manifest:
        assert manifest["commandLifecycle"]["state"] == "complete"
    else:
        assert "commandLifecycle" not in manifest
    assert not _lifecycle_lock_path(runtime).exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows first-touch interpreter forwarding")
def test_command_binding_bootstrap_preserves_off_path_spaced_interpreter_through_stage(
    binding_fixture: dict,
) -> None:
    _prepare_catalog(binding_fixture)
    payload = binding_fixture["payload"]
    scripts = payload / "scripts"
    for name in (
        "install.ps1", "installer-watchdog.ps1",
        "bootstrap-check.ps1", "bootstrap-killswitch-guard.ps1",
    ):
        shutil.copyfile(ROOT / f"plugins/agent-worktrees/scripts/{name}", scripts / name)
    _write(payload / "plugin.json", {"name": PLUGIN_ID})
    python_root = binding_fixture["home"] / "Python with spaces"
    venv.EnvBuilder(with_pip=False, symlinks=False).create(python_root)
    python = python_root / "Scripts" / "python.exe"
    environment = {
        **_catalog_environment(binding_fixture),
        "PATH": str(Path(os.environ["SystemRoot"]) / "System32"),
        "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(binding_fixture["home"] / "absent.json"),
        "COPILOT_PLUGIN_INSTALL_SMOKE": "1", "COPILOT_PLUGIN_INSTALL_SMOKE_SLEEP": "0",
    }
    probe = subprocess.run(
        [str(python), "-I", "-c",
         "import json,sys; print(json.dumps([sys.executable,sys.prefix]))"],
        env=environment, cwd=binding_fixture["home"], capture_output=True,
        text=True, encoding="utf-8", timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    executable, prefix = json.loads(probe.stdout)
    assert Path(executable) == python and Path(prefix) == python_root
    completed = subprocess.run(
        [POWERSHELL, "-NoProfile", "-File", str(scripts / "bootstrap-check.ps1"),
         "-BootstrapPython", str(python)],
        env=environment, cwd=binding_fixture["home"], capture_output=True,
        text=True, encoding="utf-8", timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout) == {}
    assert "Bootstrap stamp failed" not in completed.stderr
    smoke = json.loads((binding_fixture["legacy"] / "smoke.json").read_text())
    assert smoke["staged"] is True
    assert Path(smoke["staged_from"]) == payload
    assert ".install-stage" in smoke["ran_from"]
    assert not _lifecycle_lock_path(binding_fixture["legacy"]).exists()


@pytest.mark.parametrize("shell", [
    pytest.param("bash", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX installer")),
    pytest.param("powershell", marks=pytest.mark.skipif(POWERSHELL is None, reason="PowerShell unavailable")),
])
def test_command_binding_native_lifecycle_admits_one_runtime_owner(
    binding_fixture: dict, shell: str,
) -> None:
    other = binding_fixture["home"] / ".copilot/installed-plugins/other" / PLUGIN_ID
    other.mkdir(parents=True)
    _write(binding_fixture["home"] / ".copilot/settings.json", {
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Example/Plugins"}},
            "other": {"source": {"source": "github", "repo": "Other/Plugins"}},
        },
    })
    environment = _catalog_environment(binding_fixture)
    commands = [_native_fixture({**binding_fixture, "payload": payload}, shell)
                for payload in (binding_fixture["payload"], other)]
    processes = []
    for index, command in enumerate(commands):
        processes.append(subprocess.Popen(
            command, env={
                **environment, "TEST_OWNER": str(index), "COMPUTERNAME": "example-host",
                "TEST_RUNTIME": str(binding_fixture["legacy"]),
                "TEST_PYTHON": sys.executable if os.name == "nt" else
                str(binding_fixture["home"] / "test-bin/python3"),
                "COPILOT_PLUGIN_ROOT": str((binding_fixture["payload"], other)[index]),
            },
            cwd=binding_fixture["home"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        ))
    outputs = [process.communicate(timeout=45) for process in processes]
    assert sum(process.returncode == 0 for process in processes) == 1, outputs
    winner = next(index for index, process in enumerate(processes) if process.returncode == 0)
    runtime = binding_fixture["legacy"]
    assert (runtime / "runtime-slot").read_text() == str(winner)
    assert (runtime / "current-version").read_text() == str(winner)
    assert json.loads((runtime / "deploy-manifest.json").read_text())["commandLifecycle"]["state"] == "complete"
    # A later foreign invocation must also refuse before any runtime file changes.
    before = _snapshot(runtime)
    refused = subprocess.run(
        processes[1 - winner].args,
        env={**environment, "TEST_RUNTIME": str(runtime), "TEST_OWNER": "foreign",
             "COMPUTERNAME": "example-host", "TEST_PYTHON": sys.executable if os.name == "nt" else
             str(binding_fixture["home"] / "test-bin/python3"),
             "COPILOT_PLUGIN_ROOT": str((binding_fixture["payload"], other)[1 - winner])},
        cwd=binding_fixture["home"], capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert refused.returncode != 0
    after = _snapshot(runtime)
    assert after.keys() == before.keys()
    assert all(after[path] == value for path, value in before.items() if path != ".")
