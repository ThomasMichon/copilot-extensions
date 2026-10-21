"""Native bootstrap and early first use consult the actual deployment owner."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest

from test_command_binding import (
    POWERSHELL, ROOT, _activate, _snapshot, _write,
    binding_fixture as binding_fixture,
    powershell_host as powershell_host,
)
from test_command_deployment import _policy
from test_command_lifecycle import _real_installer

SHELLS = [
    pytest.param("bash", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX entry point")),
    pytest.param("powershell", marks=pytest.mark.skipif(POWERSHELL is None, reason="PowerShell unavailable")),
]


def _native_fixture(fixture: dict, shell: str) -> dict:
    _, environment = _real_installer(fixture, shell, "installed")
    scripts = fixture["payload"] / "scripts"
    for name in ("bootstrap-check", "invoke-payload-runtime", "resolve-runtime"):
        for suffix in ("ps1", "sh"):
            shutil.copyfile(
                ROOT / f"plugins/agent-worktrees/scripts/{name}.{suffix}",
                scripts / f"{name}.{suffix}",
            )
    context = scripts / "installation-context"
    for path in (ROOT / "libs/installation-context").iterdir():
        if path.suffix in {".ps1", ".sh", ".awk"}:
            shutil.copyfile(path, context / path.name)
    if os.name != "nt":
        # Native adapters read the OS account profile, not Python's patched pwd.
        getent = fixture["home"] / "test-bin/getent"
        getent.write_text(
            "#!/bin/sh\n"
            'printf "fixture:x:%s:%s:fixture:%s:/bin/sh\\n" "$(id -u)" "$(id -g)" "$HOME"\n',
            encoding="utf-8",
        )
        getent.chmod(0o755)
        (fixture["home"] / "native-powershell.ps1").write_text(
            "if (-not ('CePosixAccount' -as [type])) {\n"
            "  Add-Type -TypeDefinition @'\n"
            "public static class CePosixAccount {\n"
            '  public static string Home() { return System.Environment.GetEnvironmentVariable("HOME"); }\n'
            "}\n"
            "'@\n"
            "}\n"
            "$scriptPath=$args[0]\n"
            "$forward=@($args | Select-Object -Skip 1)\n"
            "& $scriptPath @forward\n"
            "exit $LASTEXITCODE\n",
            encoding="utf-8",
        )
    environment["AGENT_WORKTREES_PAYLOAD_ROOT"] = str(fixture["payload"])
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    environment.pop("AGENT_WORKTREES_NO_SELFPROVISION", None)
    environment.pop("COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS", None)
    return environment


def _command(fixture: dict, shell: str, name: str) -> list[str]:
    suffix = "sh" if shell == "bash" else "ps1"
    script = fixture["payload"] / f"scripts/{name}.{suffix}"
    if shell == "bash":
        return ["bash", str(script)]
    prefix = [POWERSHELL, "-NoLogo", "-NoProfile", "-File"]
    if os.name != "nt":
        prefix.append(str(fixture["home"] / "native-powershell.ps1"))
    return [*prefix, str(script)]


def _run(fixture: dict, environment: dict, command: list[str], host=None):
    if host:
        return host.run(command)
    return subprocess.run(
        command, env=environment, cwd=fixture["home"], capture_output=True,
        text=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _state(fixture: dict, state: str) -> None:
    if state == "missing":
        return
    policy = _policy(fixture, managed=state != "standalone")
    if state == "invalid":
        policy["managed"] = "false"
    elif state == "foreign":
        policy["owner"]["sourceFingerprint"] = "sha256:" + "0" * 64
    elif state == "future":
        policy["version"] = 999
    _write(fixture["legacy"] / "runtime-deployment.json", policy)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("entry", ["bootstrap-check", "invoke-payload-runtime"])
@pytest.mark.parametrize("state", ["managed", "invalid", "foreign", "future"])
def test_native_publication_denial_precedes_all_product_writes(
    binding_fixture: dict, shell: str, entry: str, state: str, powershell_host,
) -> None:
    environment = _native_fixture(binding_fixture, shell)
    _state(binding_fixture, state)
    host = powershell_host(binding_fixture, environment) if shell == "powershell" else None
    before = _snapshot(binding_fixture["home"])
    result = _run(binding_fixture, environment, _command(binding_fixture, shell, entry), host)
    assert result.returncode == (3 if entry == "bootstrap-check" else 126), result
    assert "Runtime publication refused" in result.stderr
    assert "explicit installation management" in result.stderr
    if entry == "bootstrap-check":
        assert json.loads(result.stdout) == {}
    assert _snapshot(binding_fixture["home"]) == before
    if host:
        host.close()
        assert _snapshot(binding_fixture["home"]) == before


def _old_lib_copy(fixture: dict) -> Path:
    root = fixture["legacy"]
    package = root / "lib/agent_worktrees"
    package.mkdir(parents=True)
    (package / "retained.py").write_text("retained = True\n", encoding="utf-8")
    source = fixture["home"] / "old-launcher-source"
    (source / "src/agent_worktrees").mkdir(parents=True)
    (source / "src/agent_worktrees/new.py").write_text("updated = True\n", encoding="utf-8")
    (source / "pyproject.toml").write_text('version = "1.0.0"\n', encoding="utf-8")
    for arguments in (
        ["init", "--quiet", str(source)],
        ["-C", str(source), "add", "."],
        ["-C", str(source), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Fixture source"],
    ):
        subprocess.run(
            ["git", *arguments], check=True, capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    _write(root / "deploy-manifest.json", {
        "plugin_source": str(source), "commit": "older", "deployed_at": "retained",
    })
    (root / "bin").mkdir()
    for suffix in ("ps1", "sh"):
        shutil.copyfile(
            ROOT / f"plugins/agent-worktrees/scripts/resolve-runtime.{suffix}",
            root / f"bin/resolve-runtime.{suffix}",
        )
    _completed_slot(fixture)
    return package


def _completed_slot(fixture: dict) -> Path:
    slot = fixture["legacy"] / "versions/1.0.0"
    venv.EnvBuilder(with_pip=False).create(slot)
    _write(slot / ".install-complete.json", {
        "version": "1.0.0", "completed_at": "2026-01-01T00:00:00Z", "pid": 1,
    })
    (fixture["legacy"] / "current-version").write_text("1.0.0", encoding="utf-8")
    return slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("state", ["missing", "standalone", "managed", "foreign"])
def test_native_old_lib_copy_uses_invoked_owner_not_manifest_source(
    binding_fixture: dict, shell: str, state: str, powershell_host,
) -> None:
    environment = _native_fixture(binding_fixture, shell)
    package = _old_lib_copy(binding_fixture)
    environment["TEST_RUNTIME_PY"] = sys.executable
    _state(binding_fixture, state)
    host = powershell_host(binding_fixture, environment) if shell == "powershell" else None
    before = _snapshot(binding_fixture["home"])
    result = _run(
        binding_fixture, environment, _command(binding_fixture, shell, "bootstrap-check"), host,
    )
    assert json.loads(result.stdout) == {}, result
    if state in {"managed", "foreign"}:
        assert result.returncode == 3, result
        assert "Runtime publication refused" in result.stderr
        assert _snapshot(binding_fixture["home"]) == before
    else:
        assert result.returncode == 0, result
        assert not (package / "retained.py").exists()
        assert (package / "new.py").read_text() == "updated = True\n"
        assert (package / "_build_info.py").is_file()
        manifest = json.loads((binding_fixture["legacy"] / "deploy-manifest.json").read_text())
        assert manifest["commit"] != "older"


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("state", ["missing", "standalone"])
def test_native_first_stamp_retains_standalone_behavior(
    binding_fixture: dict, shell: str, state: str, powershell_host,
) -> None:
    environment = _native_fixture(binding_fixture, shell)
    _state(binding_fixture, state)
    host = powershell_host(binding_fixture, environment) if shell == "powershell" else None
    result = _run(
        binding_fixture, environment, _command(binding_fixture, shell, "bootstrap-check"), host,
    )
    assert result.returncode == 0, result
    assert json.loads(result.stdout) == {}
    smoke = json.loads((binding_fixture["legacy"] / "smoke.json").read_text())
    assert smoke["staged"] is True


@pytest.mark.parametrize("shell", SHELLS)
def test_native_healthy_dispatch_does_not_require_publication_permission(
    binding_fixture: dict, shell: str,
) -> None:
    environment = _native_fixture(binding_fixture, shell)
    _policy(binding_fixture)
    _completed_slot(binding_fixture)
    modules = binding_fixture["home"] / "runtime-module"
    modules.mkdir()
    (modules / "agent_worktrees.py").write_text("print('healthy-runtime')\n", encoding="utf-8")
    environment.update({"TEST_RUNTIME_PY": sys.executable, "PYTHONPATH": str(modules)})
    result = _run(
        binding_fixture, environment, _command(binding_fixture, shell, "invoke-payload-runtime"),
    )
    assert result.returncode == 0, result
    assert result.stdout.strip() == "healthy-runtime"
    assert "Runtime publication refused" not in result.stderr
    phases = {
        json.loads(line)["phase"]
        for line in (binding_fixture["legacy"] / "logs/activity.jsonl").read_text().splitlines()
    }
    assert {"resolver-marker-start", "resolver-marker-result", "resolver-slot-result", "dispatch"} <= phases
    assert not (binding_fixture["legacy"] / ".provision.lock").exists()
    assert not (binding_fixture["legacy"] / "smoke.json").exists()


@pytest.mark.parametrize("shell", SHELLS)
def test_native_first_use_rechecks_real_policy_under_existing_provision_lock(
    binding_fixture: dict, shell: str,
) -> None:
    environment = _native_fixture(binding_fixture, shell)
    policy = _policy(binding_fixture)
    pending = binding_fixture["home"] / "pending-policy.json"
    pending.write_text(json.dumps(policy), encoding="utf-8")
    (binding_fixture["legacy"] / "runtime-deployment.json").unlink()
    environment["TEST_PENDING_POLICY"] = str(pending)
    scripts = binding_fixture["payload"] / "scripts"
    (scripts / "resolve-runtime.ps1").write_text(
        "if ($null -ne $lock) {\n"
        "  if (-not $lock.CanWrite) { throw 'Provision lock is not held' }\n"
        "  Copy-Item -LiteralPath $env:TEST_PENDING_POLICY "
        "-Destination (Join-Path $runtimeRoot 'runtime-deployment.json')\n"
        "}\n",
        encoding="utf-8",
    )
    (scripts / "resolve-runtime.sh").write_text(
        'if [[ -e "$RUNTIME_ROOT/.provision.lock" ]]; then\n'
        '  (exec 8>"$RUNTIME_ROOT/.provision.lock"; ! flock -n 8) || exit 99\n'
        '  cp "$TEST_PENDING_POLICY" "$RUNTIME_ROOT/runtime-deployment.json"\n'
        "fi\n",
        encoding="utf-8",
    )
    result = _run(
        binding_fixture, environment, _command(binding_fixture, shell, "invoke-payload-runtime"),
    )
    assert result.returncode == 126, result
    assert "deployment-managed" in result.stderr
    assert "explicit installation management" in result.stderr
    assert (binding_fixture["legacy"] / ".provision.lock").exists()
    assert not (binding_fixture["legacy"] / "smoke.json").exists()
    assert not (binding_fixture["legacy"] / ".install-stage").exists()


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("state", ["missing", "standalone", "managed"])
def test_native_bootstrap_preserves_aliased_home(
    binding_fixture: dict, shell: str, state: str, powershell_host, tmp_path: Path,
) -> None:
    environment = _native_fixture(binding_fixture, shell)
    _state(binding_fixture, state)
    alias = tmp_path / "profile-alias"
    if os.name == "nt":
        result = subprocess.run(
            [os.environ["COMSPEC"], "/d", "/c", "mklink", "/J", str(alias),
             str(binding_fixture["home"])],
            capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0, result.stderr
    else:
        alias.symlink_to(binding_fixture["home"], target_is_directory=True)
    environment.update({"HOME": str(alias), "USERPROFILE": str(alias)})
    host = powershell_host(binding_fixture, environment) if shell == "powershell" else None
    before = _snapshot(binding_fixture["home"])
    result = _run(
        binding_fixture, environment, _command(binding_fixture, shell, "bootstrap-check"), host,
    )
    assert result.returncode == (3 if state == "managed" else 0), result
    if state == "managed":
        assert "deployment-managed" in result.stderr
        assert _snapshot(binding_fixture["home"]) == before
    else:
        assert (binding_fixture["legacy"] / "smoke.json").is_file()


@pytest.mark.parametrize("shell", SHELLS)
def test_native_first_use_reads_namespaced_policy_not_legacy(
    binding_fixture: dict, shell: str, powershell_host,
) -> None:
    environment = _native_fixture(binding_fixture, shell)
    selected = _activate(binding_fixture, binding_fixture["payload"])
    _write(binding_fixture["home"] / ".copilot-extensions/installation-mode.json", {
        "schema": "copilot-extensions.installation-mode", "version": 1,
        "installationMode": {"enabled": True, "marketplaces": {}},
    })
    environment["COPILOT_EXTENSIONS_CONTEXT"] = selected["installReceipt"]
    arguments = {
        **binding_fixture["arguments"],
        "environment": {**binding_fixture["environment"],
                        "COPILOT_EXTENSIONS_CONTEXT": selected["installReceipt"]},
    }
    primitive = binding_fixture["primitive"]
    resolution = primitive.resolve_runtime_deployment(**arguments)
    assert resolution["actualMode"] == "namespaced", resolution
    root = Path(resolution["runtimeRoot"])
    _write(root / "runtime-deployment.json", {
        "schema": primitive.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
        "owner": primitive._deployment_owner(resolution, arguments), "managed": True,
    })
    _write(binding_fixture["legacy"] / "runtime-deployment.json", {"managed": False})
    host = powershell_host(binding_fixture, environment) if shell == "powershell" else None
    before = _snapshot(binding_fixture["home"])
    result = _run(
        binding_fixture, environment, _command(binding_fixture, shell, "invoke-payload-runtime"),
        host,
    )
    assert result.returncode == 126, result
    assert "deployment-managed" in result.stderr
    assert _snapshot(binding_fixture["home"]) == before
    assert not (root / ".provision.lock").exists()


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("managed", [False, True])
def test_native_old_deployed_hook_requires_capable_helper_for_present_policy(
    binding_fixture: dict, shell: str, managed: bool, powershell_host,
) -> None:
    environment = _native_fixture(binding_fixture, shell)
    package = _old_lib_copy(binding_fixture)
    environment["TEST_RUNTIME_PY"] = sys.executable
    if managed:
        _policy(binding_fixture)
    suffix = "sh" if shell == "bash" else "ps1"
    (binding_fixture["payload"] / f"scripts/installation-context/installation-context.{suffix}").unlink()
    host = powershell_host(binding_fixture, environment) if shell == "powershell" else None
    before = _snapshot(binding_fixture["home"])
    result = _run(
        binding_fixture, environment, _command(binding_fixture, shell, "bootstrap-check"), host,
    )
    assert result.returncode == (3 if managed else 0), result
    if managed:
        assert "capable installation helper" in result.stderr
        assert _snapshot(binding_fixture["home"]) == before
    else:
        assert (package / "new.py").is_file()


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("old_copy", [False, True])
@pytest.mark.parametrize("mode_policy", ["missing", "disabled", "enabled", "invalid", "tombstone"])
def test_native_bootstrap_missing_provenance_uses_canonical_legacy_admission(
    binding_fixture: dict, shell: str, old_copy: bool, mode_policy: str, powershell_host,
) -> None:
    environment = _native_fixture(binding_fixture, shell)
    if old_copy:
        _old_lib_copy(binding_fixture)
    (binding_fixture["home"] / ".copilot/settings.json").unlink()
    if mode_policy in {"disabled", "enabled", "invalid"}:
        _write(binding_fixture["home"] / ".copilot-extensions/installation-mode.json", {
            "schema": "invalid" if mode_policy == "invalid" else "copilot-extensions.installation-mode",
            "version": 1, "installationMode": {"enabled": mode_policy == "enabled"},
        })
    elif mode_policy == "tombstone":
        _write(binding_fixture["legacy"] / ".installation-ownership.json", {})
    host = powershell_host(binding_fixture, environment) if shell == "powershell" else None
    before = _snapshot(binding_fixture["home"])
    result = _run(
        binding_fixture, environment, _command(binding_fixture, shell, "bootstrap-check"), host,
    )
    allowed = mode_policy == "missing"
    assert result.returncode == (0 if allowed else 3), result
    if allowed:
        output = (
            binding_fixture["legacy"] / "lib/agent_worktrees/new.py" if old_copy else
            binding_fixture["legacy"] / "smoke.json"
        )
        assert output.is_file()
    else:
        assert "Runtime publication refused" in result.stderr
        assert _snapshot(binding_fixture["home"]) == before


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("mode_policy", ["missing", "disabled"])
def test_native_first_use_missing_provenance_retains_standalone_dispatch(
    binding_fixture: dict, shell: str, mode_policy: str,
) -> None:
    environment = _native_fixture(binding_fixture, shell)
    (binding_fixture["home"] / ".copilot/settings.json").unlink()
    if mode_policy == "disabled":
        _write(binding_fixture["home"] / ".copilot-extensions/installation-mode.json", {
            "schema": "copilot-extensions.installation-mode", "version": 1,
            "installationMode": {"enabled": False},
        })
    scripts = binding_fixture["payload"] / "scripts"
    (scripts / "install.ps1").write_text(
        "param([Parameter(Position=0)][string]$Action)\n"
        "$root=Join-Path $env:USERPROFILE '.agent-worktrees'\n"
        "if ($Action -eq 'stamp') {\n"
        "  [IO.File]::WriteAllText((Join-Path $root 'payload-dir'), $env:AGENT_WORKTREES_PAYLOAD_ROOT)\n"
        "} else {\n"
        "  [IO.File]::WriteAllText((Join-Path $PSScriptRoot 'resolve-runtime.ps1'), "
        "'$AgentRtPy = $env:TEST_RUNTIME_PY')\n"
        "}\n",
        encoding="utf-8",
    )
    (scripts / "install.sh").write_text(
        'if [[ "$1" == stamp ]]; then\n'
        '  printf "%s" "$AGENT_WORKTREES_PAYLOAD_ROOT" > "$HOME/.agent-worktrees/payload-dir"\n'
        "else\n"
        "  printf '%s\\n' 'AGENT_RT_PY=\"$TEST_RUNTIME_PY\"' > \"$(dirname \"$0\")/resolve-runtime.sh\"\n"
        "fi\n",
        encoding="utf-8",
    )
    modules = binding_fixture["home"] / "runtime-module"
    modules.mkdir()
    (modules / "agent_worktrees.py").write_text("print('standalone-runtime')\n", encoding="utf-8")
    environment.update({"TEST_RUNTIME_PY": sys.executable, "PYTHONPATH": str(modules)})
    result = _run(
        binding_fixture, environment, _command(binding_fixture, shell, "invoke-payload-runtime"),
    )
    assert result.returncode == 0, result
    assert result.stdout.strip() == "standalone-runtime"
