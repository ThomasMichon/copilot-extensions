"""Codespaces engine wiring, retry behavior, and standalone payload regressions."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from .installer_test_support import (
    assert_installer_isolated,
    guard_installer_payload,
    isolate_installer_environment,
)

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]
ENGINE = REPO / "libs" / "installer-engine"
PWSH = shutil.which("pwsh")
PACKAGES = (
    "agent-ssh-manager",
    "agent-credential-relay",
    "agent-config-migrate",
    "agent-zdd",
    "agent-venue-copilot",
    "agent-session-liveness-probe",
    "agent-single-instance-lease",
    "agent-remote-login-shell",
    "agent-codespaces",
)
VARIABLES = (
    "SSH_MGR_DIR",
    "CRED_RELAY_DIR",
    "CFG_MIGRATE_DIR",
    "ZDD_DIR",
    "VENUE_COPILOT_DIR",
    "SESSION_LIVENESS_PROBE_DIR",
    "SINGLE_INSTANCE_LEASE_DIR",
    "REMOTE_LOGIN_SHELL_DIR",
    "PLUGIN_DIR",
)
PS_VARIABLES = (
    "SshMgrDir",
    "CredRelayDir",
    "CfgMigrateDir",
    "ZddDir",
    "VenueCopilotDir",
    "SessionLivenessProbeDir",
    "SingleInstanceLeaseDir",
    "RemoteLoginShellDir",
    "PluginDir",
)
pytestmark = pytest.mark.timeout(60)


def _env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        "PYTHONHOME",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "UV_PROJECT_ENVIRONMENT",
        "COPILOT_PLUGIN_STAGED_FROM",
        "COPILOT_PLUGIN_INSTALL_SMOKE",
    ):
        env.pop(key, None)
    for key, suffix in {
        "HOME": "home",
        "USERPROFILE": "home",
        "APPDATA": "roaming",
        "LOCALAPPDATA": "local",
        "PROGRAMDATA": "program",
        "XDG_CONFIG_HOME": "config",
        "XDG_CACHE_HOME": "cache",
        "XDG_DATA_HOME": "data",
        "XDG_STATE_HOME": "state",
        "XDG_RUNTIME_DIR": "run",
        "TEMP": "tmp",
        "TMP": "tmp",
        "TMPDIR": "tmp",
        "COPILOT_HOME": "copilot",
        "AGENT_HOME": "home",
    }.items():
        root = tmp_path / suffix
        root.mkdir(exist_ok=True)
        env[key] = str(root)
    env["COPILOT_PLUGIN_INSTALL_STAGED"] = "1"
    env["TEST_UV_LOG"] = str(tmp_path / "uv.jsonl")
    return env


def _function(ext: str, name: str) -> str:
    source = (PLUGIN / "scripts" / f"install.{ext}").read_text(encoding="utf-8")
    pattern = (
        rf"(?ms)^{re.escape(name)}\(\) [{{(].*?^[}})]"
        if ext == "sh"
        else (rf"(?ms)^function {re.escape(name)} \{{.*?^\}}")
    )
    match = re.search(pattern, source)
    assert match, name
    return match.group(0) + "\n"


def _run(ext: str, script: str, tmp_path: Path, env: dict[str, str]):
    path = tmp_path / f"harness.{ext}"
    path.write_text(script, encoding="utf-8")
    command = ["bash", str(path)] if ext == "sh" else [PWSH, "-NoProfile", "-File", str(path)]
    try:
        return subprocess.run(
            command, env=env, cwd=tmp_path, capture_output=True, text=True,
            timeout=45, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        log = tmp_path / "uv.jsonl"
        calls = log.read_text() if log.is_file() else "no UV calls"
        pytest.fail(f"native fixture timeout; UV calls: {calls}; stdout: {exc.stdout}; stderr: {exc.stderr}")


def _fake_uv(tmp_path: Path) -> Path:
    driver = tmp_path / "uv-driver.py"
    driver.write_text(
        """import json, os, subprocess, sys, tomllib
from pathlib import Path
args = sys.argv[1:]
if "--show-settings" in args:
    print('''PipInstallSettings { settings: PipSettings {
        index_locations: IndexLocations { indexes: [], flat_index: [], },
        exclude_newer: ExcludeNewer { global: None, package: ExcludeNewerPackage({}), },
    }, }''')
    raise SystemExit(0)
def unseasoned(arguments):
    result = []
    items = iter(arguments)
    for argument in items:
        if argument in ("--exclude-newer", "--config-file"):
            next(items)
        elif argument == "--config-setting":
            assert next(items) == "editable_mode=compat"
        elif argument != "--reinstall":
            result.append(argument)
    return result
expected_sources = json.loads(os.environ.get("TEST_UV_EXPECTED_SOURCES", "{}"))
if expected_sources and args[:2] == ["pip", "install"]:
    config = Path(args[args.index("--config-file") + 1]).resolve()
    private_root = config.parent
    assert config.is_file() and not config.is_symlink()
    assert private_root.name == "inputs"
    if os.name != "nt":
        assert private_root.stat().st_uid == os.getuid()
        assert private_root.stat().st_mode & 0o077 == 0
    for index, argument in enumerate(args):
        source = Path(argument)
        if not source.is_dir() or not (source / "pyproject.toml").is_file():
            continue
        project = tomllib.loads((source / "pyproject.toml").read_text())["project"]
        original = Path(expected_sources[project["name"]])
        assert source.resolve() != original.resolve()
        assert any(parent.samefile(private_root) for parent in source.resolve().parents)
        assert project == tomllib.loads((original / "pyproject.toml").read_text())["project"]
        for member in original.rglob("*.py"):
            assert (source / member.relative_to(original)).read_bytes() == member.read_bytes()
        args[index] = str(original)
    if os.name == "nt" and "--editable" in args:
        expected_parents = {Path(value).resolve().parent for value in expected_sources.values()}
        assert any(private_root.parent.parent.samefile(parent) for parent in expected_parents)
    else:
        assert private_root.parent.parent.samefile(Path(os.environ["TEMP"]))
    assert not any(private_root.resolve().is_relative_to(Path(value).resolve())
                   for value in expected_sources.values())
log = Path(os.environ["TEST_UV_LOG"])
history = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
with log.open("a") as stream:
    stream.write(json.dumps(args) + "\\n")
if args == ["--version"]:
    print("uv test")
    raise SystemExit(int(os.environ.get("TEST_UV_VERSION_EXIT", "0")))
mode = os.environ.get("TEST_UV_MODE", "ok")
if mode == "fatal":
    print("permanent failure")
    raise SystemExit(23)
count = sum(unseasoned(item) == unseasoned(args) for item in history)
if args[:2] == ["pip", "install"]:
    first = next((unseasoned(item) for item in history if item[:2] == ["pip", "install"]), unseasoned(args))
    if mode == "sre" and unseasoned(args) == first and count < 2:
        print("SRE module mismatch")
        raise SystemExit(1)
    raise SystemExit(0)
if args[0] == "venv":
    root = Path(args[1])
    if mode == "fallback" and "--python" in args:
        print("requested Python unavailable")
        raise SystemExit(24)
    root.mkdir(parents=True, exist_ok=True)
    real_venv = os.environ.get("TEST_UV_REAL_VENV") == "1"
    if real_venv:
        subprocess.run(
            [sys._base_executable, "-I", "-m", "venv", "--without-pip", "--copies", str(root)],
            check=True,
        )
    else:
        (root / "bin").mkdir(exist_ok=True)
        (root / "bin" / "python").touch()
        (root / "Scripts").mkdir(exist_ok=True)
        (root / "Scripts" / "python.exe").touch()
    if mode == "missing-cfg" and count < 2:
        (root / "pyvenv.cfg").unlink(missing_ok=True)
        raise SystemExit(0)
    if mode == "corrupt" and count < 2:
        (root / "pyvenv.cfg").unlink(missing_ok=True)
        print("failed to locate pyvenv.cfg; exit code: 106")
        raise SystemExit(106)
    if mode == "sre" and count < 2:
        print("SRE module mismatch")
        raise SystemExit(1)
    if not real_venv:
        (root / "pyvenv.cfg").write_text("home = test")
    raise SystemExit(0)
raise SystemExit(2)
""",
        encoding="utf-8",
    )
    if os.name == "nt":
        uv = tmp_path / "uv.cmd"
        uv.write_text(
            f'@"{sys.executable}" "{driver}" %*\n@exit /b %ERRORLEVEL%\n',
            encoding="utf-8",
        )
    else:
        uv = tmp_path / "uv"
        uv.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{driver}" "$@"\n', encoding="utf-8")
        uv.chmod(0o755)
    return uv


def _without_policy_arguments(arguments):
    assert "--exclude-newer" in arguments and "--reinstall" in arguments
    result = []
    items = iter(arguments)
    for argument in items:
        if argument in ("--exclude-newer", "--config-file"):
            next(items)
        elif argument == "--config-setting":
            assert next(items) == "editable_mode=compat"
        elif argument != "--reinstall":
            result.append(argument)
    return result

def _prelude(ext: str, engine: Path = ENGINE) -> str:
    if ext == "sh":
        return (
            "set -euo pipefail\n"
            '_warn() { echo "$*" >&2; }\n_fail() { echo "$*" >&2; }\n'
            "_step() { :; }\n_ok() { :; }\nsleep() { :; }\n"
            f'. "{engine / "installer-engine.sh"}"\n'
        )
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "function Write-ServiceWarn { param($Msg) [Console]::Error.WriteLine($Msg) }\n"
        "function Write-ServiceErr { param($Msg) [Console]::Error.WriteLine($Msg) }\n"
        "function Write-ServiceOk { param($Msg) }\n"
        "function Write-Warn { param($Msg) Write-ServiceWarn $Msg }\n"
        "function Write-Ok { param($Msg) }\nfunction Start-Sleep { param($Seconds) }\n"
        f". '{engine / 'installer-engine.ps1'}'\n"
    )


@pytest.fixture
def launch_runtime(tmp_path):
    slot = tmp_path / "runtime" / "versions" / "1.2.3"
    env = _env(tmp_path)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", "--copies", str(slot)],
        env=env, capture_output=True, check=True, timeout=30,
        creationflags=flags,
    )
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    assert python.resolve().is_relative_to(slot)
    site = subprocess.check_output(
        [str(python), "-I", "-c", "import site; print(site.getsitepackages()[0])"],
        env=env, text=True, timeout=10, creationflags=flags,
    ).strip()
    package = Path(site) / "agent_codespaces"
    package.mkdir()
    return slot, python, package, env


@pytest.mark.portfolio_tier("T3")
@pytest.mark.parametrize(
    "ext",
    [
        pytest.param("sh", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX harness")),
        pytest.param("ps1", marks=pytest.mark.skipif(PWSH is None, reason="pwsh unavailable")),
    ],
)
@pytest.mark.parametrize(
    "state", ["healthy", "namespace", "broken-entrypoint", "shadowed", "invalid-interpreter"],
)
def test_codespaces_activation_requires_installed_module_launch(
    tmp_path, launch_runtime, ext, state,
):
    slot, python, package, env = launch_runtime
    if state != "namespace":
        (package / "__init__.py").write_text("", encoding="utf-8")
    if state == "healthy":
        (package / "__main__.py").write_text(
            "import argparse\nargparse.ArgumentParser().parse_args()\n",
            encoding="utf-8",
        )
    elif state == "broken-entrypoint":
        (package / "__main__.py").write_text(
            "import missing_codespaces_runtime_dependency\n", encoding="utf-8",
        )
    elif state == "shadowed":
        shadow = tmp_path / "agent_codespaces"
        shadow.mkdir()
        (shadow / "__init__.py").write_text("", encoding="utf-8")
        (shadow / "__main__.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
        env["PYTHONPATH"] = str(tmp_path)
    elif state == "invalid-interpreter":
        python.write_text("not an interpreter\n", encoding="utf-8")

    if state == "namespace":
        imported = subprocess.run(
            [str(python), "-I", "-c", "import agent_codespaces"],
            env=env, capture_output=True, timeout=10, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert imported.returncode == 0
    root = slot.parents[1]
    marker = slot / ".install-complete.json"
    marker.write_text('{"version":"1.2.3","completed_at":"2026-10-01"}', encoding="utf-8")
    selector = root / "current-version"
    selector.write_text("previous", encoding="utf-8")
    before = marker.read_bytes(), selector.read_bytes()
    published = tmp_path / "published"
    if ext == "sh":
        script = (
            _prelude(ext)
            + _function(ext, "_codespaces_runtime_healthy")
            + _function(ext, "_versioned_activate")
            + f"""
VERSIONED_RUNTIME=1
SRC_VERSION='1.2.3'
VENV_DIR='{slot}'
INSTALL_DIR='{root}'
SCRIPT_DIR='{PLUGIN / "scripts"}'
_versioned_mark_complete() {{ printf 'published' > '{published}'; return 1; }}
_versioned_activate
"""
        )
    else:
        script = (
            _prelude(ext)
            + _function(ext, "Test-CodespacesRuntime")
            + _function(ext, "Invoke-VersionedActivate")
            + f"""
$VersionedRuntime=$true
$global:LASTEXITCODE=0
$SrcVersion='1.2.3'
$VenvPython='{python}'
$LinkPython=$VenvPython
function Invoke-VersionedMarkComplete {{
    [IO.File]::WriteAllText('{published}', 'published')
    return $false
}}
if (-not (Invoke-VersionedActivate)) {{ exit 1 }}
"""
        )
    result = _run(ext, script, tmp_path, env)
    assert result.returncode != 0, result.stdout + result.stderr
    assert published.exists() == (state == "healthy"), result.stdout + result.stderr
    assert (marker.read_bytes(), selector.read_bytes()) == before
    if state not in ("healthy", "invalid-interpreter"):
        assert "module launch health gate" in result.stdout + result.stderr


@pytest.mark.parametrize(
    "ext",
    [
        pytest.param("sh", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX harness")),
        pytest.param("ps1", marks=pytest.mark.skipif(PWSH is None, reason="pwsh unavailable")),
    ],
)
def test_codespaces_completed_namespace_refuses_before_construction(
    tmp_path, launch_runtime, ext,
):
    slot, python, package, env = launch_runtime
    marker = slot / ".install-complete.json"
    marker.write_text('{"version":"1.2.3","completed_at":"2026-10-01"}', encoding="utf-8")
    sentinel = package / "remaining-module.py"
    sentinel.write_text("unchanged\n", encoding="utf-8")
    before = marker.read_bytes(), sentinel.read_bytes()
    forbidden = tmp_path / "construction"
    if ext == "sh":
        script = (
            _prelude(ext)
            + _function(ext, "_codespaces_runtime_healthy")
            + _function(ext, "deploy_venv")
            + f"""
SRC_VERSION='1.2.3'
VENV_DIR='{slot}'
VENV_PYTHON='{python}'
FORCE=true
_assert_uv() {{ printf 'uv' > '{forbidden}'; return 1; }}
deploy_venv
"""
        )
    else:
        script = (
            _prelude(ext)
            + _function(ext, "Test-CodespacesRuntime")
            + _function(ext, "Deploy-Venv")
            + f"""
$SrcVersion='1.2.3'
$VenvDir='{slot}'
$VenvPython='{python}'
$Force=$true
function Assert-Uv {{
    [IO.File]::WriteAllText('{forbidden}', 'uv')
    throw 'construction must not start'
}}
if (-not (Deploy-Venv)) {{ exit 1 }}
"""
        )
    result = _run(ext, script, tmp_path, env)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "Refusing an in-place rebuild" in result.stdout + result.stderr
    assert not forbidden.exists()
    assert (marker.read_bytes(), sentinel.read_bytes()) == before


@pytest.mark.parametrize("ext", ["sh", "ps1"])
@pytest.mark.parametrize("mode", ["sre", "missing-cfg", "corrupt", "fallback", "fatal"])
def test_codespaces_deploy_venv_uses_shared_retry_and_fallback(tmp_path, ext, mode):
    if ext == "ps1" and PWSH is None:
        pytest.skip("pwsh unavailable")
    if ext == "sh" and os.name == "nt":
        pytest.skip("POSIX harness")
    env = _env(tmp_path)
    uv = _fake_uv(tmp_path)
    env["TEST_UV_MODE"] = mode
    env["TEST_UV_REAL_VENV"] = "1"
    root = tmp_path / "runtime"
    slot = root / "versions" / "1.2.3"
    if ext == "sh":
        script = (
            _prelude(ext)
            + _function(ext, "_versioned_slot_clean")
            + _function(ext, "deploy_venv")
            + f"""
SCRIPT_DIR='{PLUGIN / "scripts"}'
INSTALL_DIR='{root}'
SRC_VERSION=1.2.3
VERSIONED_RUNTIME=1
LINK_DIR="$INSTALL_DIR/.venv"
VENV_DIR='{slot}'
VENV_PYTHON="$VENV_DIR/bin/python"
UV_CMD='{uv}'
_bootstrap_python() {{ printf '%s\\n' '{sys._base_executable}'; }}
_payload_hash() {{ printf '%s' aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; }}
_assert_uv() {{ :; }}
_ensure_uv_index() {{ :; }}
deploy_venv
# This stage-only test has not installed a package or earned completion.
"$VENV_PYTHON" -I -c 'import pathlib, sys; assert sys.prefix != sys.base_prefix; assert pathlib.Path(sys.prefix).resolve() == pathlib.Path(sys.argv[1]).resolve()' "$VENV_DIR"
construction_rc=0
_runtime_construction_finish || construction_rc=$?
[[ "$construction_rc" == 1 ]] || {{ echo 'Incomplete construction did not abort' >&2; exit 1; }}
[[ ! -e "$VENV_DIR" ]] || {{ echo 'Aborted candidate remained' >&2; exit 1; }}
"""
        )
    else:
        python_relative = "Scripts/python.exe" if os.name == "nt" else "bin/python"
        script = (
            _prelude(ext)
            + _function(ext, "Test-PythonVenv")
            + _function(ext, "Deploy-Venv")
            + f"""
$env:OS = 'Test'
$VenvDir = '{slot}'
$VenvPython = Join-Path $VenvDir '{python_relative}'
$UvCommand = '{uv}'
function Assert-Uv {{}}
function Invoke-VersionedSlotClean {{ return $true }}
if (-not (Deploy-Venv)) {{ exit 1 }}
if ($ErrorActionPreference -ne 'Stop') {{ throw 'preference leaked' }}
"""
        )
    result = _run(ext, script, tmp_path, env)
    log = tmp_path / "uv.jsonl"
    assert log.exists(), result.stdout + result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert bool(result.returncode) == (mode == "fatal"), result.stdout + result.stderr
    assert calls[0] == ["venv", str(slot), "--python", "3.11", "--allow-existing"]
    assert len(calls) == (2 if mode in ("fatal", "fallback") else 3)
    if mode in ("fallback", "fatal"):
        assert calls[-1] == ["venv", str(slot), "--allow-existing"]
    elif ext == "ps1":
        assert "retrying" in result.stderr
    if ext == "sh":
        assert not slot.exists()
        assert not (root / "current-version").exists()
        assert not (root / "last-known-good").exists()


def _attributed_library_sources(tmp_path: Path, layout: str):
    sys.path.insert(0, str(REPO / "tools"))
    try:
        import materialize_main as mm
    finally:
        sys.path.pop(0)
    repository = tmp_path / "publisher-repo"
    payload = repository / "plugins" / PLUGIN.name
    shutil.copytree(PLUGIN, payload, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    logs = mm.materialize_uv_editable_refs(repository, canonical_root=REPO)
    skipped = [line for line in logs if line.startswith("SKIP")]
    assert not skipped, "\n".join(skipped)
    # Ordering/retry tests need the real attributed metadata graph, not the
    # product's whole source corpus (artifact tests exercise that separately).
    for manifest_path in list(payload.rglob("pyproject.toml")):
        project = manifest_path.parent
        for directory in project.iterdir():
            if directory.is_dir() and directory.name not in ("libs", "scripts"):
                shutil.rmtree(directory)
        scripts = project / "scripts"
        if scripts.is_dir():
            for member in scripts.iterdir():
                if member.name in (
                    "install.sh", "install.ps1",
                    "installer-engine.sh", "installer-engine.ps1",
                    "installer_dependency_policy.py", "installer_dependency_config.py",
                ):
                    continue
                if member.is_dir():
                    shutil.rmtree(member)
                else:
                    member.unlink()
        code = project / "src"
        code.mkdir()
        (code / "fixture_source.py").write_text("value = 'retained-source-contract'\n")
    manifest = json.loads((payload / "plugin.json").read_text(encoding="utf-8"))
    standalone = repository / "snapshots" / manifest["version"]
    if layout == "local":
        shutil.copytree(payload, standalone)
        logs = mm.materialize_installer_engine_ref_into(
            source_consumer_dir=PLUGIN,
            dest_consumer_dir=standalone,
            canonical_root=REPO,
            dest_root=repository,
        )
        skipped = [line for line in logs if line.startswith("SKIP")]
        assert not skipped, "\n".join(skipped)
    shutil.copytree(payload / "libs", repository / "libs")
    shutil.rmtree(payload / "libs")
    shutil.copyfile(PLUGIN / "pyproject.toml", payload / "pyproject.toml")
    engine = repository / "libs" / "installer-engine"
    engine.mkdir(parents=True, exist_ok=True)
    for name in (
        "installer-engine.sh", "installer-engine.ps1",
        "installer_dependency_policy.py", "installer_dependency_config.py",
    ):
        shutil.copyfile(ENGINE / name, engine / name)
    roster = repository / ".github" / "plugin" / "marketplace.json"
    roster.parent.mkdir(parents=True)
    roster.write_text(
        json.dumps({
            "name": "copilot-extensions",
            "plugins": [{
                "name": manifest["name"],
                "version": manifest["version"],
                "source": f"plugins/{PLUGIN.name}",
            }],
        }),
        encoding="utf-8",
    )
    if layout == "local":
        payload, engine = standalone, standalone / "scripts"
    project = tomllib.loads((payload / "pyproject.toml").read_text(encoding="utf-8"))
    sources = [
        (payload / project["tool"]["uv"]["sources"][name]["path"]).resolve()
        for name in PACKAGES[:-1]
    ] + [payload]
    assert all(source.is_relative_to(repository) for source in sources)
    return engine, sources


@pytest.fixture(scope="module")
def attributed_library_sources(tmp_path_factory):
    return {
        layout: _attributed_library_sources(
            tmp_path_factory.mktemp(f"codespaces-attributed-{layout}"), layout,
        )
        for layout in ("local", "canonical")
    }


@pytest.mark.parametrize("ext", ["sh", "ps1"])
@pytest.mark.parametrize("editable", [False, True])
@pytest.mark.parametrize("mode", ["sre", "fatal"])
def test_codespaces_library_install_order_retry_and_errors(
    tmp_path, ext, editable, mode, attributed_library_sources,
):
    if ext == "ps1" and PWSH is None:
        pytest.skip("pwsh unavailable")
    if ext == "sh" and os.name == "nt":
        pytest.skip("POSIX harness")
    env = _env(tmp_path)
    uv = _fake_uv(tmp_path)
    env["TEST_UV_MODE"] = mode
    engine, sources = attributed_library_sources["canonical" if editable else "local"]
    env["TEST_UV_EXPECTED_SOURCES"] = json.dumps({
        tomllib.loads((source / "pyproject.toml").read_text())["project"]["name"]: str(source)
        for source in sources
    })
    python = Path(sys._base_executable)
    if ext == "sh":
        script = _prelude(ext, engine) + _function(ext, "_install_package_into")
        script += f"\nUV_CMD='{uv}'\n"
        script += "".join(f"{var}='{path}'\n" for var, path in zip(VARIABLES, sources))
        script += f"_install_package_into '{python}'" + (" --editable\n" if editable else "\n")
    else:
        script = _prelude(ext, engine) + _function(ext, "Install-PackageInto")
        script += (
            f"\n$UvCommand='{uv}'\nfunction Remove-ConsoleTrampolines {{ param($VenvDir) }}\n"
        )
        script += "".join(f"${var}='{path}'\n" for var, path in zip(PS_VARIABLES, sources))
        script += f"if (-not (Install-PackageInto -Python '{python}'"
        script += " -Editable" if editable else ""
        script += ")) { exit 1 }\n"
    result = _run(ext, script, tmp_path, env)
    log = tmp_path / "uv.jsonl"
    assert log.exists(), result.stdout + result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert bool(result.returncode) == (mode == "fatal"), result.stdout + result.stderr
    if mode == "fatal":
        assert len(calls) == 1
        assert "permanent failure" in result.stderr
        assert PACKAGES[0] in result.stderr
        mode_args = ["--editable"] if editable else ["--reinstall-package", PACKAGES[0]]
        assert _without_policy_arguments(calls[0]) == [
            "pip", "install", "--python", str(python), *mode_args, str(sources[0]), "--quiet",
        ]
        if ext == "ps1":
            assert "(exit 23)" in result.stderr
        return
    assert len(calls) == len(PACKAGES) + 2
    calls = [_without_policy_arguments(args) for args in calls]
    offset = 0
    for index, (package, source) in enumerate(zip(PACKAGES, sources)):
        args = calls[offset]
        assert args[:4] == ["pip", "install", "--python", str(python)]
        attempts = 3 if index == 0 else 1
        assert calls[offset : offset + attempts] == [args] * attempts
        assert args[-2:] == [str(source), "--quiet"]
        assert args[4:-2] == (["--editable"] if editable else ["--reinstall-package", package])
        offset += attempts


def test_codespaces_canonical_reference_registration_and_release_materialization(tmp_path):
    sys.path.insert(0, str(REPO / "tools"))
    try:
        import installer_engine_ref as ier
        import materialize_main as mm
    finally:
        sys.path.pop(0)
    assert "agent-codespaces" in ier.ADOPTERS
    for ext in ("sh", "ps1"):
        ref = ier.find_engine_ref(PLUGIN / "scripts" / f"install.{ext}", ext)
        assert ref is not None and ier.is_canonical_ref(ref, PLUGIN)
        assert not (PLUGIN / "scripts" / ref.file_name).exists()
    payload = tmp_path / "release" / "plugins" / PLUGIN.name
    shutil.copytree(PLUGIN, payload, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    logs = mm.materialize_uv_editable_refs(tmp_path / "release", canonical_root=REPO)
    logs += mm.materialize_installer_engine_refs(tmp_path / "release", canonical_root=REPO)
    skipped = [line for line in logs if line.startswith("SKIP")]
    assert not skipped, "\n".join(skipped)
    for ext in ("sh", "ps1"):
        ref = ier.find_engine_ref(payload / "scripts" / f"install.{ext}", ext)
        assert ref is not None and ier.is_local_ref(ref, payload)
        assert ref.local_path.read_bytes() == (ENGINE / ref.file_name).read_bytes()
    project = tomllib.loads((payload / "pyproject.toml").read_text())
    for spec in project["tool"]["uv"]["sources"].values():
        assert (payload / spec["path"]).resolve().is_relative_to(payload)
        assert (payload / spec["path"] / "pyproject.toml").is_file()
        assert not spec.get("editable", False)


@pytest.fixture(scope="module", params=[False, True])
def codespaces_stamped_snapshot(tmp_path_factory, request):
    if PWSH is None:
        pytest.skip("pwsh unavailable")
    tmp_path = tmp_path_factory.mktemp("codespaces-stamped-snapshot")
    materialized = request.param
    sys.path.insert(0, str(REPO / "tools"))
    try:
        import materialize_main as mm
    finally:
        sys.path.pop(0)
    stage = tmp_path / "source"
    payload = stage / "plugins" / PLUGIN.name
    shutil.copytree(PLUGIN, payload, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    if materialized:
        logs = mm.materialize_uv_editable_refs(stage, canonical_root=REPO)
        logs += mm.materialize_installer_engine_refs(stage, canonical_root=REPO)
        skipped = [line for line in logs if line.startswith("SKIP")]
        assert not skipped, "\n".join(skipped)
    else:
        for lib in (
            "installer-engine",
            *(
                Path(spec["path"]).name
                for spec in tomllib.loads((PLUGIN / "pyproject.toml").read_text())["tool"]["uv"][
                    "sources"
                ].values()
            ),
        ):
            shutil.copytree(REPO / "libs" / lib, stage / "libs" / lib)
    env = _env(tmp_path)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _fake_uv(fake_bin)
    if os.name == "nt":
        (fake_bin / "py.cmd").write_text(
            f'@echo {sys._base_executable}\n@exit /b 0\n', encoding="utf-8",
        )
    else:
        (fake_bin / "python3").symlink_to(sys._base_executable)
    isolate_installer_environment(env, tmp_path / "home", fake_bin, PWSH)
    env["TEST_UV_MODE"] = "fatal"
    guard_installer_payload(payload)
    root = tmp_path / "home" / ".agent-codespaces"
    stamp = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(payload / "scripts" / "install.ps1"),
            "stamp",
            "-InstallDir",
            str(root),
        ],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert stamp.returncode == 0, stamp.stdout + stamp.stderr
    snapshot = Path((root / "payload-dir").read_text().strip())
    for ext in ("sh", "ps1"):
        assert (snapshot / "scripts" / f"installer-engine.{ext}").read_bytes() == (
            ENGINE / f"installer-engine.{ext}"
        ).read_bytes()
        text = (snapshot / "scripts" / f"install.{ext}").read_text()
        expected = (
            '. "$SCRIPT_DIR/installer-engine.sh"'
            if ext == "sh"
            else (". (Join-Path $PSScriptRoot 'installer-engine.ps1')")
        )
        assert expected in text.splitlines()
    for name in (
        "versioned_runtime.py",
        "versioned_runtime_leases.py",
        "versioned_runtime_construction.py",
    ):
        assert (snapshot / "scripts" / name).read_bytes() == (PLUGIN / "scripts" / name).read_bytes()
    for name in ("installer_dependency_policy.py", "installer_dependency_config.py"):
        assert (snapshot / "scripts" / name).read_bytes() == (ENGINE / name).read_bytes()
    project = tomllib.loads((snapshot / "pyproject.toml").read_text())
    for spec in project["tool"]["uv"]["sources"].values():
        library = snapshot / spec["path"]
        assert library.resolve().is_relative_to(snapshot)
        assert (library / "pyproject.toml").is_file()
        assert not spec.get("editable", False)
        nested = tomllib.loads((library / "pyproject.toml").read_text())
        for dependency in nested.get("tool", {}).get("uv", {}).get("sources", {}).values():
            assert (library / dependency["path"]).resolve().is_relative_to(snapshot)
            assert (library / dependency["path"] / "pyproject.toml").is_file()
            assert not dependency.get("editable", False)
    stage.rename(tmp_path / "unavailable-source")
    assert not (root / "current-version").exists()
    assert not (tmp_path / "uv.jsonl").exists()
    assert_installer_isolated(fake_bin)
    return tmp_path, root, snapshot, fake_bin, env


def test_codespaces_stamp_snapshot_is_standalone_status(codespaces_stamped_snapshot):
    tmp_path, root, snapshot, fake_bin, env = codespaces_stamped_snapshot
    status = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(snapshot / "scripts" / "install.ps1"),
            "status",
            "-InstallDir",
            str(root),
        ],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    # A stamped payload deliberately has no runtime yet.
    assert status.returncode != 0
    assert "Venv missing" in status.stdout
    if os.name != "nt":
        status_sh = subprocess.run(
            [
                "bash",
                str(snapshot / "scripts" / "install.sh"),
                "status",
                "--install-dir",
                str(root),
            ],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert status_sh.returncode == 0, status_sh.stdout + status_sh.stderr
    assert_installer_isolated(fake_bin)


def test_codespaces_stamp_snapshot_is_standalone(codespaces_stamped_snapshot):
    tmp_path, root, _snapshot, fake_bin, env = codespaces_stamped_snapshot
    first_use = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(tmp_path / "home" / ".local" / "bin" / "agent-codespaces.ps1"),
            "version",
        ],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert first_use.returncode != 0
    assert "permanent failure" in first_use.stdout + first_use.stderr, (
        first_use.stdout + first_use.stderr
    )
    assert "provisioning failed" in first_use.stdout + first_use.stderr
    calls = [json.loads(line) for line in (tmp_path / "uv.jsonl").read_text().splitlines()]
    assert calls[0] == ["--version"], first_use.stdout + first_use.stderr
    assert any(call[0] == "venv" for call in calls)
    assert not (root / "current-version").exists()
    assert not list((root / "versions").glob("*/.install-complete.json"))
    assert_installer_isolated(fake_bin)


@pytest.mark.skipif(PWSH is None, reason="pwsh unavailable")
@pytest.mark.parametrize("usable", [False, True])
@pytest.mark.parametrize("signed_rc", [0, 23])
def test_codespaces_signed_venv_recovery_keeps_usable_nonzero_result(tmp_path, usable, signed_rc):
    env = _env(tmp_path)
    uv = _fake_uv(tmp_path)
    slot = tmp_path / "slot"
    slot.mkdir()
    ownership = slot / ".runtime-slot-ownership.json"
    ownership.write_text("reserved ownership", encoding="utf-8")
    if os.name == "nt":
        driver = tmp_path / "signed-python.py"
        driver.write_text(
            "import sys\nfrom pathlib import Path\n"
            "root = Path(sys.argv[-1])\n"
            "(root / 'Scripts').mkdir(parents=True)\n"
            "(root / 'Scripts' / 'python.exe').touch()\n"
            "(root / 'pyvenv.cfg').touch()\n"
            f"raise SystemExit({signed_rc})\n",
            encoding="utf-8",
        )
        signed = tmp_path / "signed-python.cmd"
        signed.write_text(
            f'@"{sys.executable}" "{driver}" %*\n@exit /b %ERRORLEVEL%\n',
            encoding="utf-8",
        )
    else:
        signed = tmp_path / "signed-python"
        signed.write_text(
            '#!/bin/sh\nmkdir -p "$4/Scripts"\ntouch "$4/Scripts/python.exe" "$4/pyvenv.cfg"\n'
            f"exit {signed_rc}\n",
            encoding="utf-8",
        )
        signed.chmod(0o755)
    # Signed invocation is: -m venv --copies <slot>.
    script = (
        _prelude("ps1")
        + _function("ps1", "Deploy-Venv")
        + f"""
$env:OS = 'Windows_NT'
$VenvDir = '{slot}'
$VenvPython = Join-Path $VenvDir 'Scripts/python.exe'
$UvCommand = '{uv}'
function py {{ param($Version, $Flag, $Code) $global:LASTEXITCODE = 0; return '{signed}' }}
function Get-AuthenticodeSignature {{ param($Path) return @{{ Status = 'Valid' }} }}
function Assert-Uv {{}}
function Invoke-VersionedSlotClean {{ return $true }}
function Test-PythonVenv {{
    param($Dir, $Python)
    if (Test-Path $env:TEST_UV_LOG) {{ return $true }}
    return ${str(usable).lower()}
}}
$ok = Deploy-Venv
if ($ErrorActionPreference -ne 'Stop') {{ throw 'preference leaked' }}
if (-not $ok) {{ exit 1 }}
"""
    )
    result = _run("ps1", script, tmp_path, env)
    log = tmp_path / "uv.jsonl"
    if usable:
        assert result.returncode == 0, result.stdout + result.stderr
        assert not log.exists()
        if signed_rc:
            assert "after producing a usable venv" in result.stderr
    else:
        assert result.returncode != 0, result.stdout + result.stderr
        assert not log.exists()
        assert "construction aborted" in result.stderr
    assert ownership.read_text(encoding="utf-8") == "reserved ownership"


@pytest.mark.parametrize("ext", ["sh", "ps1"])
@pytest.mark.parametrize("marketplace", [False, True])
def test_codespaces_manifest_shared_writer_preserves_slot_and_source(tmp_path, ext, marketplace):
    if ext == "ps1" and PWSH is None:
        pytest.skip("pwsh unavailable")
    if ext == "sh" and os.name == "nt":
        pytest.skip("POSIX harness")
    env = _env(tmp_path)
    root = tmp_path / "runtime"
    root.mkdir()
    if marketplace:
        env["COPILOT_PLUGIN_STAGED_FROM"] = (
            "/home/example/.copilot/installed-plugins/mkt/agent-codespaces"
        )
    if ext == "sh":
        script = _prelude(ext) + _function(ext, "_source_kind")
        script += (
            _function(ext, "_write_codespaces_deploy_manifest")
            + f"""
INSTALL_DIR='{root}'
PLUGIN_DIR='{PLUGIN}'
VENV_DIR='{root}/versions/test'
_git_info() {{ echo 'abc dev true'; }}
_write_codespaces_deploy_manifest
"""
        )
    else:
        script = _prelude(ext) + _function(ext, "Get-SourceKind")
        script += (
            _function(ext, "Write-CodespacesDeployManifest")
            + f"""
$InstallDir='{root}'
$PluginDir='{PLUGIN}'
$RepoRoot='original-root'
$VenvDir='{root}/versions/build'
$LinkDir='{root}/versions/selected'
function Get-GitInfo {{ param($Path)
    if ($Path -ne 'original-root') {{ throw 'lost repository root' }}
    return @{{ commit = 'abc'; branch = 'dev'; dirty = $true }}
}}
Write-CodespacesDeployManifest
"""
        )
    result = _run(ext, script, tmp_path, env)
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads((root / "deploy-manifest.json").read_text(encoding="utf-8-sig"))
    assert manifest["schema_version"] == 3
    assert manifest["service"] == manifest["source"]["plugin"] == "agent-codespaces"
    assert manifest["runtime"] == "python"
    assert manifest["venv"] == f"{root.as_posix()}/versions/" + (
        "test" if ext == "sh" else "selected"
    )
    assert manifest["source"]["kind"] == ("marketplace" if marketplace else "local")
    assert (
        manifest["source"]["version"]
        == tomllib.loads((PLUGIN / "pyproject.toml").read_text())["project"]["version"]
    )
    assert manifest["source"]["commit"] == (None if marketplace else "abc")
    assert manifest["source"]["dirty"] == (not marketplace)
    assert not (root / "deploy-manifest.json.tmp").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX owning-payload stamp")
def test_codespaces_materialized_posix_stamp_and_first_use_are_local(tmp_path):
    sys.path.insert(0, str(REPO / "tools"))
    try:
        import materialize_main as mm
    finally:
        sys.path.pop(0)
    payload = tmp_path / "release" / "plugins" / PLUGIN.name
    shutil.copytree(PLUGIN, payload, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    logs = mm.materialize_uv_editable_refs(tmp_path / "release", canonical_root=REPO)
    logs += mm.materialize_installer_engine_refs(tmp_path / "release", canonical_root=REPO)
    assert not any(line.startswith("SKIP") for line in logs), logs
    env = _env(tmp_path)
    uv = _fake_uv(tmp_path)
    env["PATH"] = str(uv.parent) + os.pathsep + env["PATH"]
    env["TEST_UV_MODE"] = "fatal"
    stamp = subprocess.run(
        ["bash", str(payload / "scripts" / "install.sh"), "stamp"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert stamp.returncode == 0, stamp.stdout + stamp.stderr
    root = tmp_path / "home" / ".agent-codespaces"
    assert (root / "payload-dir").read_text().strip() == str(payload)
    assert not (tmp_path / "uv.jsonl").exists()
    first_use = subprocess.run(
        ["bash", str(tmp_path / "home" / ".local" / "bin" / "agent-codespaces"), "version"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert first_use.returncode != 0
    assert "permanent failure" in first_use.stderr
    assert "provisioning FAILED" in first_use.stderr


@pytest.mark.parametrize("ext", ["sh", "ps1"])
def test_codespaces_dev_refuses_conflicting_claim_before_venv_writes(tmp_path, ext):
    if ext == "ps1" and PWSH is None:
        pytest.skip("pwsh unavailable")
    if ext == "sh" and os.name == "nt":
        pytest.skip("POSIX harness")
    env = _env(tmp_path)
    env["TEST_UV_MODE"] = "missing-cfg"
    uv = _fake_uv(tmp_path)
    root = tmp_path / "runtime"
    if ext == "sh":
        script = (
            _prelude(ext)
            + _function(ext, "do_dev")
            + f"""
INSTALL_DIR='{root}'
LOCAL_BIN='{tmp_path}/bin'
SRC_VERSION='test'
SERVICE_NAME='Codespaces'
UV_CMD='{uv}'
_header() {{ :; }}
_deploy_versioned_runtime_helper() {{ :; }}
_resolve_dev_slot_owner() {{ echo 'owner'; }}
_assert_uv() {{ :; }}
_ensure_uv_index() {{ :; }}
_bootstrap_python() {{ printf '%s\\n' '{sys._base_executable}'; }}
_run_versioned_runtime() {{ return 2; }}
do_dev
"""
        )
    else:
        script = (
            _prelude(ext)
            + _function(ext, "Invoke-Dev")
            + f"""
$InstallDir='{root}'
$LocalBin='{tmp_path}/bin'
$VersionedRuntime=$true
$ServiceName='Codespaces'
$Force=$false
$UvCommand='{uv}'
function Write-ServiceHeader {{ param($Msg) }}
function Deploy-VersionedRuntimeHelper {{}}
function Resolve-DevSlotOwner {{ return 'owner' }}
function Assert-Uv {{}}
function Test-PythonVenv {{ param($Dir, $Python) return Test-Path (Join-Path $Dir 'pyvenv.cfg') }}
function Invoke-VersionedRuntime {{ param($Arguments)
    return @{{ ExitCode=2; Output='claim stopped' }}
}}
if (-not (Invoke-Dev)) {{ exit 1 }}
"""
        )
    result = _run(ext, script, tmp_path, env)
    assert result.returncode != 0
    assert not (tmp_path / "uv.jsonl").exists()
    assert not (root / "versions").exists()


@pytest.mark.parametrize("ext", ["sh", "ps1"])
def test_native_dev_generation_dependency_failure_preserves_working_runtime(tmp_path, ext):
    if ext == "sh" and os.name == "nt":
        pytest.skip("native POSIX shell is unavailable")
    if ext == "ps1" and PWSH is None:
        pytest.skip("PowerShell is unavailable")
    root = tmp_path / "runtime"
    env = _env(tmp_path)
    base, helper = Path(sys._base_executable), PLUGIN / "scripts/versioned_runtime.py"
    for name in ("versioned_runtime_leases.py", "versioned_runtime_construction.py", "versioned_runtime.py"):
        shutil.copyfile(helper.with_name(name), tmp_path / name)
    if ext == "ps1":
        # The extracted Windows entry point must consume the real host venv layout.
        dev_function = _function(ext, "Invoke-Dev")
        if os.name != "nt":
            assert dev_function.count("'Scripts\\python.exe'") == 1
            dev_function = dev_function.replace("'Scripts\\python.exe'", "'bin/python'")
        script = (
            _prelude(ext)
            + _function(ext, "Invoke-VersionedRuntime")
            + _function(ext, "Deploy-VersionedRuntimeHelper")
            + _function(ext, "Test-PythonVenv")
            + _function(ext, "Test-CodespacesRuntime")
            + dev_function
            + f"""
$PSScriptRoot='{PLUGIN / "scripts"}'
$InstallDir='{root}'
$LocalBin='{tmp_path / "bin"}'
$PluginDir='{tmp_path / "editable-source"}'
$VersionedRuntime=$true
$ServiceName='Codespaces'
$Force=$false
$UvCommand='fixture'
$script:DependencyFailure=$false
function Write-ServiceHeader {{ param($Msg) }}
function Resolve-DevSlotOwner {{ 'fixture-owner' }}
function Get-BootstrapPython {{ '{base}' }}
function Get-PayloadHash {{ 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' }}
function Assert-Uv {{}}
function Deploy-SelfProvisioningBinstub {{}}
function Invoke-UvVenvResilient {{
    param($VenvDir, $Arguments, $UvCommand)
    & '{base}' -I -m venv --without-pip $VenvDir
    return @{{ ExitCode=$LASTEXITCODE; Output='' }}
}}
function Install-PackageInto {{
    param($Python, [switch]$Editable)
    if (-not $Editable) {{ throw 'Development source was not selected editable' }}
    $site = & $Python -I -c 'import sysconfig; print(sysconfig.get_path("purelib"))'
    Set-Content (Join-Path $site 'agent_codespaces.py') ''
    Set-Content (Join-Path (Split-Path (Split-Path $Python)) 'partial-dependency') 'written'
    return (-not $script:DependencyFailure)
}}
New-Item -ItemType Directory (Join-Path $InstallDir 'versions/1.0.0') -Force | Out-Null
& '{base}' -I '{helper}' --root $InstallDir mark-complete 1.0.0 | Out-Null
& '{base}' -I '{helper}' --root $InstallDir activate 1.0.0 --no-link | Out-Null
if (-not (Invoke-Dev)) {{ throw 'Initial development build failed' }}
$before = (Get-Content (Join-Path $InstallDir 'current-version') -Raw).Trim()
if ($before -notlike 'dev-*') {{ throw 'Initial development build did not activate its generation' }}
$good = Join-Path $InstallDir "versions/$before"
$completion = [IO.File]::ReadAllText((Join-Path $good '.install-complete.json'))
$prefix = [IO.File]::ReadAllText((Join-Path $good 'pyvenv.cfg'))
$script:DependencyFailure=$true
$failed=$false
try {{ if (-not (Invoke-Dev)) {{ $failed=$true }} }} catch {{
    if ($_ -notmatch 'construction aborted') {{ throw }}
    $failed=$true
}}
if (-not $failed) {{ throw 'Selected dependency failure was swallowed' }}
if ((Get-Content (Join-Path $InstallDir 'current-version') -Raw).Trim() -cne $before) {{ throw 'Working development selection changed' }}
if ((Get-Content (Join-Path $InstallDir 'last-known-good') -Raw).Trim() -cne $before) {{ throw 'Last known good changed' }}
if ([IO.File]::ReadAllText((Join-Path $good '.install-complete.json')) -cne $completion) {{ throw 'Working completion changed' }}
if ([IO.File]::ReadAllText((Join-Path $good 'pyvenv.cfg')) -cne $prefix) {{ throw 'Working environment changed' }}
if (@(Get-ChildItem (Join-Path $InstallDir 'versions') -Directory).Count -ne 2) {{ throw 'Failed development candidate remained' }}
$script:DependencyFailure=$false
if (-not (Invoke-Dev)) {{ throw 'Development retry failed' }}
$after = (Get-Content (Join-Path $InstallDir 'current-version') -Raw).Trim()
if ($after -eq $before) {{ throw 'Retry rewrote its predecessor' }}
$claim = Get-Content (Join-Path $InstallDir 'dev-claim.json') -Raw | ConvertFrom-Json
if ($claim.previous_version -ne '1.0.0' -or $claim.selected_generation -ne $after) {{ throw 'Development ownership or rollback drifted' }}
& '{base}' -I '{root / "versioned_runtime.py"}' --root $InstallDir dev-release --owner fixture-owner --restore | Out-Null
if ($LASTEXITCODE -ne 0) {{ throw 'Deployed release failed' }}
if ((Get-Content (Join-Path $InstallDir 'current-version') -Raw).Trim() -ne '1.0.0') {{ throw 'Original release was not restored' }}
if (Test-Path (Join-Path $InstallDir 'dev-claim.json')) {{ throw 'Restored claim remained open' }}
"""
        )
    else:
        script = (
            _prelude(ext)
            + _function(ext, "_run_versioned_runtime")
            + _function(ext, "_deploy_versioned_runtime_helper")
            + _function(ext, "_codespaces_runtime_healthy")
            + _function(ext, "do_dev")
            + f"""
SCRIPT_DIR='{PLUGIN / "scripts"}'
INSTALL_DIR='{root}'
LOCAL_BIN='{tmp_path / "bin"}'
PLUGIN_DIR='{tmp_path / "editable-source"}'
SRC_VERSION=test
SERVICE_NAME=Codespaces
UV_CMD=fixture
dependency_failure=0
_header() {{ :; }}
_resolve_dev_slot_owner() {{ printf '%s\\n' fixture-owner; }}
_bootstrap_python() {{ printf '%s\\n' '{base}'; }}
_payload_hash() {{ printf '%s' aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; }}
_assert_uv() {{ :; }}
_ensure_uv_index() {{ :; }}
deploy_binstub() {{ :; }}
invoke_uv_venv_resilient() {{ '{base}' -I -m venv --without-pip "$2"; }}
_install_package_into() {{
    [[ "$2" == --editable ]] || return 91
    site="$("$1" -I -c 'import sysconfig; print(sysconfig.get_path("purelib"))')" || return 1
    printf '\\n' > "$site/agent_codespaces.py"
    printf written > "$(dirname "$(dirname "$1")")/partial-dependency"
    [[ "$dependency_failure" == 0 ]]
}}
mkdir -p "$INSTALL_DIR/versions/1.0.0"
'{base}' -I '{helper}' --root "$INSTALL_DIR" mark-complete 1.0.0 >/dev/null
'{base}' -I '{helper}' --root "$INSTALL_DIR" activate 1.0.0 --no-link >/dev/null
do_dev || exit 90
before="$(cat "$INSTALL_DIR/current-version")"
[[ "$before" == dev-* ]] || {{ echo 'Initial development build did not activate its generation' >&2; exit 103; }}
good="$INSTALL_DIR/versions/$before"
completion="$(cat "$good/.install-complete.json")"
prefix="$(cat "$good/pyvenv.cfg")"
dependency_failure=1
if do_dev; then echo 'Selected dependency failure was swallowed' >&2; exit 92; fi
[[ "$(cat "$INSTALL_DIR/current-version")" == "$before" ]] || exit 93
[[ "$(cat "$INSTALL_DIR/last-known-good")" == "$before" ]] || exit 94
[[ "$(cat "$good/.install-complete.json")" == "$completion" ]] || exit 95
[[ "$(cat "$good/pyvenv.cfg")" == "$prefix" ]] || exit 96
[[ "$(find "$INSTALL_DIR/versions" -mindepth 1 -maxdepth 1 -type d | wc -l)" == 2 ]] || exit 97
dependency_failure=0
do_dev || exit 98
after="$(cat "$INSTALL_DIR/current-version")"
[[ "$after" != "$before" ]] || exit 99
'{base}' -I -c 'import json,sys; c=json.load(open(sys.argv[1])); assert c["previous_version"]=="1.0.0" and c["selected_generation"]==sys.argv[2]' "$INSTALL_DIR/dev-claim.json" "$after"
'{base}' -I "$INSTALL_DIR/versioned_runtime.py" --root "$INSTALL_DIR" dev-release --owner fixture-owner --restore >/dev/null || exit 100
[[ "$(cat "$INSTALL_DIR/current-version")" == 1.0.0 ]] || exit 101
[[ ! -e "$INSTALL_DIR/dev-claim.json" ]] || exit 102
"""
        )
    result = _run(ext, script, tmp_path, env)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX uv tool PATH")
def test_codespaces_assert_uv_keeps_cached_tool_on_caller_path(tmp_path):
    env = _env(tmp_path)
    uv = _fake_uv(tmp_path)
    root = tmp_path / "runtime"
    tool = root / "tool"
    tool.mkdir(parents=True)
    shutil.copy2(uv, tool / "uv")
    env["PATH"] = "/usr/bin:/bin"
    script = (
        _prelude("sh")
        + _function("sh", "_assert_uv")
        + f"""
INSTALL_DIR='{root}'
_assert_uv
[[ "$UV_CMD" == '{tool}/uv' ]]
[[ "$(command -v uv)" == "$UV_CMD" ]]
uv --version
"""
    )
    result = _run("sh", script, tmp_path, env)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in (tmp_path / "uv.jsonl").read_text().splitlines()]
    assert calls == [["--version"], ["--version"]]


@pytest.mark.skipif(PWSH is None, reason="pwsh unavailable")
@pytest.mark.parametrize("available", [False, True])
def test_codespaces_assert_uv_uses_shared_acquisition_and_propagates_failure(tmp_path, available):
    env = _env(tmp_path)
    script = (
        _prelude("ps1")
        + _function("ps1", "Assert-Uv")
        + f"""
$InstallDir='{tmp_path}/runtime'
function Ensure-Uv {{
    param($InstallRoot)
    if ($InstallRoot -ne $InstallDir) {{ throw 'wrong tool root' }}
    if (${str(available).lower()}) {{ return 'resolved-uv' }}
    return $null
}}
Assert-Uv
if ($script:UvCommand -ne 'resolved-uv') {{ throw 'lost uv command' }}
"""
    )
    result = _run("ps1", script, tmp_path, env)
    assert bool(result.returncode) == (not available)
    if not available:
        assert "uv is required but acquisition failed" in result.stderr


def test_installer_engine_adoption_environment_excludes_host_tools(tmp_path):
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    env = {
        "PATH": "host-uv-and-gh",
        "PYTHONHOME": "host-python",
        "PYTHONPATH": "host-package",
        "VIRTUAL_ENV": "host-venv",
        "COPILOT_EXTENSIONS_CONTEXT": "host-context",
    }
    home = tmp_path / "home"
    isolate_installer_environment(env, home, fake_bin, PWSH or "unused-pwsh")
    assert env["PATH"] == str(fake_bin)
    assert not any(key in env for key in (
        "PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "COPILOT_EXTENSIONS_CONTEXT",
    ))
    for key in ("HOME", "USERPROFILE", "APPDATA", "COPILOT_HOME", "TEMP"):
        assert Path(env[key]).is_relative_to(home)
    assert env["COPILOT_EXTENSIONS_TEST_CONTAINED"] == "1"


@pytest.mark.skipif(PWSH is None, reason="pwsh unavailable")
@pytest.mark.parametrize(
    ("command", "effect"),
    [
        ("Register-ScheduledTask", "register scheduled task"),
        ("Start-ScheduledTask", "start scheduled task"),
        ("Start-Process -FilePath 'host.exe' -ArgumentList 'owner'", "start host process"),
        ("Start-Process -FilePath (Get-Process -Id $PID).Path -ArgumentList '-File', $PSCommandPath",
         "start host process"),
        ("""Start-Process -FilePath (Get-Process -Id $PID).Path -ArgumentList ('"-NoProfile" "-ExecutionPolicy" "Bypass" "-File" "' + $PSCommandPath + '"')""",
         "start host process"),
    ],
)
def test_installer_engine_adoption_guards_reject_host_effects(tmp_path, command, effect):
    env = _env(tmp_path)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _fake_uv(fake_bin)
    isolate_installer_environment(env, tmp_path / "home", fake_bin, PWSH)
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    installer = scripts / "install.ps1"
    installer.write_text(
        "Set-StrictMode -Version Latest\n$ErrorActionPreference = 'Stop'\n"
        "foreach ($name in 'gh', 'py', 'ssh', 'schtasks.exe', 'conhost.exe') {\n"
        "    if (Get-Command $name -ErrorAction SilentlyContinue) { throw \"Host tool: $name\" }\n"
        "}\n" + command + "\n",
        encoding="utf-8",
    )
    guard_installer_payload(payload)
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(installer)],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert f"Forbidden installer test effect: {effect}" in result.stdout + result.stderr
    assert (fake_bin / "forbidden-effects").read_text().splitlines() == [effect]


@pytest.mark.skipif(os.name != "nt" or PWSH is None, reason="native Windows uv resolver")
@pytest.mark.parametrize("version_exit", [0, 31])
def test_installer_engine_adoption_native_fake_uv_selection_is_fail_closed(tmp_path, version_exit):
    env = _env(tmp_path)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    uv = _fake_uv(fake_bin)
    isolate_installer_environment(env, tmp_path / "home", fake_bin, PWSH)
    env["TEST_UV_VERSION_EXIT"] = str(version_exit)
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    installer = scripts / "install.ps1"
    installer.write_text(
        "Set-StrictMode -Version Latest\n" + _prelude("ps1")
        + "function Write-Fail { param($Msg) Write-Host $Msg }\n"
        + f"$resolved = Ensure-Uv -InstallRoot '{tmp_path / 'runtime'}'\n"
        + f"if ($resolved -ne '{uv}') {{ exit 1 }}\n",
        encoding="utf-8",
    )
    guard_installer_payload(payload)
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(installer)],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=30, check=False,
    )
    calls = [json.loads(line) for line in (tmp_path / "uv.jsonl").read_text().splitlines()]
    assert calls == [["--version"]], result.stdout + result.stderr
    assert bool(result.returncode) == bool(version_exit), result.stdout + result.stderr
    if version_exit:
        assert "Forbidden installer test effect: uv bootstrap" in result.stdout + result.stderr
        assert (fake_bin / "forbidden-effects").read_text().splitlines() == ["uv bootstrap"]
        assert not list(tmp_path.rglob("tool/uv.exe"))
    else:
        assert_installer_isolated(fake_bin)
