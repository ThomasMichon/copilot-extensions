"""The real Index installer removes code, not durable data or coordination."""
from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
FLAGS = no_window_kwargs()


def _launchers(payload: Path, mode: str) -> dict[str, str]:
    if mode == "installed":
        spec = importlib.util.spec_from_file_location("index_stamp_repair", payload / "scripts/stamp_repair.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        source = (payload / "scripts/install.ps1").read_text(encoding="utf-8-sig")
        launchers = {"agent-index": module.installed_launcher().decode()}
        for variable, name in (("ps1Content", "agent-index.ps1"), ("cmdContent", "agent-index.cmd")):
            match = re.search(r"(?ms)^    \$" + variable + r" = @'\n(.*?)\n'@$", source)
            assert match is not None
            launchers[name] = match[1]
        return launchers
    return {
        "agent-index": f'#!/usr/bin/env bash\nexec bash {shlex.quote(str(payload / "scripts/runtime-gate.sh"))} "$@"\n',
        "agent-index.ps1": f"& '{str(payload / 'scripts/runtime-gate.ps1').replace(chr(39), chr(39) * 2)}' @args\nexit $LASTEXITCODE\n",
        "agent-index.cmd": (
            '@echo off\nwhere pwsh >nul 2>&1\n'
            'if %ERRORLEVEL%==0 (pwsh -NoProfile -ExecutionPolicy Bypass -File '
            '"%~dp0agent-index.ps1" %*) else (powershell -NoProfile -ExecutionPolicy '
            'Bypass -File "%~dp0agent-index.ps1" %*)\nexit /b %ERRORLEVEL%\n'
        ),
    }


def _fixture(tmp_path: Path, mode: str = "installed") -> tuple[Path, Path, dict[str, str]]:
    home = tmp_path / "home"
    root = home / ".agent-index"
    home.mkdir(mode=0o700)
    for target in (root, root / "engine"):
        for name in ("versions/1.0/code", "snapshots/1.0/code", ".venv/pyvenv.cfg"):
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"reconstructible runtime")
        for name in ("current-version", "last-known-good", "stamped-version"):
            (target / name).write_text("1.0", encoding="utf-8")
        (target / "config.yaml").write_bytes(b"operator configuration")
        helpers = ("service.ps1", "service.sh") if target == root else (
            "engine.ps1", "engine.sh", *(
                f"versioned_runtime{suffix}.py" for suffix in (
                    "", "_leases", "_construction", "_source", "_publication",
                )
            ),
        )
        for name in helpers:
            (target / name).write_bytes(b"owned generated helper")
            if name.endswith(".py"):
                cache = target / "__pycache__"
                cache.mkdir(exist_ok=True)
                (cache / f"{Path(name).stem}.cpython-313.pyc").write_bytes(b"owned bytecode")
        if target != root:
            (target / "bin").mkdir()
            (target / "bin/native_capture.sh").write_bytes(b"owned generated capture helper")
        (target / "custom.py").write_bytes(b"operator helper")
        cache = target / "__pycache__"
        cache.mkdir(exist_ok=True)
        (cache / "custom.cpython-313.pyc").write_bytes(b"operator bytecode")
    (root / "deploy-manifest.json").write_text(json.dumps({
        "schema_version": 3, "service": "agent-index",
        "source": {"plugin": "agent-index", "kind": "local"},
    }), encoding="utf-8")
    (root / "engine" / "deploy-manifest.json").write_text(json.dumps({
        "schema_version": 3, "service": "agent-index-engine",
        "source": {"plugin": "agent-index", "kind": "local"},
    }), encoding="utf-8")
    (root / ".install.lock").write_bytes(b"persistent coordination inode")
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    for name, content in _launchers(SCRIPTS.parent, mode).items():
        (bin_dir / name).write_text(content, encoding="utf-8")
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENT_", "COPILOT_PLUGIN_", "COPILOT_EXTENSIONS_"))
        and key != "COPILOT_PROJECT_DIR"
    }
    environment.update(
        HOME=str(home), USERPROFILE=str(home), COPILOT_EXTENSIONS_TEST_CONTAINED="1",
        PATH=os.pathsep.join((
            str(Path(sys.executable).parent), str(Path(sys.base_prefix)),
            *[part for part in os.environ.get("PATH", "").split(os.pathsep)
              if "WindowsApps" not in part],
        )),
    )
    base = Path(str(getattr(sys, "_base_executable", sys.executable))).resolve()
    environment.setdefault(
        "UV_PYTHON_INSTALL_DIR", str(base.parents[1 if os.name == "nt" else 2]),
    )
    environment["UV_OFFLINE"] = "1"
    if os.name != "nt":
        for path in home.rglob("*"):
            path.chmod(0o700 if path.is_dir() else 0o600)
    return home, root, environment


def _run(
    tmp_path: Path, root: Path, environment: dict[str, str], host: str, *extra: str,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    executable = shutil.which(host)
    if executable is None:
        pytest.skip(f"{host} unavailable")
    if host != "bash" and os.name != "nt":
        pytest.skip("Whole Windows install transactions require native Windows")
    if host == "bash":
        if os.name == "nt":
            pytest.skip("Whole POSIX install transactions require native POSIX")
        fakebin = tmp_path / "fakebin"
        fakebin.mkdir(exist_ok=True)
        systemctl = fakebin / "systemctl"
        systemctl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        systemctl.chmod(0o755)
        environment = {**environment, "PATH": str(fakebin) + os.pathsep + environment["PATH"]}
        args = [executable, str(SCRIPTS / "install.sh"), "uninstall",
                "--install-dir", str(root), *extra]
    else:
        wrapper = tmp_path / "native-uninstall.ps1"
        wrapper.write_text(
            "param($Installer, [switch]$Purge, [switch]$DryRun, [switch]$DefaultRoot)\n"
            "$ErrorActionPreference='Stop'\n"
            "$Root=$env:INDEX_TEST_RUNTIME_ROOT\n"
            "function Get-ScheduledTask { param($TaskName,$ErrorAction) }\n"
            "function Stop-ScheduledTask { param($TaskName,$ErrorAction) }\n"
            "function Unregister-ScheduledTask { param($TaskName,$Confirm,$ErrorAction) }\n"
            "function Get-ItemProperty { param($Path,$Name,$ErrorAction) }\n"
            "function Remove-ItemProperty { param($Path,$Name,$ErrorAction) }\n"
            "if ($DefaultRoot) { & $Installer uninstall -Purge:$Purge -DryRun:$DryRun }\n"
            "else { & $Installer uninstall -InstallDir $Root -Purge:$Purge -DryRun:$DryRun }\n",
            encoding="utf-8",
        )
        environment = {**environment, "INDEX_TEST_RUNTIME_ROOT": str(root)}
        args = [executable, "-NoProfile", "-NonInteractive", "-File", str(wrapper),
                str(SCRIPTS / "install.ps1"), *extra]
    return subprocess.run(
        args, env=environment, cwd=cwd or tmp_path, capture_output=True,
        text=True, timeout=60, **FLAGS,
    )


@pytest.mark.skipif(os.name == "nt", reason="Native POSIX removal primitive")
def test_nested_engine_capture_helper_removal_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, root, _environment = _fixture(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("AGENT_INDEX_ENGINE_HOME", raising=False)
    source = SCRIPTS.parents[2] / "libs/installer-engine/installer_engine_uninstall.py"
    spec = importlib.util.spec_from_file_location("index_nested_helper_uninstall", source)
    assert spec and spec.loader
    uninstall = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = uninstall
    spec.loader.exec_module(uninstall)
    runtime = uninstall._load(SCRIPTS / "versioned_runtime.py")
    profile = uninstall.profile_for(SCRIPTS.parent, "agent-index")
    targets = uninstall._targets(SCRIPTS.parent, "agent-index", root, profile, runtime)
    plan = uninstall._plan(SCRIPTS.parent, root, profile, targets)
    shell = shutil.which("bash")
    assert shell
    lock = root / ".install.lock"
    before = lock.read_bytes(), lock.stat().st_ino
    try:
        for directory, names in plan:
            assert directory.is_relative_to(home)
            uninstall._native_remove(SCRIPTS.parent, shell, directory, names)
    finally:
        sys.modules.pop(spec.name, None)
    assert not (root / "engine/bin/native_capture.sh").exists()
    assert (lock.read_bytes(), lock.stat().st_ino) == before
    for target in (root, root / "engine"):
        assert (target / "config.yaml").read_bytes() == b"operator configuration"
        assert (target / "custom.py").read_bytes() == b"operator helper"


@pytest.mark.parametrize("host", ("powershell.exe", "pwsh", "bash"))
@pytest.mark.parametrize("purge", (False, True))
@pytest.mark.parametrize("mode", ("installed", "session"))
def test_real_native_removal_includes_nested_engine_and_preserves_transaction(
    tmp_path: Path, host: str, purge: bool, mode: str,
) -> None:
    home, root, environment = _fixture(tmp_path, mode)
    owned = [path for target in (root, root / "engine") for path in target.iterdir()
             if path.suffix in (".ps1", ".sh", ".py") and path.name != "custom.py"]
    owned += list((root / "engine" / "__pycache__").glob("versioned_runtime*.pyc"))
    owned.append(root / "engine/bin/native_capture.sh")
    lock = root / ".install.lock"
    before = (lock.read_bytes(), lock.stat().st_ino)
    extra = (("--purge" if host == "bash" else "-Purge"),) if purge else ()
    result = _run(tmp_path, root, environment, host, *extra)
    assert result.returncode == 0, result.stdout + result.stderr
    for target in (root, root / "engine"):
        if purge:
            assert not (target / "config.yaml").exists()
        else:
            assert (target / "config.yaml").read_bytes() == b"operator configuration"
        for name in (".venv", "versions/1.0", "snapshots/1.0", "current-version",
                     "last-known-good", "stamped-version"):
            assert not os.path.lexists(target / name), name
    assert (lock.read_bytes(), lock.stat().st_ino) == before
    assert not (root / "deploy-manifest.json").exists()
    assert not (root / "engine" / "deploy-manifest.json").exists()
    assert all(not path.exists() for path in owned)
    for name in ("agent-index", "agent-index.ps1", "agent-index.cmd"):
        assert not (home / ".local" / "bin" / name).exists()
    if not purge:
        for target in (root, root / "engine"):
            assert (target / "custom.py").read_bytes() == b"operator helper"
            assert (target / "__pycache__/custom.cpython-313.pyc").read_bytes() == b"operator bytecode"


@pytest.mark.parametrize("host", ("powershell.exe", "pwsh", "bash"))
def test_real_native_removal_preserves_modified_global_launchers(tmp_path: Path, host: str) -> None:
    home, root, environment = _fixture(tmp_path)
    commands = home / ".local/bin"
    ps1 = commands / "agent-index.ps1"
    ps1.write_text(ps1.read_text() + "\n# operator customization\n", encoding="utf-8")
    shell = commands / "agent-index"
    shell.write_text(shell.read_text() + "\n# operator customization\n", encoding="utf-8")
    before = {path: path.read_bytes() for path in commands.iterdir()}
    result = _run(tmp_path, root, environment, host)
    assert result.returncode == 0, result.stdout + result.stderr
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("host", ("powershell.exe", "pwsh", "bash"))
def test_real_native_preview_and_absent_root_do_not_mutate(
    tmp_path: Path, host: str,
) -> None:
    _, root, environment = _fixture(tmp_path)
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    result = _run(
        tmp_path, root, environment, host, "--dry-run" if host == "bash" else "-DryRun",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert {path: path.read_bytes() for path in before} == before
    absent = root.parent / ".absent-index"
    result = _run(tmp_path, absent, environment, host)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not absent.exists()


@pytest.mark.parametrize("host", ("powershell.exe", "pwsh"))
def test_real_native_preview_defaults_root_before_helper_admission(
    tmp_path: Path, host: str,
) -> None:
    _, root, environment = _fixture(tmp_path)
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    base = Path(str(getattr(sys, "_base_executable", sys.executable))).resolve()
    result = _run(
        tmp_path, root, environment, host, "-DryRun", "-DefaultRoot", cwd=base.parent,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert {path: path.read_bytes() for path in root.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("host", ("powershell.exe", "pwsh", "bash"))
@pytest.mark.parametrize("kind", ("foreign", "cell", "managed", "external-engine",
                                  "engine-cell", "engine-managed", "engine-foreign"))
def test_real_native_refusal_precedes_any_runtime_removal(
    tmp_path: Path, host: str, kind: str,
) -> None:
    _, root, environment = _fixture(tmp_path)
    if kind == "foreign":
        (root / "deploy-manifest.json").write_text(
            '{"schema_version":3,"service":"foreign"}', encoding="utf-8",
        )
    elif kind in ("cell", "managed", "engine-cell", "engine-managed"):
        marker = "install.json" if kind.endswith("cell") else "runtime-deployment.json"
        target = root / "engine" if kind.startswith("engine-") else root
        (target / marker).write_text("{}", encoding="utf-8")
    elif kind == "engine-foreign":
        (root / "engine" / "deploy-manifest.json").write_text(
            '{"schema_version":3,"service":"foreign"}', encoding="utf-8",
        )
    else:
        external = tmp_path / "external-engine"
        external.mkdir()
        (external / "state").write_bytes(b"external persistent state")
        environment["AGENT_INDEX_ENGINE_HOME"] = str(external)
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    result = _run(tmp_path, root, environment, host)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "refus" in (result.stdout + result.stderr).casefold()
    assert {path: path.read_bytes() for path in before} == before
    assert not (root / ".install-stage").exists()
    if kind == "external-engine":
        assert (external / "state").read_bytes() == b"external persistent state"
