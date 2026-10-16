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


@pytest.mark.parametrize("isolated", [False, True], ids=["safe-path", "isolated"])
def test_staged_profile_loads_only_its_owned_runtime_with_safe_path(tmp_path, isolated):
    staged = tmp_path / "staged payload" / "scripts"
    staged.mkdir(parents=True)
    for name in (
        "runtime_profile.py", "versioned_runtime.py", "versioned_runtime_leases.py",
        "versioned_runtime_construction.py",
    ):
        shutil.copyfile(SCRIPTS / name, staged / name)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "versioned_runtime.py").write_text(
        "raise AssertionError('loaded an ambient runtime helper')\n", encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, *(["-I"] if isolated else []), str(staged / "runtime_profile.py"), "--help"],
        cwd=foreign, env={**os.environ, "PYTHONSAFEPATH": "1", "PYTHONPATH": str(foreign)},
        capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--profile" in result.stdout
    assert result.stderr == ""


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
    kwargs.setdefault("uv", "fixture-uv")
    return profiles.provision(root, payload, python=sys.executable, **kwargs)


def _install_command(commands):
    return next(
        call for call in commands
        if any(Path(argument).name == "installer_dependency_policy.py" for argument in call)
    )


@pytest.mark.guard
def test_fresh_profile_is_base_and_completed_slot_is_reused(
    profiles, payload, commands, tmp_path
):
    root = tmp_path / "runtime"
    first = _provision(profiles, root, payload, uv="fixture-uv")
    assert first["profile"] == "base"
    assert first["runtimeVersion"] == "1.0.0+base"
    assert not any("[provider-exec]" in arg for call in commands for arg in call)
    install = _install_command(commands)
    assert install[:4] == [
        first["python"], "-I", str(SCRIPTS / "installer_dependency_policy.py"), "--uv",
    ]
    assert install[4:6] == ["fixture-uv", "--"]
    assert "--no-sources" in install
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


@pytest.mark.skipif(os.name != "nt", reason="Windows uses signed stdlib copies")
def test_windows_uv_profile_preserves_copies_without_seeding(
    profiles, payload, commands, tmp_path,
):
    _provision(profiles, tmp_path / "runtime", payload, uv="fixture-uv")
    create = next(call for call in commands if "venv" in call)
    assert create[:5] == [sys.executable, "-m", "venv", "--copies", "--without-pip"]
    assert all(call[:2] != ["fixture-uv", "venv"] for call in commands)
    assert _install_command(commands)[4:6] == ["fixture-uv", "--"]


def test_missing_uv_refuses_before_runtime_or_profile_changes(
    profiles, payload, commands, tmp_path,
):
    root = tmp_path / "runtime"
    with pytest.raises(RuntimeError, match="unrestricted pip fallback"):
        _provision(profiles, root, payload, uv=None)
    assert not root.exists()
    assert commands == []


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
    assert not (root / "versions" / "1.0.0+provider-exec").exists()


def test_native_profile_policy_refusal_retains_selected_runtime(
    profiles, tmp_path,
):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native UV is unavailable")
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    for name in (
        "runtime_profile.py", "versioned_runtime.py", "versioned_runtime_leases.py",
        "versioned_runtime_construction.py", "installer_dependency_policy.py",
        "installer_dependency_config.py",
    ):
        shutil.copyfile(SCRIPTS / name, scripts / name)
    (payload / "plugin.json").write_text(
        json.dumps({
            "name": "agent-containers", "version": "1.0.0",
            "repository": "https://github.com/ThomasMichon/copilot-extensions",
        }),
        encoding="utf-8",
    )
    (payload / "pyproject.toml").write_text(
        '[project]\nname="agent-containers"\nversion = "1.0.0"\n'
        '[project.optional-dependencies]\n'
        'provider-exec=["unseasoned @ https://example.invalid/unseasoned.whl"]\n',
        encoding="utf-8",
    )
    for name in profiles.LIBRARIES:
        library = payload / "libs" / name
        library.mkdir(parents=True)
        (library / "pyproject.toml").write_text(
            f'[project]\nname="fixture-{name}"\nversion="1.0.0"\n', encoding="utf-8",
        )
    root = tmp_path / "runtime"
    previous = profiles.runtime.slot(root, "0.9.0+base")
    profiles.runtime.mark_complete(root, "0.9.0+base")
    profiles.runtime.activate(root, "0.9.0+base", link_free=True)
    selector = (root / "current-version").read_bytes()
    marker = (previous / ".install-complete.json").read_bytes()
    completed = subprocess.run(
        [
            sys.executable, "-I", str(scripts / "runtime_profile.py"),
            "--root", str(root), "--payload", str(payload),
            "--python", sys.executable, "--uv", uv, "--profile", "provider-exec",
        ],
        capture_output=True, text=True, timeout=30, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert completed.returncode != 0
    assert "direct remote dependencies lack release-age evidence" in completed.stderr
    assert completed.stdout == ""
    assert (root / "current-version").read_bytes() == selector
    assert (previous / ".install-complete.json").read_bytes() == marker
    assert profiles.selected_profile(root) == "provider-exec"
    assert not (root / "versions" / "1.0.0+provider-exec").exists()


@pytest.mark.parametrize("stage", ["venv", "install", "health", "fingerprint"])
def test_failed_profile_construction_discards_before_releasing_ownership_and_retries(
    profiles, payload, commands, monkeypatch, tmp_path, stage,
):
    root = tmp_path / "runtime"
    previous = _provision(profiles, root, payload)
    selector = (root / "current-version").read_bytes()
    marker = Path(previous["venv"]) / ".install-complete.json"
    completed = marker.read_bytes()
    run = profiles._run

    def failing(arguments, *, cwd):
        run(arguments, cwd=cwd)
        if (
            (stage == "venv" and "venv" in arguments)
            or (stage == "install" and any(
                Path(argument).name == "installer_dependency_policy.py" for argument in arguments
            ))
            or (stage == "health" and "-c" in arguments)
        ):
            raise RuntimeError(f"fixture {stage} failed")
        if stage == "fingerprint" and "-c" in arguments:
            (payload / "src" / "module.py").write_text("# changed\n", encoding="utf-8")

    monkeypatch.setattr(profiles, "_run", failing)
    with pytest.raises(RuntimeError, match="source changed|fixture .* failed"):
        _provision(profiles, root, payload, requested="provider-exec")
    assert not (root / "versions" / "1.0.0+provider-exec").exists()
    assert (root / "current-version").read_bytes() == selector
    assert marker.read_bytes() == completed
    assert profiles.selected_profile(root) == "provider-exec"
    with profiles.runtime.construction_lease(root, "1.0.0+provider-exec", timeout=0.1):
        pass
    monkeypatch.setattr(profiles, "_run", run)
    retry = _provision(profiles, root, payload)
    assert retry["profile"] == "provider-exec"
    assert not retry["reused"]


def test_reused_profile_health_failure_never_discards_completed_slot(
    profiles, payload, commands, monkeypatch, tmp_path,
):
    root = tmp_path / "runtime"
    installed = _provision(profiles, root, payload)
    marker = Path(installed["venv"]) / ".install-complete.json"
    before = marker.read_bytes()
    run = profiles._run

    def failing(arguments, *, cwd):
        if "-c" in arguments:
            raise RuntimeError("fixture health failure")
        run(arguments, cwd=cwd)

    monkeypatch.setattr(profiles, "_run", failing)
    monkeypatch.setattr(
        profiles.runtime, "discard_failed_candidate",
        lambda *args, **kwargs: pytest.fail("discarded completed candidate"),
    )
    with pytest.raises(RuntimeError, match="health failure"):
        _provision(profiles, root, payload)
    assert marker.read_bytes() == before
    assert (root / "current-version").read_text().strip() == installed["runtimeVersion"]


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
    install = _install_command(commands)
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


@pytest.mark.parametrize("extension", ["ps1", "sh"])
def test_marketplace_self_stage_preserves_caller_relative_root_and_profile(tmp_path, extension):
    if extension == "ps1":
        if os.name != "nt":
            pytest.skip("native Windows PowerShell staging contract")
        shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        if not shell.is_file():
            pytest.skip("Windows PowerShell 5.1 unavailable")
    else:
        shell = shutil.which("bash")
        if os.name == "nt" or shell is None:
            pytest.skip("native POSIX staging contract")
    home = tmp_path / "home"
    home.mkdir()
    caller = tmp_path / "caller"
    caller.mkdir()
    payload = home / ".copilot" / "installed-plugins" / "example" / "agent-containers"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    (payload / "plugin.json").write_text('{"name":"agent-containers"}\n', encoding="utf-8")
    source = (SCRIPTS / f"init.{extension}").read_text(encoding="utf-8")
    marker = "# === end install-contract:v5 self-stage ==="
    shutil.copyfile(
        SCRIPTS / f"installer-watchdog.{extension}",
        scripts / f"installer-watchdog.{extension}",
    )
    prefix = source[:source.index(marker) + len(marker)]
    receipt = tmp_path / "staged-arguments.txt"
    if extension == "ps1":
        body = (
            '[System.IO.File]::WriteAllLines($env:PROFILE_STAGE_RECEIPT, '
            '[string[]]@($InstallDir, $Profile, $Action, [string]$Force.IsPresent, '
            '$env:COPILOT_PLUGIN_STAGED_FROM))\n'
        )
        arguments = [
            str(shell), "-NoProfile", "-File", str(scripts / "init.ps1"),
            "-Action", "provision", "-InstallDir", ".\\new runtime\\nested",
            "-Profile", "provider-exec", "-Force",
        ]
    else:
        body = (
            'printf "%s\\n" "$INSTALL_DIR" "$PROFILE" "$ACTION" "$FORCE" '
            '"$COPILOT_PLUGIN_STAGED_FROM" > "$PROFILE_STAGE_RECEIPT"\n'
        )
        arguments = [
            shell, str(scripts / "init.sh"), "provision",
            "--install-dir", "./new runtime/nested", "--profile", "provider-exec", "--force",
        ]
    (scripts / f"init.{extension}").write_text(prefix + "\n" + body, encoding="utf-8")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("COPILOT_", "AGENT_CONTAINERS_"))}
    env.update(HOME=str(home), USERPROFILE=str(home), PROFILE_STAGE_RECEIPT=str(receipt),
               COPILOT_EXTENSIONS_TEST_CONTAINED="1", COPILOT_PLUGIN_INSTALL_DEADLINE_SEC="20")
    completed = subprocess.run(
        arguments, cwd=caller, env=env, capture_output=True, text=True, timeout=30,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    root, profile, action, force, origin = receipt.read_text(encoding="utf-8-sig").splitlines()
    assert Path(root).is_absolute()
    assert Path(root).resolve() == caller / "new runtime" / "nested"
    assert (profile, action, force) == (
        "provider-exec", "provision", "True" if extension == "ps1" else "1",
    )
    assert Path(origin).resolve() == payload
    assert not (home / "new runtime").exists()


@pytest.mark.parametrize("escape", [False, True])
def test_native_health_requires_all_helper_code_inside_the_immutable_slot(
    profiles, tmp_path, escape,
):
    root = tmp_path / "runtime"
    subprocess.run(
        [sys.executable, "-m", "venv", "--copies", "--without-pip", str(root)],
        check=True, capture_output=True, text=True, timeout=15,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    python = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = root / (
        "Lib/site-packages" if os.name == "nt"
        else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    )
    for name in ["agent_containers", *(name.replace("-", "_") for name in profiles.LIBRARIES)]:
        (site / f"{name}.py").write_text("", encoding="utf-8")
    if escape:
        external = tmp_path / "mutable-source"
        external.mkdir()
        (site / "credential_relay.py").rename(external / "credential_relay.py")
        (site / "editable-helper.pth").write_text(str(external) + "\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="Runtime command failed"):
            profiles._health(python, "base", cwd=tmp_path)
    else:
        profiles._health(python, "base", cwd=tmp_path)
