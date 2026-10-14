from __future__ import annotations

import importlib
import builtins
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def profiles(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return importlib.import_module("runtime_profile")


@pytest.fixture
def payload(tmp_path, profiles):
    root = tmp_path / "payload"
    root.mkdir()
    (root / "pyproject.toml").write_text('version = "1.0.0"\n', encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "module.py").write_text("# fixture\n", encoding="utf-8")
    for name in profiles.LIBRARIES:
        library = root / "libs" / name
        library.mkdir(parents=True)
        (library / "pyproject.toml").write_text('version = "1.0.0"\n', encoding="utf-8")
    return root


@pytest.fixture
def commands(monkeypatch, profiles):
    calls = []

    def run(arguments, *, cwd):
        calls.append(arguments)
        if "venv" in arguments:
            slot = Path(arguments[-1])
            python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_bytes(b"fixture interpreter")

    monkeypatch.setattr(profiles, "_run", run)
    return calls


def _provision(profiles, root, payload, **kwargs):
    return profiles.provision(root, payload, python=sys.executable, **kwargs)


@pytest.mark.guard
def test_fresh_profile_is_base_and_completed_slot_is_reused(
    profiles, payload, commands, tmp_path
):
    root = tmp_path / "runtime"
    first = _provision(profiles, root, payload, uv="fixture-uv")
    assert first["profile"] == "base"
    assert first["runtimeVersion"] == "1.0.0+base"
    assert not any("[provider-exec]" in arg for call in commands for arg in call)
    install = next(call for call in commands if "install" in call)
    assert all(str(payload / "libs" / name) in install for name in profiles.LIBRARIES)
    slot = Path(first["venv"])
    before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns)
              for path in (slot / "runtime-profile.json", slot / ".install-complete.json")}
    commands.clear()
    again = _provision(profiles, root, payload, uv="fixture-uv")
    assert again["reused"]
    assert all("-I" in call for call in commands)
    assert before == {path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                      for path in (slot / "runtime-profile.json", slot / ".install-complete.json")}


def test_explicit_profile_switch_and_update_preserve_intent(
    profiles, payload, commands, tmp_path
):
    root = tmp_path / "runtime"
    base = _provision(profiles, root, payload)
    provider = _provision(profiles, root, payload, requested="provider-exec")
    assert provider["venv"] != base["venv"]
    assert str(payload) + "[provider-exec]" in commands[-2]
    assert (root / "current-version").read_text().strip() == "1.0.0+provider-exec"
    assert Path(base["venv"]).is_dir()
    (payload / "pyproject.toml").write_text('version = "1.0.1"\n', encoding="utf-8")
    updated = _provision(profiles, root, payload)
    assert updated["runtimeVersion"] == "1.0.1+provider-exec"


def test_switch_back_to_base_reuses_unchanged_slot(profiles, payload, commands, tmp_path):
    root = tmp_path / "runtime"
    base = _provision(profiles, root, payload)
    _provision(profiles, root, payload, requested="provider-exec")
    commands.clear()
    rollback = _provision(profiles, root, payload, requested="base")
    assert rollback["reused"]
    assert rollback["venv"] == base["venv"]
    assert all("-I" in call for call in commands)


def test_requested_provider_failure_never_falls_back_or_changes_active_slot(
    profiles, payload, commands, monkeypatch, tmp_path
):
    root = tmp_path / "runtime"
    _provision(profiles, root, payload)
    previous = (root / "current-version").read_bytes()
    run = profiles._run

    def failing(arguments, *, cwd):
        if str(payload) + "[provider-exec]" in arguments:
            raise RuntimeError("fixture provider install failed")
        run(arguments, cwd=cwd)

    monkeypatch.setattr(profiles, "_run", failing)
    with pytest.raises(RuntimeError, match="provider install failed"):
        _provision(profiles, root, payload, requested="provider-exec")
    assert (root / "current-version").read_bytes() == previous
    assert profiles.selected_profile(root) == "provider-exec"
    assert not (root / "versions" / "1.0.0+provider-exec" / ".install-complete.json").exists()


def test_same_version_source_change_refuses_immutable_rebuild(
    profiles, payload, commands, tmp_path
):
    root = tmp_path / "runtime"
    installed = _provision(profiles, root, payload)
    marker = Path(installed["venv"]) / ".install-complete.json"
    before = marker.read_bytes()
    (payload / "src" / "module.py").write_text("# changed\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="content-conflict"):
        _provision(profiles, root, payload)
    assert marker.read_bytes() == before


def test_relative_install_root_and_payload_use_the_callers_directory(
    profiles, payload, commands, tmp_path, monkeypatch
):
    caller = tmp_path / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    result = _provision(profiles, Path("runtime"), Path("..") / "payload")
    assert Path(result["venv"]).is_relative_to(caller / "runtime")
    create = next(call for call in commands if "venv" in call)
    assert Path(create[-1]).is_absolute()
    install = next(call for call in commands if "install" in call)
    assert str(payload) in install
    assert (caller / "runtime" / "current-version").is_file()
    assert not (payload / "runtime").exists()


@pytest.mark.parametrize("paramiko", [False, True])
def test_legacy_selected_runtime_preserves_installed_ssh_capability(
    profiles, tmp_path, paramiko
):
    root = tmp_path / "runtime"
    slot = profiles.runtime.slot(root, "0.9.0")
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"legacy fixture")
    if paramiko:
        metadata = slot / "Lib" / "site-packages" / "paramiko-4.0.0.dist-info"
        metadata.mkdir(parents=True)
        (metadata / "METADATA").write_text("Name: paramiko\nVersion: 4.0.0\n")
    profiles.runtime.mark_complete(root, "0.9.0")
    profiles.runtime.activate(root, "0.9.0", link_free=True)
    assert profiles.selected_profile(root) == ("provider-exec" if paramiko else "base")
    assert not (root / "runtime-profile.json").exists()


@pytest.mark.parametrize("value", [
    {"schema": "foreign", "version": 1, "profile": "base"},
    {"schema": "copilot-extensions.agent-containers.runtime-profile",
     "version": 1, "profile": "unknown"},
    {"schema": "copilot-extensions.agent-containers.runtime-profile",
     "version": True, "profile": "base"},
    [],
])
def test_invalid_durable_profile_fails_closed(profiles, tmp_path, value):
    (tmp_path / "runtime-profile.json").write_text(json.dumps(value))
    with pytest.raises(RuntimeError):
        profiles.selected_profile(tmp_path)


def test_package_failure_reports_only_bounded_sanitized_tail(profiles, tmp_path, capsys):
    script = (
        "import sys; "
        "print('\\n'.join('line-' + str(i) for i in range(60))); "
        "print('https://fixture:private@example.invalid/pkg token=private'); "
        "sys.exit(7)"
    )
    with pytest.raises(RuntimeError, match="exit 7"):
        profiles._run([sys.executable, "-c", script], cwd=tmp_path)
    error = capsys.readouterr().err
    assert len(error.splitlines()) == 40
    assert "line-0\n" not in error
    assert "private" not in error
    assert "line-59" in error


@pytest.mark.parametrize("missing", ["paramiko", "bcrypt"])
def test_missing_provider_dependency_is_explicit_and_does_not_hide_broken_installs(
    monkeypatch, missing
):
    from agent_containers import provider_ssh

    original = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "paramiko":
            raise ModuleNotFoundError(f"No module named {missing}", name=missing)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    if missing == "paramiko":
        with pytest.raises(RuntimeError, match="provider-exec profile"):
            provider_ssh._serve_ssh(None, stdin=io.BytesIO(), stdout=io.BytesIO())
    else:
        with pytest.raises(ModuleNotFoundError, match="bcrypt"):
            provider_ssh._serve_ssh(None, stdin=io.BytesIO(), stdout=io.BytesIO())


@pytest.mark.parametrize("profile", ["base", "provider-exec"])
def test_native_bootstrap_accepts_current_profile_without_reconcile(
    tmp_path, monkeypatch, profile
):
    shell = shutil.which("pwsh" if os.name == "nt" else "bash")
    if shell is None:
        pytest.skip("native installer shell unavailable")
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    ext = "ps1" if os.name == "nt" else "sh"
    bootstrap = scripts / f"bootstrap-check.{ext}"
    shutil.copyfile(SCRIPTS / bootstrap.name, bootstrap)
    (payload / "plugin.json").write_text('{"name":"agent-containers"}')
    (payload / "pyproject.toml").write_text('version = "1.0.0"\n')
    home = tmp_path / "home"
    root = home / ".agent-containers"
    runtime_version = f"1.0.0+{profile}"
    python = root / "versions" / runtime_version / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    python.parent.mkdir(parents=True)
    python.write_bytes(b"bootstrap fixture")
    python.chmod(0o700)
    (root / "current-version").write_text(runtime_version)
    (root / "runtime-profile.json").write_text(json.dumps({
        "schema": "copilot-extensions.agent-containers.runtime-profile",
        "version": 1, "profile": profile,
    }))
    (root / "deploy-manifest.json").write_text(json.dumps({
        "source": {"version": "1.0.0"}, "runtime_version": runtime_version,
        "runtime_profile": profile,
    }))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    argv = [shell]
    if os.name == "nt":
        argv += ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    completed = subprocess.run(
        [*argv, str(bootstrap)], capture_output=True, text=True, timeout=15, check=False
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "{}"
    assert "reconciling" not in completed.stderr
    assert not (root / "reconcile.lock").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 native stderr contract")
def test_signed_python_probe_continues_after_unavailable_candidate(tmp_path):
    shell = Path(os.environ["SystemRoot"]) / (
        "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    if not shell.is_file():
        pytest.skip("Windows PowerShell 5.1 unavailable")
    source = (SCRIPTS / "init.ps1").read_text(encoding="utf-8")
    start = source.index("$signedBase = $null")
    end = source.index("$profileArgs = @(", start)
    probe = source[start:end]
    command = (
        "$ErrorActionPreference = 'Stop'; "
        "$script:candidates = @(); "
        "function py { "
        "  $script:candidates += $args[0]; "
        "  if ($args[0] -eq '-3.13') { "
        "    & $env:ComSpec /c 'echo unavailable 1>&2 & exit /b 103'; "
        "  } else { "
        f"    '{sys.executable}'; $global:LASTEXITCODE = 0; "
        "  } "
        "}; "
        "function Get-AuthenticodeSignature { [pscustomobject]@{Status='Valid'} }; "
        f"{probe}\n"
        "if (($script:candidates -join ',') -ne '-3.13,-3.12') { throw 'probe stopped early' }; "
        "if ($ErrorActionPreference -ne 'Stop') { throw 'preference not restored' }; "
        f"if ($signedBase -ne '{sys.executable}') {{ throw 'candidate not selected' }}"
    )
    result = subprocess.run(
        [str(shell), "-NoProfile", "-Command", command],
        capture_output=True, text=True, check=False, timeout=15,
    )
    assert result.returncode == 0, result.stderr
