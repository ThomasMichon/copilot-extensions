"""Run native removal adapters against disposable, byte-checked installations."""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
PLUGINS = ("agent-bridge", "agent-dispatch", "agent-vault", "budget-guidance")
LABELS = dict(zip(PLUGINS, ("Bridge", "Dispatch", "Vault", "Budget"), strict=True))
GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")
BASH = str(GIT_BASH) if GIT_BASH.is_file() else shutil.which("bash")
PWSH = shutil.which("pwsh")


def _path(path: Path) -> str:
    value = path.as_posix()
    return f"/{value[0].lower()}{value[2:]}" if os.name == "nt" else value


def _function(plugin: str, shell: str, name: str) -> str:
    text = (REPO / "plugins" / plugin / "scripts" / f"install.{shell}").read_text(
        encoding="utf-8",
    )
    declaration = f"function {name} {{" if shell == "ps1" else f"{name}() {{"
    line_start = text.rfind("\n", 0, text.index(declaration)) + 1
    indentation = text[line_start:text.index(declaration)]
    end = text.index(f"\n{indentation}}}\n", line_start) + len(indentation) + 2
    return textwrap.dedent(text[line_start:end])


def _run(tmp_path: Path, shell: str, body: str) -> subprocess.CompletedProcess[str]:
    executable = PWSH if shell == "ps1" else BASH
    if not executable:
        pytest.skip(f"Native {shell} host unavailable")
    script = tmp_path / f"fixture.{shell}"
    script.write_text(body, encoding="utf-8", newline="\n")
    args = (
        [executable, "-NoProfile", "-NonInteractive", "-File", str(script)]
        if shell == "ps1" else [executable, _path(script)]
    )
    return subprocess.run(  # noqa: S603 - fixture-owned script and fixed native host
        args, cwd=tmp_path, capture_output=True, text=True, timeout=40,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def _quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("shell", ("ps1", "sh"))
def test_named_helpers_removed_and_unknown_bytes_retained(
    tmp_path: Path, plugin: str, shell: str,
) -> None:
    install = tmp_path / "runtime"
    helpers = install / "bin"
    helpers.mkdir(parents=True)
    owned = helpers / "resolve-runtime.sh"
    owned.write_bytes(b"owned reconstructible helper")
    unknown = helpers / "custom-helper"
    unknown.write_bytes(b"\x00operator bytes\xff")
    before = (unknown.read_bytes(), unknown.stat().st_mtime_ns)
    if shell == "ps1":
        name = f"Remove-{LABELS[plugin]}HelperFiles"
        body = "\n".join((
            "$ErrorActionPreference='Stop'",
            f"$InstallDir={_quote(install)}",
            _function(plugin, shell, name),
            f"{name} -Directory {_quote(helpers)} -Names @('resolve-runtime.sh')",
        ))
    else:
        name = f"_remove_{LABELS[plugin].lower()}_helper_files"
        body = "\n".join((
            "set -euo pipefail",
            f"INSTALL_DIR={shlex.quote(_path(install))}",
            '_fail() { echo "$1" >&2; }',
            _function(plugin, shell, name),
            f"{name} {shlex.quote(_path(helpers))} resolve-runtime.sh",
        ))
    result = _run(tmp_path, shell, body)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not owned.exists()
    assert (unknown.read_bytes(), unknown.stat().st_mtime_ns) == before


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("shell", ("ps1", "sh"))
def test_nonfile_helper_refused_without_removing_unknown_files(
    tmp_path: Path, plugin: str, shell: str,
) -> None:
    install = tmp_path / "runtime"
    helpers = install / "bin"
    owned = helpers / "resolve-runtime.sh"
    owned.mkdir(parents=True)
    sentinel = owned / "unknown"
    sentinel.write_bytes(b"not an installer-owned file")
    if shell == "ps1":
        name = f"Remove-{LABELS[plugin]}HelperFiles"
        body = "\n".join((
            "$ErrorActionPreference='Stop'",
            f"$InstallDir={_quote(install)}",
            _function(plugin, shell, name),
            f"{name} -Directory {_quote(helpers)} -Names @('resolve-runtime.sh')",
        ))
    else:
        name = f"_remove_{LABELS[plugin].lower()}_helper_files"
        body = "\n".join((
            "set -euo pipefail",
            f"INSTALL_DIR={shlex.quote(_path(install))}",
            '_fail() { echo "$1" >&2; }',
            _function(plugin, shell, name),
            f"{name} {shlex.quote(_path(helpers))} resolve-runtime.sh",
        ))
    result = _run(tmp_path, shell, body)
    assert result.returncode != 0
    assert sentinel.read_bytes() == b"not an installer-owned file"


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("shell", ("ps1", "sh"))
def test_real_uninstall_foreign_ownership_preserves_all_durable_bytes(
    tmp_path: Path, plugin: str, shell: str,
) -> None:
    if shell == "sh" and os.name == "nt":
        pytest.skip("Whole POSIX install transactions require native POSIX, not Git Bash")
    executable = PWSH if shell == "ps1" else BASH
    if not executable:
        pytest.skip(f"Native {shell} host unavailable")
    install = tmp_path / "runtime"
    home = tmp_path / "home"
    home.mkdir()
    records = {
        "config.yaml": b"operator configuration",
        "tasks.db": b"persistent queue",
        "auth/token": b"fixture credential, not real",
        "run/endpoint.json": b'{"pid":0}',
        "logs/connect.log": b"retained connection history",
        "unknown.txt": b"\x00unknown bytes\xff",
        ".construction-leases/sentinel.lock": b"persistent lease inode",
        "versions/1.0/runtime.txt": b"runtime",
    }
    for relative, data in records.items():
        path = install / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (install / "deploy-manifest.json").write_text(json.dumps({
        "schema_version": 3, "service": "foreign", "source": {"plugin": "foreign"},
    }), encoding="utf-8")
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in install.rglob("*") if path.is_file()
    }
    script = REPO / "plugins" / plugin / "scripts" / f"install.{shell}"
    args = (
        [executable, "-NoProfile", "-NonInteractive", "-File", str(script),
         "-Action", "uninstall", "-InstallDir", str(install)]
        if shell == "ps1" else
        [executable, _path(script), "uninstall", "--install-dir", _path(install)]
    )
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENT_", "BUDGET_GUIDANCE_", "COPILOT_PLUGIN_"))
    }
    env.update(
        USERPROFILE=str(home), HOME=_path(home) if shell == "sh" else str(home),
        COPILOT_EXTENSIONS_TEST_CONTAINED="1",
        PATH=os.pathsep.join((
            str(Path(sys.executable).parent),
            *[part for part in os.environ.get("PATH", "").split(os.pathsep)
              if "WindowsApps" not in part],
        )),
    )
    result = subprocess.run(  # noqa: S603 - fixed installer and disposable installation
        args, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "foreign" in (result.stdout + result.stderr).lower(), result.stdout + result.stderr
    assert {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in before
    } == before


@pytest.mark.parametrize("plugin", ("agent-bridge", "agent-dispatch", "agent-vault"))
def test_foreign_manifest_refused_before_native_process_inspection(
    tmp_path: Path, plugin: str,
) -> None:
    install = tmp_path / "runtime"
    install.mkdir()
    manifest = install / "deploy-manifest.json"
    manifest.write_text('{"service":"foreign","source":{"plugin":"foreign"}}', encoding="utf-8")
    name = f"Get-{LABELS[plugin]}UninstallProcesses"
    body = "\n".join((
        "$ErrorActionPreference='Stop'",
        "$DryRun=$true",
        f"$InstallDir={_quote(install)}",
        "function Invoke-FiniteRuntimeUninstall { param($InstallRoot,[switch]$Check) }",
        "function Get-CimInstance { throw 'process inspection must not be reached' }",
        _function(plugin, "ps1", name),
        f"{name}",
    ))
    result = _run(tmp_path, "ps1", body)
    assert result.returncode != 0
    assert "foreign" in (result.stdout + result.stderr).lower()
    assert "process inspection must not be reached" not in result.stdout + result.stderr
    assert json.loads(manifest.read_text())["service"] == "foreign"


def test_selective_cleanup_integration_dependency_is_present(tmp_path: Path) -> None:
    """The new adapters must never silently fall back to the old purge API."""
    engine = REPO / "libs" / "installer-engine" / "installer-engine.ps1"
    result = _run(tmp_path, "ps1", "\n".join((
        "$ErrorActionPreference='Stop'",
        f". {_quote(engine)}",
        "$command=Get-Command Remove-InstallRuntimeContent -CommandType Function",
        "if (-not $command.Parameters.ContainsKey('EntryNames')) {",
        "    throw 'Pending shared dependency: selective runtime cleanup EntryNames API'",
        "}",
    )))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Native Windows process identity")
@pytest.mark.parametrize("plugin", ("agent-bridge", "agent-dispatch", "agent-vault"))
def test_real_live_foreign_pid_is_not_stop_authority(tmp_path: Path, plugin: str) -> None:
    install = tmp_path / "runtime"
    install.mkdir()
    receipt = install / "pid"
    receipt.write_text(str(os.getpid()), encoding="ascii")
    endpoint = install / "run" / "endpoint.json"
    endpoint.parent.mkdir()
    endpoint.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    name = f"Get-{LABELS[plugin]}UninstallProcesses"
    body = "\n".join((
        "$ErrorActionPreference='Stop';$DryRun=$true",
        f"$InstallDir={_quote(install)};$PidFile={_quote(receipt)}",
        "function Invoke-FiniteRuntimeUninstall { param($InstallRoot,[switch]$Check) }",
        "$TaskName='fixture';$SupervisorTaskName='fixture-supervisor'",
        "function Get-ScheduledTask { param($TaskName,$ErrorAction) }",
        _function(plugin, "ps1", name),
        f"{name}",
    ))
    result = _run(tmp_path, "ps1", body)
    assert result.returncode != 0
    assert "foreign process" in (result.stdout + result.stderr).lower()
    assert receipt.read_text() == str(os.getpid())
    assert json.loads(endpoint.read_text())["pid"] == os.getpid()


@pytest.mark.skipif(os.name != "nt", reason="Native Windows junction")
@pytest.mark.parametrize("plugin", ("agent-bridge", "agent-dispatch", "agent-vault"))
@pytest.mark.parametrize("indirection", ("junction", "traversal"))
def test_root_indirection_refused_before_process_inspection(
    tmp_path: Path, plugin: str, indirection: str,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"untouched outside bytes")
    root = tmp_path / "linked"
    name = f"Get-{LABELS[plugin]}UninstallProcesses"
    if indirection == "junction":
        setup = (
            f"New-Item -ItemType Junction -Path {_quote(root)} "
            f"-Target {_quote(outside)} | Out-Null"
        )
    else:
        root = outside / "child" / ".."
        setup = ""
    body = "\n".join((
        "$ErrorActionPreference='Stop';$DryRun=$true",
        setup,
        f"$InstallDir={_quote(root)}",
        "function Invoke-FiniteRuntimeUninstall { param($InstallRoot,[switch]$Check) }",
        "function Get-CimInstance { throw 'process inspection must not be reached' }",
        _function(plugin, "ps1", name),
        f"{name}",
    ))
    result = _run(tmp_path, "ps1", body)
    assert result.returncode != 0
    assert ("linked" if indirection == "junction" else "traversal") in (
        result.stdout + result.stderr
    ).lower()
    assert "process inspection must not be reached" not in result.stdout + result.stderr
    assert sentinel.read_bytes() == b"untouched outside bytes"


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("shell", ("ps1", "sh"))
def test_runtime_code_absent_and_retained_bytes_unchanged(
    tmp_path: Path, plugin: str, shell: str,
) -> None:
    install = tmp_path / "runtime"
    entries = {
        "versions/1.0/runtime.txt": b"runtime",
        "snapshots/1.0/code.py": b"source",
        "payload-dir": b"snapshot",
        "stamped-version": b"1.0",
        "current-version": b"1.0",
        "last-known-good": b"1.0",
        "deploy-manifest.json": json.dumps({
            "schema_version": 3, "service": plugin,
            "source": {"plugin": plugin, "kind": "local"},
        }).encode(),
        "bin/resolve-runtime.sh": b"generated resolver",
    }
    retained = {
        "config.yaml": b"configuration",
        "tasks.db": b"durable tasks",
        "run/endpoint.json": b"endpoint",
        "auth/custom": b"fixture auth",
        "logs/connect.log": b"history",
        "active.json": b"route receipt",
        ".install.lock": b"transaction authority",
        "bin/custom": b"unknown helper",
        "scripts/custom": b"unknown script",
        "unknown": b"\x00\xff",
    }
    for name, value in {**entries, **retained}.items():
        target = install / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
    runtime_path = REPO / "plugins" / plugin / "scripts" / "versioned_runtime.py"
    spec = importlib.util.spec_from_file_location("native_removal_released_leases", runtime_path)
    assert spec is not None and spec.loader is not None
    runtime = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runtime
    spec.loader.exec_module(runtime)
    with runtime._leases.batch_removal_custody(install, ["1.0"]):
        pass
    for path in install.glob(".*-leases/*.lock"):
        retained[str(path.relative_to(install))] = path.read_bytes()
    before = {
        name: ((install / name).read_bytes(), (install / name).stat().st_mtime_ns)
        for name in retained
    }
    host = PWSH if shell == "ps1" else BASH
    if not host or (shell == "ps1" and os.name != "nt"):
        pytest.skip("Required native host unavailable")
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    origin = REPO / "plugins" / plugin / "scripts"
    shutil.copytree(origin / "installation-context", scripts / "installation-context")
    module_path = REPO / "libs" / "installer-engine" / "installer_engine_uninstall.py"
    module_spec = importlib.util.spec_from_file_location(
        "native_finite_profile_fixture", module_path,
    )
    assert module_spec is not None and module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_spec.name] = module
    module_spec.loader.exec_module(module)
    for name in (*module.RUNTIME_MODULES, "command_binding.py", "command_lifecycle.py"):
        shutil.copyfile(origin / name, scripts / name)
    (payload / "pyproject.toml").write_text(
        f'[project]\nname="{plugin}"\nversion="1.0"\n', encoding="utf-8",
    )
    selected = module.profile_for(origin.parent, plugin)
    profile = scripts / "uninstall-profile.json"
    profile.write_text(json.dumps({
        "version": 1, "root_entries": list(selected.root_entries),
        "bin_entries": list(selected.bin_entries), "script_entries": list(selected.script_entries),
        "local_bin_entries": [], "module": "fixture_uninstall_worker",
    }), encoding="utf-8")
    driver = scripts / "uninstall-state.py"
    engine = Path(os.environ.get("COPILOT_UNINSTALL_TEST_ENGINE_ROOT", str(module_path.parent)))
    driver.write_text(
        "import importlib.util,pathlib,sys\n"
        f"spec=importlib.util.spec_from_file_location('native_finite',{str(module_path)!r})\n"
        "module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module\n"
        "spec.loader.exec_module(module)\n"
        f"engine=pathlib.Path({str(engine)!r});previous=module._dependency\n"
        "module._dependency=lambda payload,name: engine/name if name in "
        "('installer-engine.ps1','installer-engine.sh','installer_engine_process.py') "
        "else previous(payload,name)\n"
        "raise SystemExit(module.main())\n",
        encoding="utf-8",
    )
    result = subprocess.run(  # noqa: S603 - exact fixture CLI, trusted finite cleanup only
        [sys.executable, "-I", "-B", str(driver), "--payload-root", str(payload),
         "--plugin-id", plugin, "--root", str(install), "--shell", host,
         "--profile", str(profile), "--remove-runtime"],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any((install / name).exists() for name in entries)
    assert {
        name: ((install / name).read_bytes(), (install / name).stat().st_mtime_ns)
        for name in retained
    } == before
