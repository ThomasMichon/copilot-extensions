"""Dispatch contracts in canonical source and real materialized release layouts.

Each snapshot identity item owns at most two real stamps within its 30-second budget.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import installer_engine_ref as ier
import materialize_main as mm

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]
ENGINE = REPO / "libs" / "installer-engine"
PWSH = shutil.which("pwsh") or shutil.which("powershell")
LIBRARIES = {
    "agent-zdd": "zdd",
    "agent-procutil": "agent-procutil",
    "agent-dropin-registry": "dropin-registry",
    "agent-plugin-resolve": "plugin-resolve",
    "agent-single-instance-lease": "single-instance-lease",
    "agent-plugin-activation": "plugin-activation",
}


def _stage_owned_source(
    plugin: Path, libraries: dict[str, Path], *, repository: Path | None = None,
) -> Path:
    """Stage publisher-owned fixture sources at the real helper-owner boundary."""
    plugin.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
    (plugin / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    project = (
        f'[project]\nname = "{manifest["name"]}"\nversion = "{manifest["version"]}"\n'
        f"dependencies = {json.dumps(list(libraries))}\n"
        '[project.optional-dependencies]\nmcp = []\n[tool.uv.sources]\n'
    )
    for package, source in libraries.items():
        source.mkdir(parents=True, exist_ok=True)
        relative = Path(os.path.relpath(source, plugin)).as_posix()
        project += f'{package} = {{ path = "{relative}" }}\n'
        library_project = f'[project]\nname = "{package}"\nversion = "1.0.0"\n'
        if package == "agent-plugin-activation" and "agent-dropin-registry" in libraries:
            dependency = libraries["agent-dropin-registry"]
            relative_dependency = Path(os.path.relpath(dependency, source)).as_posix()
            library_project += (
                'dependencies = ["agent-dropin-registry"]\n[tool.uv.sources]\n'
                f'agent-dropin-registry = {{ path = "{relative_dependency}" }}\n'
            )
        (source / "pyproject.toml").write_text(library_project, encoding="utf-8")
    (plugin / "pyproject.toml").write_text(project, encoding="utf-8")
    if repository is not None:
        assert plugin == repository / "plugins" / "agent-dispatch"
        roster = repository / ".github" / "plugin" / "marketplace.json"
        roster.parent.mkdir(parents=True, exist_ok=True)
        roster.write_text(
            json.dumps({
                "name": "copilot-extensions",
                "plugins": [{
                    "name": manifest["name"], "version": manifest["version"],
                    "source": "plugins/agent-dispatch",
                }],
            }),
            encoding="utf-8",
        )
    scripts = plugin / "scripts"
    shutil.copytree(ENGINE, scripts, dirs_exist_ok=True)
    return scripts


def _function(ext: str, name: str) -> str:
    text = (PLUGIN / "scripts" / f"install.{ext}").read_text(encoding="utf-8")
    pattern = (
        rf"(?ms)^{re.escape(name)}\(\) \{{.*?^\}}"
        if ext == "sh"
        else rf"(?ms)^function {re.escape(name)} \{{.*?^\}}"
    )
    match = re.search(pattern, text)
    assert match, name
    return match.group(0) + "\n"


def _env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in tuple(env):
        if key.startswith(("AGENT_", "COPILOT_PLUGIN_", "PYTHON", "UV_")):
            env.pop(key)
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
    }.items():
        root = tmp_path / suffix
        root.mkdir(exist_ok=True)
        env[key] = str(root)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    if os.name != "nt":
        for name in (
            "grep",
            "dirname",
            "sed",
            "head",
            "date",
            "hostname",
            "uname",
            "tr",
            "mv",
            "cat",
            "git",
        ):
            command = shutil.which(name)
            assert command, name
            (fake_bin / name).symlink_to(command)
    env["PATH"] = str(fake_bin)
    env["OS"] = "Installer_Test"
    env["COPILOT_EXTENSIONS_TEST_CONTAINED"] = "1"
    env["TEST_UV_LOG"] = str(tmp_path / "uv.jsonl")
    return env


def _run(ext: str, script: str, tmp_path: Path, env: dict[str, str]):
    if ext == "ps1" and PWSH is None:
        pytest.skip("PowerShell unavailable")
    if ext == "sh" and os.name == "nt":
        pytest.skip("POSIX harness")
    file = tmp_path / f"harness.{ext}"
    file.write_text(script, encoding="utf-8")
    command = ["/bin/bash", str(file)] if ext == "sh" else [PWSH, "-NoProfile", "-File", str(file)]
    return subprocess.run(
        command, cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30
    )


def _prelude(ext: str, engine: Path = ENGINE) -> str:
    if ext == "sh":
        return (
            "set -euo pipefail\n"
            '_warn() { echo "$*" >&2; }\n_fail() { echo "$*" >&2; }\n'
            "_ok() { :; }\n_step() { :; }\nsleep() { :; }\n"
            f'. "{engine / "installer-engine.sh"}"\n'
        )
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "function Write-Warn { param($Msg) Write-Host $Msg }\n"
        "function Write-Fail { param($Msg) throw $Msg }\n"
        "function Write-Ok { param($Msg) }\nfunction Write-Step { param($Msg) }\n"
        "function Write-Skip { param($Msg) }\nfunction Start-Sleep { param($Seconds) }\n"
        "function Start-Process { throw 'forbidden process launch' }\n"
        "function Register-ScheduledTask { throw 'forbidden scheduled task' }\n"
        "function Stop-Process { throw 'forbidden process termination' }\n"
        f". '{engine / 'installer-engine.ps1'}'\n"
    )


def _retained_uv_fixture_prelude() -> str:
    return """
import re, tempfile, tomllib
if args[:2] == ["pip", "install"]:
    config = Path(args[args.index("--config-file") + 1]).resolve()
    private = config.parent
    assert config.is_file() and not config.is_symlink() and private.name == "inputs"
    assert private.parent.parent.samefile(Path(tempfile.gettempdir()))
    if os.name != "nt":
        assert private.stat().st_uid == os.getuid() and private.stat().st_mode & 0o077 == 0
    expected = json.loads(os.environ.get("TEST_UV_EXPECTED_SOURCES", "{}"))
    for index, argument in enumerate(args):
        match = re.fullmatch(r"(.*?)(\\[[^\\]]+\\])?", argument)
        source = Path(match[1])
        if not source.is_dir() or not (source / "pyproject.toml").is_file():
            continue
        project = tomllib.loads((source / "pyproject.toml").read_text())["project"]
        original = Path(expected[project["name"]])
        assert source.resolve() != original.resolve() and source.resolve().is_relative_to(private)
        assert project == tomllib.loads((original / "pyproject.toml").read_text())["project"]
        for member in original.rglob("*.py"):
            assert (source / member.relative_to(original)).read_bytes() == member.read_bytes()
        args[index] = str(original) + (match[2] or "")
    position = args.index("--config-file")
    del args[position:position + 2]
    if "--config-setting" in args:
        position = args.index("--config-setting")
        assert args[position + 1] == "editable_mode=compat"
        del args[position:position + 2]
"""


def _fake_uv(tmp_path: Path) -> Path:
    driver = tmp_path / "uv-driver.py"
    driver.write_text(
        """import json, os, sys
from pathlib import Path
args = sys.argv[1:]
if args[:3] == ["pip", "install", "--show-settings"]:
    print("PipInstallSettings { settings: PipSettings {\\n    index_locations: IndexLocations {\\n        flat_index: [],\\n    },\\n    exclude_newer: ExcludeNewer {\\n        global: None,\\n        package: ExcludeNewerPackage({}),\\n    },\\n}, }")
    raise SystemExit(0)
""" + _retained_uv_fixture_prelude() + """
def normalized(arguments):
    result = []
    iterator = iter(arguments)
    for argument in iterator:
        if argument == "--exclude-newer":
            next(iterator)
        elif argument != "--reinstall":
            result.append(argument)
    return result
log = Path(os.environ["TEST_UV_LOG"])
history = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
with log.open("a") as stream:
    stream.write(json.dumps(args) + "\\n")
if args == ["--version"]:
    print("uv fixture")
    raise SystemExit(0)
count = sum(normalized(item) == normalized(args) for item in history)
mode = os.environ.get("TEST_UV_MODE", "ok")
if mode == "fatal":
    print("permanent failure")
    raise SystemExit(23)
if args[0] == "venv":
    root = Path(args[1])
    root.mkdir(parents=True, exist_ok=True)
    (root / "Scripts").mkdir(exist_ok=True)
    (root / "Scripts/python.exe").touch()
    if mode == "missing-cfg" and count < 2:
        raise SystemExit(0)
    if mode == "corrupt" and count < 2:
        print("failed to locate pyvenv.cfg; exit code: 106")
        raise SystemExit(106)
    if mode == "sre" and count < 2:
        print("SRE module mismatch")
        raise SystemExit(1)
    (root / "pyvenv.cfg").write_text("home = fixture")
    raise SystemExit(0)
if args[:2] == ["pip", "install"]:
    if mode == "sre" and count < 2:
        print("SRE module mismatch")
        raise SystemExit(1)
    raise SystemExit(0)
raise SystemExit(2)
""",
        encoding="utf-8",
    )
    uv = tmp_path / "fake-bin" / ("uv.cmd" if os.name == "nt" else "uv")
    uv.write_text(
        f'@"{sys.executable}" "{driver}" %*\n@exit /b %ERRORLEVEL%\n'
        if os.name == "nt"
        else f'#!/bin/sh\nexec "{sys.executable}" "{driver}" "$@"\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)
    return uv


@pytest.mark.guard
def test_dispatch_composes_canonical_helpers_without_local_engine_copies():
    sh = (PLUGIN / "scripts/install.sh").read_text(encoding="utf-8")
    ps1 = (PLUGIN / "scripts/install.ps1").read_text(encoding="utf-8")
    layouts = set()
    for ext, text in (("sh", sh), ("ps1", ps1)):
        script = PLUGIN / "scripts" / f"install.{ext}"
        assert ier.source_match_count(script, ext) == 1
        ref = ier.find_engine_ref(script, ext)
        assert ref is not None
        if ier.is_canonical_ref(ref, PLUGIN, repo_root=REPO):
            layouts.add("canonical")
            assert not ref.local_path.exists()
        else:
            layouts.add("local")
            assert ier.is_local_ref(ref, PLUGIN)
        assert ref.resolved().read_bytes() == ier.canonical_file(
            ext, repo_root=REPO
        ).read_bytes()
        for name in ier.support_files(ENGINE):
            assert (ref.resolved().parent / name).read_bytes() == (ENGINE / name).read_bytes()
    assert len(layouts) == 1, "both language variants must select the same layout"
    assert 'invoke_uv_pip_install_resilient "$UV_CMD"' in sh
    assert 'invoke_uv_venv_resilient "$UV_CMD"' in sh
    assert "Invoke-UvPipInstallResilient -UvCommand $UvCommand" in ps1
    assert "Invoke-UvVenvResilient -VenvDir $VenvDir" in ps1
    assert 'write_deploy_manifest "agent-dispatch"' in sh
    assert "Write-DeployManifest -Service 'agent-dispatch'" in ps1


@pytest.mark.parametrize("ext", ["sh", "ps1"])
@pytest.mark.parametrize("mode", ["sre", "missing-cfg", "corrupt", "fatal"])
def test_shared_venv_retry_is_offline_and_preserves_failure(tmp_path, ext, mode):
    env = _env(tmp_path)
    uv = _fake_uv(tmp_path)
    env["TEST_UV_MODE"] = mode
    slot = tmp_path / "slot"
    if ext == "sh":
        call = f"""
if invoke_uv_venv_resilient '{uv}' '{slot}' --allow-existing; then exit 0; else exit $?; fi
"""
    else:
        call = f"""
$result = Invoke-UvVenvResilient -VenvDir '{slot}' -Arguments @('--allow-existing') -UvCommand '{uv}'
if ($result.ExitCode -eq 0 -and -not (Test-Path '{slot}/pyvenv.cfg')) {{ exit 1 }}
exit $result.ExitCode
"""
    result = _run(ext, _prelude(ext) + call, tmp_path, env)
    assert result.returncode == (23 if mode == "fatal" else 0), result.stdout + result.stderr
    calls = [json.loads(line) for line in Path(env["TEST_UV_LOG"]).read_text().splitlines()]
    assert len(calls) == (1 if mode == "fatal" else 3)
    assert all(call == ["venv", str(slot), "--allow-existing"] for call in calls)


@pytest.mark.parametrize("ext", ["sh", "ps1"])
@pytest.mark.parametrize("mode", ["sre", "fatal"])
def test_package_retry_keeps_optional_extra_and_exact_exit_code(tmp_path, ext, mode):
    env = _env(tmp_path)
    uv = _fake_uv(tmp_path)
    env["TEST_UV_MODE"] = mode
    plugin = tmp_path / "plugin"
    engine = _stage_owned_source(plugin, {})
    env["TEST_UV_EXPECTED_SOURCES"] = json.dumps({"agent-dispatch": str(plugin)})
    spec = str(plugin) + "[mcp]"
    args = ["--python", sys.executable, "--reinstall-package", "agent-dispatch", spec]
    if ext == "sh":
        call = f"""
if invoke_uv_pip_install_resilient '{uv}' --python '{sys.executable}' --reinstall-package agent-dispatch '{spec}'; then exit 0; else exit $?; fi
"""
    else:
        call = f"""
$result = Invoke-UvPipInstallResilient -UvCommand '{uv}' -Arguments @('--python', '{sys.executable}', '--reinstall-package', 'agent-dispatch', '{spec}')
Write-Host $result.Output
exit $result.ExitCode
"""
    started = datetime.now(timezone.utc)
    result = _run(ext, _prelude(ext, engine) + call, tmp_path, env)
    assert result.returncode == (23 if mode == "fatal" else 0), result.stdout + result.stderr
    calls = [json.loads(line) for line in Path(env["TEST_UV_LOG"]).read_text().splitlines()]
    assert len(calls) == (1 if mode == "fatal" else 3)
    for actual in calls:
        assert actual[:-3] == ["pip", "install", *args]
        assert actual[-3] == "--exclude-newer"
        assert actual[-1] == "--reinstall"
        cutoff = datetime.fromisoformat(actual[-2].replace("Z", "+00:00"))
        assert started - timedelta(seconds=604800) <= cutoff
        assert cutoff <= datetime.now(timezone.utc) - timedelta(seconds=604800)
    output = result.stdout + result.stderr
    assert ("permanent failure" if mode == "fatal" else "SRE module mismatch") in output
    assert "PipInstallSettings" not in output


def _signed_venv_block() -> str:
    text = (PLUGIN / "scripts/install.ps1").read_text(encoding="utf-8")
    start = "    if (-not $script:RuntimeConstructionReuse) {\n    # -- venv"
    block = start + text.split(start, 1)[1].split(
        "    # The selected MCP extra", 1
    )[0]
    return block + "}\n"


@pytest.mark.parametrize("ext", ["sh", "ps1"])
def test_shared_acquisition_selects_fixture_and_never_bootstraps(tmp_path, ext):
    env = _env(tmp_path)
    uv = _fake_uv(tmp_path)
    if ext == "sh":
        script = (
            _prelude(ext)
            + _function(ext, "_ensure_uv")
            + f"""
INSTALL_DIR='{tmp_path / "runtime"}'
_ensure_uv
[[ "$UV_CMD" == '{uv}' ]]
"""
        )
    else:
        script = (
            _prelude(ext)
            + f"""
$uv = Ensure-Uv -InstallRoot '{tmp_path / "runtime"}' -AcquireIfMissing $false
if ($uv -ne '{uv}') {{ throw 'fixture uv not selected' }}
"""
        )
    result = _run(ext, script, tmp_path, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert [json.loads(line) for line in Path(env["TEST_UV_LOG"]).read_text().splitlines()] == [
        ["--version"]
    ]
    assert not (tmp_path / "runtime").exists()


@pytest.mark.parametrize("signed_exit,usable", [(0, True), (7, True), (0, False), (7, False)])
@pytest.mark.parametrize("existing_invalid", ["none", "missing-cfg", "wrong-prefix"])
def test_signed_python_result_is_accepted_only_when_healthy(
    tmp_path, signed_exit, usable, existing_invalid
):
    env = _env(tmp_path)
    uv = _fake_uv(tmp_path)
    slot = tmp_path / "slot"
    if existing_invalid != "none":
        (slot / "Scripts").mkdir(parents=True)
        (slot / "Scripts/python.exe").write_text("invalid existing Python")
        if existing_invalid == "wrong-prefix":
            (slot / "pyvenv.cfg").write_text("wrong prefix")
    slot.mkdir(exist_ok=True)
    receipt = slot / "construction-receipt.json"
    receipt.write_text('{"state":"reserved"}', encoding="utf-8")
    signed = tmp_path / "signed-python.ps1"
    signed.write_text(
        f"""
$Dir = $args[-1]
New-Item -ItemType Directory -Path (Join-Path $Dir 'Scripts') -Force | Out-Null
[IO.File]::WriteAllText((Join-Path $Dir 'Scripts/python.exe'), 'fixture')
if ('{usable}' -eq 'True') {{ [IO.File]::WriteAllText((Join-Path $Dir 'pyvenv.cfg'), 'fixture') }}
exit {signed_exit}
""",
        encoding="utf-8",
    )
    script = (
        _prelude("ps1")
        + f"""
$env:OS = 'Windows_NT'
$VenvDir='{slot}'
$VenvPython=Join-Path $VenvDir 'Scripts/python.exe'
$UvCommand='{uv}'
$script:RuntimeConstructionReuse=$false
$pythonCmd='{tmp_path / "forbidden-python.ps1"}'
function py {{ $global:LASTEXITCODE=0; '{signed}' }}
function Get-AuthenticodeSignature {{ param($Path) return @{{ Status='Valid' }} }}
function Invoke-VersionedSlotClean {{ throw 'reserved slot must not be cleaned after signed bootstrap' }}
function Remove-Item {{ throw 'reserved slot or receipt must not be raw-deleted' }}
function Test-DispatchVenv {{
    param($Dir, $Python)
    if ((Test-Path -LiteralPath $Python) -and [IO.File]::ReadAllText($Python) -eq 'invalid existing Python') {{ return $false }}
    $cfg = Join-Path $Dir 'pyvenv.cfg'
    return ((Test-Path -LiteralPath $Python) -and (Test-Path -LiteralPath $cfg) -and [IO.File]::ReadAllText($cfg) -ne 'wrong prefix')
}}
{_signed_venv_block()}
if ($ErrorActionPreference -ne 'Stop') {{ throw 'error preference leaked' }}
"""
    )
    result = _run("ps1", script, tmp_path, env)
    assert result.returncode == (0 if usable else 1), result.stdout + result.stderr
    log = Path(env["TEST_UV_LOG"])
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    assert calls == []
    assert receipt.read_text(encoding="utf-8") == '{"state":"reserved"}'
    assert (slot / "Scripts/python.exe").read_text() == "fixture"
    if usable:
        assert (slot / "pyvenv.cfg").read_text() == "fixture"
        if signed_exit:
            assert f"Signed Python exited {signed_exit}" in result.stdout
    else:
        assert "Signed Python venv failed validation" in result.stdout + result.stderr


def test_completed_construction_reuse_skips_signed_venv_mutation(tmp_path):
    env = _env(tmp_path)
    script = (
        _prelude("ps1")
        + """
$script:RuntimeConstructionReuse=$true
function Test-DispatchVenv { throw 'completed candidate must not be reprobed here' }
function Invoke-VersionedSlotClean { throw 'completed candidate must not be cleaned' }
function Remove-Item { throw 'completed candidate must not be deleted' }
"""
        + _signed_venv_block()
    )
    result = _run("ps1", script, tmp_path, env)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "probe,code,healthy",
    [
        ("matching", 0, True),
        ("wrong-prefix", 0, False),
        ("base-python", 0, False),
        ("matching", 23, False),
        ("missing-cfg", 0, False),
    ],
)
def test_dispatch_venv_health_checks_prefix_cfg_and_probe_exit(tmp_path, probe, code, healthy):
    env = _env(tmp_path)
    slot = tmp_path / "slot"
    slot.mkdir()
    python = slot / "python-fixture"
    python.touch()
    if probe != "missing-cfg":
        (slot / "pyvenv.cfg").touch()
    prefix = tmp_path / "wrong" if probe == "wrong-prefix" else slot
    flag = "0" if probe == "base-python" else "1"
    script = (
        _prelude("ps1")
        + _function("ps1", "Test-DispatchVenv")
        + f"""
function Invoke-NativeCapture {{
    param($Command)
    return [pscustomobject]@{{ ExitCode={code}; Output="{prefix}`n{flag}" }}
}}
$result=Test-DispatchVenv -Dir '{slot}' -Python '{python}'
if ($result -ne ${str(healthy).lower()}) {{ throw 'unexpected health outcome' }}
"""
    )
    result = _run("ps1", script, tmp_path, env)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("ext", ["sh", "ps1"])
def test_manifest_preserves_dispatch_schema_and_marketplace_origin(tmp_path, ext):
    env = _env(tmp_path)
    origin = tmp_path / ".copilot/installed-plugins/example/agent-dispatch"
    env["COPILOT_PLUGIN_STAGED_FROM"] = str(origin)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    payload = tmp_path / "payload"
    payload.mkdir()
    if ext == "sh":
        script = (
            _prelude(ext)
            + _function(ext, "_write_manifest")
            + f"""
INSTALL_DIR='{runtime}'
PLUGIN_DIR='{payload}'
VENV_DIR='{runtime / "versions/1.2.3"}'
SRC_VERSION=1.2.3
_source_kind() {{ echo marketplace; }}
_write_manifest
"""
        )
    else:
        script = (
            _prelude(ext)
            + _function(ext, "Write-Manifest")
            + f"""
$InstallDir='{runtime}'
$PluginDir='{payload}'
$LinkDir='{runtime / "versions/1.2.3"}'
$SrcVersion='1.2.3'
function Get-SourceKind {{ param($PluginPath) return 'marketplace' }}
function Get-GitInfo {{ throw 'marketplace must not query git' }}
Write-Manifest
"""
        )
    result = _run(ext, script, tmp_path, env)
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads((runtime / "deploy-manifest.json").read_text(encoding="utf-8-sig"))
    assert manifest["schema_version"] == 3
    assert manifest["service"] == "agent-dispatch"
    assert manifest["runtime"] == "python"
    assert manifest["venv"] == str(runtime / "versions/1.2.3").replace("\\", "/")
    assert manifest["source"] == {
        "kind": "marketplace",
        "path": str(origin).replace("\\", "/"),
        "repo": "copilot-extensions",
        "plugin": "agent-dispatch",
        "version": "1.2.3",
        "commit": None,
        "branch": None,
        "dirty": False,
    }
    assert not (runtime / "deploy-manifest.json.tmp").exists()


def test_snapshot_hash_preserves_case_distinct_paths(tmp_path):
    env = _env(tmp_path)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "A.py").write_text("upper")
    (snapshot / "a.py").write_text("lower")
    if len(list(snapshot.iterdir())) != 2:
        pytest.skip("case-insensitive filesystem")
    script = (
        _prelude("ps1")
        + _function("ps1", "Get-DispatchSnapshotHash")
        + f"""
$first = Get-DispatchSnapshotHash -SnapshotDir '{snapshot}'
[IO.File]::WriteAllText('{snapshot / "A.py"}', 'upper changed')
$second = Get-DispatchSnapshotHash -SnapshotDir '{snapshot}'
if ($first -eq $second) {{ throw 'case-distinct upper path disappeared from hash' }}
[IO.File]::WriteAllText('{snapshot / "a.py"}', 'lower changed')
$third = Get-DispatchSnapshotHash -SnapshotDir '{snapshot}'
if ($second -eq $third) {{ throw 'case-distinct lower path disappeared from hash' }}
if ($third -ne (Get-DispatchSnapshotHash -SnapshotDir '{snapshot}')) {{ throw 'hash is not stable' }}
"""
    )
    result = _run("ps1", script, tmp_path, env)
    assert result.returncode == 0, result.stdout + result.stderr


def _fixture_checkout(tmp_path: Path, *, materialized: bool = False) -> Path:
    root = tmp_path / "authoring"
    plugin = root / "plugins/agent-dispatch"
    shutil.copytree(PLUGIN / "scripts", plugin / "scripts")
    shutil.copyfile(PLUGIN / "pyproject.toml", plugin / "pyproject.toml")
    shutil.copyfile(PLUGIN / "plugin.json", plugin / "plugin.json")
    refs = ier.plugin_ref_map(plugin)
    assert set(refs) == {"sh", "ps1"}
    if any(ier.is_canonical_ref(ref, plugin, repo_root=root) for ref in refs.values()):
        shutil.copytree(ENGINE, root / "libs/installer-engine")
    project_sources = tomllib.loads((plugin / "pyproject.toml").read_text())["tool"]["uv"]["sources"]
    for package, lib in LIBRARIES.items():
        source = (plugin / project_sources[package]["path"]).resolve()
        source.mkdir(parents=True)
        project = f'[project]\nname = "{package}"\nversion = "1.0.0"\n'
        if lib == "plugin-activation":
            project += '[tool.uv.sources]\nagent-dropin-registry = { path = "../dropin-registry", editable = true }\n'
        (source / "pyproject.toml").write_text(project, encoding="utf-8")
        (source / "src").mkdir()
        (source / "src" / "fixture.py").write_text(f'PACKAGE = "{package}"\n')
    if materialized:
        dest = tmp_path / "release" / "plugins" / "agent-dispatch"
        shutil.copytree(plugin, dest)
        before = {
            path: path.read_bytes() for path in root.rglob("*") if path.is_file()
        }
        for materializer in (
            mm.materialize_uv_editable_ref_into,
            mm.materialize_installer_engine_ref_into,
        ):
            log = materializer(
                source_consumer_dir=plugin, dest_consumer_dir=dest,
                canonical_root=root, dest_root=dest.parents[1],
            )
            assert not any(line.startswith("SKIP") for line in log), log
        assert before == {
            path: path.read_bytes() for path in root.rglob("*") if path.is_file()
        }, "release materialization must not mutate source"
        return dest
    return plugin


def _snapshot_prelude() -> str:
    script = _prelude("ps1")
    for name in (
        "Resolve-VendoredLib",
        "Materialize-DispatchSnapshot",
        "Get-DispatchSnapshotHash",
        "Get-SourceKind",
        "Enter-PluginSnapshotLock",
        "New-PluginBuildSnapshot",
        "Publish-FileAtomically",
        "Test-VersionLt",
        "Get-VerTuple",
        "Invoke-Stamp",
    ):
        script += _function("ps1", name)
    return script


def test_delayed_stamp_cannot_publish_over_newer_same_version_content(tmp_path):
    env = _env(tmp_path)
    plugin = _fixture_checkout(tmp_path)
    runtime = tmp_path / "runtime"
    script = _snapshot_prelude()
    script += _function("ps1", "Enter-PluginSnapshotLock").replace(
        "function Enter-PluginSnapshotLock", "function Enter-RealSnapshotLock", 1
    )
    script += f"""
$PluginDir='{plugin}'
$InstallDir='{runtime}'
$LocalBin='{tmp_path / "local-bin"}'
$SrcVersion='1.2.3'
$Force=$false
$script:GlobalActivationLockTimeoutSeconds=10
$script:interleaved=$false
$script:deployed=0
function Deploy-SelfProvisioningBinstub {{ $script:deployed++ }}
function Enter-PluginSnapshotLock {{
    param($InstallDir, $Version, $TimeoutSeconds=20)
    if (-not $Version -and -not $script:interleaved) {{
        $script:interleaved=$true
        [IO.File]::WriteAllText((Join-Path $PluginDir 'new-source.py'), 'newer content')
        Invoke-Stamp
    }}
    return Enter-RealSnapshotLock -InstallDir $InstallDir -Version $Version -TimeoutSeconds $TimeoutSeconds
}}
Invoke-Stamp
$published = [IO.File]::ReadAllText((Join-Path $InstallDir 'payload-dir'))
if (-not (Test-Path (Join-Path $published 'new-source.py'))) {{ throw 'delayed stamp overwrote newer content' }}
if ($script:deployed -ne 1) {{ throw 'stale stamp deployed its launchers' }}
if ([IO.File]::ReadAllText((Join-Path $InstallDir 'stamp-candidate-1.2.3')) -cne $published) {{ throw 'candidate and published identity diverged' }}
"""
    result = _run("ps1", script, tmp_path, env)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("materialized", [False, True], ids=["checkout", "release"])
@pytest.mark.parametrize("mutation", ["unchanged", "plugin", "engine", "library"])
def test_stamp_materializes_standalone_engine_pair_and_all_libraries(
    tmp_path, materialized, mutation,
):
    env = _env(tmp_path)
    plugin = _fixture_checkout(tmp_path, materialized=materialized)
    engine = ier.find_engine_ref(plugin / "scripts/install.sh", "sh")
    assert engine is not None
    sources = tomllib.loads((plugin / "pyproject.toml").read_text())["tool"]["uv"]["sources"]
    library = (plugin / sources["agent-procutil"]["path"]).resolve()
    runtime = tmp_path / "runtime"
    baseline_path = tmp_path / "baseline-path"
    change = {
        "unchanged": "",
        "plugin": (
            "[IO.File]::WriteAllText((Join-Path $PluginDir 'new-source.py'), "
            "'changed plugin source')"
        ),
        "engine": f"""[IO.File]::AppendAllText('{engine.resolved()}', "`n# fixture edit")""",
        "library": (
            f"[IO.File]::WriteAllText('{library / 'new-lib.py'}', 'changed library source')"
        ),
    }[mutation]
    script = _snapshot_prelude()
    script += f"""
$PluginDir='{plugin}'
$InstallDir='{runtime}'
$LocalBin='{tmp_path / "local-bin"}'
$SrcVersion='1.2.3'
$Force=$false
$script:GlobalActivationLockTimeoutSeconds=10
function Deploy-SelfProvisioningBinstub {{ [IO.File]::WriteAllText((Join-Path $LocalBin 'stamp-proof'), 'deployed') }}
Invoke-Stamp
$first = [IO.File]::ReadAllText((Join-Path $InstallDir 'payload-dir'))
[IO.File]::WriteAllText('{baseline_path}', $first)
$firstHash = Get-DispatchSnapshotHash -SnapshotDir $first
{change}
Invoke-Stamp
$second = [IO.File]::ReadAllText((Join-Path $InstallDir 'payload-dir'))
if (($first -ceq $second) -ne ('{mutation}' -eq 'unchanged')) {{ throw '{mutation}: unexpected snapshot identity' }}
if ($firstHash -cne (Get-DispatchSnapshotHash -SnapshotDir $first)) {{ throw 'published baseline snapshot was mutated' }}
if ([IO.File]::ReadAllText((Join-Path $InstallDir 'stamp-candidate-1.2.3')) -cne $second) {{ throw 'candidate and published identity diverged' }}
if (@(Get-ChildItem (Join-Path $InstallDir 'snapshots') -Directory).Count -ne {1 if mutation == "unchanged" else 2}) {{ throw 'unexpected snapshot count' }}
"""
    result = _run("ps1", script, tmp_path, env)
    assert result.returncode == 0, result.stdout + result.stderr
    snapshots = sorted((runtime / "snapshots").iterdir())
    snapshot = Path(baseline_path.read_text(encoding="utf-8").strip())
    published = Path((runtime / "payload-dir").read_text(encoding="utf-8").strip())
    assert snapshot in snapshots and published in snapshots
    assert snapshot.parent == runtime / "snapshots"
    assert (runtime / "stamped-version").read_text() == "1.2.3"
    assert not (runtime / "current-version").exists()
    assert len(snapshots) == (1 if mutation == "unchanged" else 2)
    assert (snapshot == published) == (mutation == "unchanged")
    assert not (snapshot / "new-source.py").exists()
    assert not (snapshot / "libs/agent-procutil/new-lib.py").exists()
    if mutation == "plugin":
        assert (published / "new-source.py").read_text() == "changed plugin source"
    if mutation == "library":
        assert (published / "libs/agent-procutil/new-lib.py").read_text() == "changed library source"
    for ext in ("sh", "ps1"):
        assert (snapshot / "scripts" / f"installer-engine.{ext}").read_bytes() == (
            ENGINE / f"installer-engine.{ext}"
        ).read_bytes()
        consumed = ier.find_engine_ref(plugin / "scripts" / f"install.{ext}", ext)
        assert consumed is not None
        assert (published / "scripts" / f"installer-engine.{ext}").read_bytes() == (
            consumed.resolved().read_bytes()
        )
        for selected in (snapshot, published):
            script = selected / "scripts" / f"install.{ext}"
            assert ier.source_match_count(script, ext) == 1
            ref = ier.find_engine_ref(script, ext)
            assert ref is not None and ier.is_local_ref(ref, selected)
    for name in (
        "installer_dependency_policy.py",
        "installer_dependency_config.py",
        "versioned_runtime.py",
        "versioned_runtime_leases.py",
        "versioned_runtime_construction.py",
        "versioned_runtime_source.py",
    ):
        source = ENGINE if name in (
            "installer_dependency_policy.py", "installer_dependency_config.py",
        ) else PLUGIN / "scripts"
        assert (snapshot / "scripts" / name).read_bytes() == (source / name).read_bytes()
    project = tomllib.loads((snapshot / "pyproject.toml").read_text())
    assert project["tool"]["uv"]["sources"] == {
        package: {"path": f"libs/{lib}"} for package, lib in LIBRARIES.items()
    }
    for lib in LIBRARIES.values():
        assert (snapshot / "libs" / lib / "pyproject.toml").is_file()
    nested = tomllib.loads((snapshot / "libs/plugin-activation/pyproject.toml").read_text())
    assert nested["tool"]["uv"]["sources"]["agent-dropin-registry"] == {
        "path": "../dropin-registry"
    }


@pytest.mark.parametrize(
    "materialized,acquired_uv,invalid",
    [
        (False, False, None), (True, False, None),
        (False, True, None), (True, True, None),
        (True, True, "missing-helper"), (True, True, "outside-source"),
    ],
    ids=["checkout-fallback", "release-fallback", "checkout-uv", "release-uv",
         "missing-helper", "outside-source"],
)
def test_runtime_install_attributes_its_snapshot_not_the_calling_helper(
    tmp_path, materialized, acquired_uv, invalid,
):
    env = _env(tmp_path)
    plugin = _fixture_checkout(tmp_path, materialized=materialized)
    runtime = tmp_path / "runtime"
    snapshot = runtime / "snapshots" / "1.2.3"
    uv = _fake_uv(tmp_path)
    env["TEST_UV_EXPECTED_SOURCES"] = json.dumps({
        "agent-dispatch": str(snapshot),
        **{package: str(snapshot / "libs" / lib) for package, lib in LIBRARIES.items()},
    })
    uv_result = f"return '{uv}'" if acquired_uv else "return $null"
    python = Path(sys.executable)
    if not acquired_uv:
        # CreateProcess cannot resolve a bare uv.cmd like a real uv.exe.
        python = tmp_path / "python-fixture.ps1"
        python.write_text(
            f"""
$forward = @($args)
for ($i = 0; $i -lt $forward.Count - 1; $i++) {{
    if ($forward[$i] -eq '--uv' -and $forward[$i + 1] -eq 'uv') {{
        $forward[$i + 1] = '{uv}'
        # PowerShell consumed the script-call end-of-options marker.
        $forward = @($forward[0..($i + 1)]) + @('--') + @($forward[($i + 2)..($forward.Count - 1)])
        break
    }}
}}
& '{sys.executable}' @forward
exit $LASTEXITCODE
""", encoding="utf-8",
        )
    failure = "package-boundary-reached"
    mutation = ""
    if invalid == "missing-helper":
        failure = "Build snapshot dependency policy is unavailable:"
        mutation = """
Set-Item Function:New-RealPluginBuildSnapshot ${function:New-PluginBuildSnapshot}
function New-PluginBuildSnapshot {
    param($PluginDir, $InstallDir, $Version, [switch]$BestEffort)
    $snapshot = New-RealPluginBuildSnapshot @PSBoundParameters
    Remove-Item -LiteralPath (Join-Path $snapshot 'scripts/installer_dependency_policy.py')
    return $snapshot
}
"""
    elif invalid == "outside-source":
        failure = "local source is outside attributable trusted source roots"
        outside = tmp_path / "unowned"
        outside.mkdir()
        (outside / "pyproject.toml").write_text(
            '[project]\nname = "agent-zdd"\nversion = "1.0.0"\n', encoding="utf-8",
        )
        mutation = f"function Resolve-Zdd {{ param($LibRoot) return '{outside}' }}\n"
    script = (
        _snapshot_prelude()
        + _function("ps1", "Resolve-Zdd")
        + _function("ps1", "Install-Runtime")
        + f"""
$PluginDir='{plugin}'
$PkgSrcDir=$PluginDir
$InstallDir='{runtime}'
$LocalBin='{tmp_path / "local-bin"}'
$SrcVersion='1.2.3'
$VenvDir='{tmp_path / "slot"}'
$VenvPython='{python}'
$script:RuntimeConstructionReuse=$false
$originalPolicy=$script:InstallerDependencyPolicy
$script:trampolinePass=0
function Get-SourceKind {{ param($PluginPath) return 'marketplace' }}
function Get-Command {{ return [pscustomobject]@{{ Source='{sys.executable}' }} }}
function Write-Fail {{ param($Msg) throw "$Msg; $zddOut" }}
function Ensure-Uv {{ param($InstallRoot) {uv_result} }}
function Invoke-VersionedSlotClean {{ }}
function Test-DispatchVenv {{ param($Dir, $Python) return $true }}
function Remove-PluginBuildArtifacts {{ param($PluginDir, $ExtraDir) }}
function Remove-ConsoleTrampolines {{
    param($VenvDir)
    $script:trampolinePass++
    if ($script:trampolinePass -eq 2) {{ throw 'package-boundary-reached' }}
}}
{mutation}
try {{
    Install-Runtime
    throw 'package boundary was not exercised'
}} catch {{
    if (-not $_.Exception.Message.Contains('{failure}')) {{ throw }}
}}
if ($script:InstallerDependencyPolicy -cne $originalPolicy) {{ throw 'caller policy was not restored' }}
"""
    )
    result = _run("ps1", script, tmp_path, env)
    assert result.returncode == 0, result.stdout + result.stderr
    if invalid:
        assert not Path(env["TEST_UV_LOG"]).exists()
        return
    calls = [json.loads(line) for line in Path(env["TEST_UV_LOG"]).read_text().splitlines()]
    assert len(calls) == (7 if acquired_uv else 8)
    expected_sources = set(json.loads(env["TEST_UV_EXPECTED_SOURCES"]).values()) | {f"{snapshot}[mcp]"}
    sources = [
        next(arg for arg in call if arg in expected_sources)
        for call in calls
    ]
    assert sources[:6] == [
        str(snapshot / "libs" / lib)
        for lib in (
            "zdd", "dropin-registry", "plugin-resolve",
            "single-instance-lease", "agent-procutil", "plugin-activation",
        )
    ]
    assert sources[6:] == [f"{snapshot}[mcp]"] * (1 if acquired_uv else 2)
    for call in calls:
        assert "--exclude-newer" in call
        cutoff = datetime.fromisoformat(call[call.index("--exclude-newer") + 1].replace("Z", "+00:00"))
        assert cutoff <= datetime.now(timezone.utc) - timedelta(days=7)
        assert "--reinstall" in call


@pytest.mark.parametrize("materialized", [False, True], ids=["checkout", "release"])
def test_materialized_snapshot_admits_install_without_authoring_checkout(tmp_path, materialized):
    env = _env(tmp_path)
    plugin = _fixture_checkout(tmp_path, materialized=materialized)
    runtime = tmp_path / "runtime"
    stamped = _run(
        "ps1", _snapshot_prelude() + f"""
$PluginDir='{plugin}'
$InstallDir='{runtime}'
$LocalBin='{tmp_path / "local-bin"}'
$SrcVersion='1.2.3'
$Force=$false
$script:GlobalActivationLockTimeoutSeconds=10
function Deploy-SelfProvisioningBinstub {{ }}
Invoke-Stamp
""", tmp_path, env,
    )
    assert stamped.returncode == 0, stamped.stdout + stamped.stderr
    snapshot = Path((runtime / "payload-dir").read_text().strip())
    shutil.move(str(plugin.parents[1]), str(tmp_path / "authoring-unavailable"))
    if materialized:
        shutil.move(str(tmp_path / "authoring"), str(tmp_path / "source-unavailable"))
    uv = _fake_uv(tmp_path)
    env["TEST_UV_EXPECTED_SOURCES"] = json.dumps({"agent-dispatch": str(snapshot)})
    proof = _run(
        "ps1",
        _prelude("ps1", snapshot / "scripts")
        + f"""
& '{sys.executable}' -I '{snapshot / "scripts/versioned_runtime.py"}' --help > $null
if ($LASTEXITCODE -ne 0) {{ throw 'standalone runtime helper cannot load its companions' }}
$result = Invoke-UvVenvResilient -VenvDir '{tmp_path / "slot"}' -Arguments @('--allow-existing') -UvCommand '{uv}'
if ($result.ExitCode -ne 0) {{ throw $result.Output }}
$result = Invoke-UvPipInstallResilient -UvCommand '{uv}' -Arguments @('--python', '{sys.executable}', '{snapshot}[mcp]')
if ($result.ExitCode -ne 0) {{ throw $result.Output }}
exit $result.ExitCode
""",
        tmp_path,
        env,
    )
    assert proof.returncode == 0, proof.stdout + proof.stderr
    calls = [
        json.loads(line)
        for line in Path(env["TEST_UV_LOG"]).read_text().splitlines()
    ]
    assert calls[0] == ["venv", str(tmp_path / "slot"), "--allow-existing"]
    assert len(calls) == 2
    assert calls[1][:-3] == [
        "pip", "install", "--python", sys.executable, f"{snapshot}[mcp]"
    ]
    assert calls[1][-3] == "--exclude-newer"
    assert calls[1][-1] == "--reinstall"
    assert "PipInstallSettings" not in proof.stdout + proof.stderr


@pytest.mark.parametrize("materialized", [False, True], ids=["checkout", "release"])
@pytest.mark.parametrize("missing", ["sh", "ps1", "agent-procutil"])
def test_stamp_refuses_missing_consumed_input(tmp_path, materialized, missing):
    env = _env(tmp_path)
    plugin = _fixture_checkout(tmp_path, materialized=materialized)
    if missing in ("sh", "ps1"):
        ref = ier.find_engine_ref(plugin / "scripts" / f"install.{missing}", missing)
        assert ref is not None
        ref.resolved().unlink()
        assert not (plugin / "scripts" / f"installer-engine.{missing}").exists()
        assert not (plugin.parents[1] / "libs/installer-engine" / f"installer-engine.{missing}").exists()
    else:
        sources = tomllib.loads((plugin / "pyproject.toml").read_text())["tool"]["uv"]["sources"]
        shutil.rmtree((plugin / sources[missing]["path"]).resolve())
    runtime = tmp_path / "runtime"
    result = _run(
        "ps1", _snapshot_prelude() + f"""
$PluginDir='{plugin}'
$InstallDir='{runtime}'
$LocalBin='{tmp_path / "local-bin"}'
$SrcVersion='1.2.3'
$Force=$false
$script:GlobalActivationLockTimeoutSeconds=10
function Deploy-SelfProvisioningBinstub {{ throw 'missing input must not publish launchers' }}
Invoke-Stamp
""", tmp_path, env,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert (
        f"installer-engine.{missing}" if missing in ("sh", "ps1")
        else "Cannot locate required snapshot library: agent-procutil"
    ) in result.stdout + result.stderr
    assert not (runtime / "payload-dir").exists()
    assert not (runtime / "stamped-version").exists()
