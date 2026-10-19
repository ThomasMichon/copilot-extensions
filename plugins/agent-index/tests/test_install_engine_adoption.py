"""Contained installer mechanics: no real packages, engine, or service effects."""
from __future__ import annotations

import json
import os
import re
import runpy
import shlex
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]
ENGINE = REPO / "libs" / "installer-engine"
FAKE_PYTHON = """#!/usr/bin/env bash
if [[ "$*" == *sys.prefix* ]]; then
    printf '%s\\n1\\n' "$(cd "$(dirname "$0")/.." && pwd -P)"
fi
exit 0
"""


def fake_uv(tmp_path: Path, body: str) -> Path:
    source = tmp_path / "fixture_uv.py"
    source.write_text(
        "import pathlib, sys\n"
        "if '--show-settings' in sys.argv:\n"
        " print('''PipInstallSettings { settings: PipSettings {\n"
        " index_locations: IndexLocations { indexes: [], flat_index: [], },\n"
        " exclude_newer: ExcludeNewer { global: None, package: ExcludeNewerPackage({}), },\n"
        " }, }''')\n"
        " raise SystemExit(0)\n" + body,
        encoding="utf-8",
    )
    wrapper = tmp_path / ("fixture_uv.cmd" if os.name == "nt" else "fixture_uv")
    if os.name == "nt":
        wrapper.write_text(f'@"{sys._base_executable}" "{source}" %*\n', encoding="utf-8")
    else:
        wrapper.write_text(
            f"#!/bin/sh\nexec {shlex.quote(sys._base_executable)} {shlex.quote(str(source))} \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
    return wrapper


def construction_bundle(scripts: Path) -> None:
    scripts.mkdir(parents=True, exist_ok=True)
    for name in ("versioned_runtime.py", "versioned_runtime_leases.py",
                 "versioned_runtime_construction.py", "versioned_runtime_source.py"):
        shutil.copy2(REPO / "libs/versioned-runtime" / name, scripts / name)


def _materializer() -> dict:
    previous = sys.path[:]
    try:
        return runpy.run_path(str(REPO / "tools/materialize_main.py"))
    finally:
        sys.path[:] = previous


def stage_index_installer(payload: Path, *, materialized: bool) -> Path:
    """Build either wrapper layout from canonical assets, regardless of the test checkout."""
    release = _materializer()
    refs = release["mie"].ier
    canonical_root = payload.parents[1]
    canonical_engine = canonical_root / "libs" / "installer-engine"
    shutil.copytree(
        ENGINE, canonical_engine,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
    )
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    for extension in ("sh", "ps1"):
        source = PLUGIN / "scripts" / f"install.{extension}"
        ref = refs.find_engine_ref(source, extension)
        assert ref is not None
        assert refs.is_canonical_ref(ref, PLUGIN, repo_root=REPO) or refs.is_local_ref(ref, PLUGIN)
        lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
        matches = 0
        for index, line in enumerate(lines):
            if line.strip() in (refs.canonical_line(extension), refs.local_line(extension)):
                lines[index] = refs.canonical_line(extension) + "\n"
                matches += 1
        assert matches == 1
        (scripts / source.name).write_text("".join(lines), encoding="utf-8")
    script = scripts / "install.ps1"
    if refs.HUMAN_SOURCE_LINE in script.read_text(encoding="utf-8"):
        shutil.copy2(canonical_engine / refs.HUMAN_HELPER, scripts / refs.HUMAN_HELPER)
        script.with_suffix(".human-output.json").write_text(
            refs.human_output_json(script), encoding="utf-8",
        )
    if materialized:
        report = release["materialize_installer_engine_ref_into"](
            source_consumer_dir=payload, dest_consumer_dir=payload,
            canonical_root=canonical_root, dest_root=canonical_root,
        )
        assert not any(message.startswith("SKIP ") for message in report), report
    # Fixture text writers, including the materializer, translate newlines on
    # Windows. Git Bash needs LF after the final rewrite, not just the first one.
    bash_entry = scripts / "install.sh"
    bash_entry.write_bytes(bash_entry.read_bytes().replace(b"\r\n", b"\n"))
    for extension in ("sh", "ps1"):
        ref = refs.find_engine_ref(scripts / f"install.{extension}", extension)
        assert ref is not None
        if materialized:
            assert refs.is_local_ref(ref, payload)
            assert (scripts / ref.file_name).read_bytes() == (canonical_engine / ref.file_name).read_bytes()
        else:
            assert refs.is_canonical_ref(ref, payload, repo_root=canonical_root)
            assert not (scripts / ref.file_name).exists()
    if script.with_suffix(".human-output.json").exists():
        assert script.with_suffix(".human-output.json").read_text(encoding="utf-8") == (
            refs.human_output_json(script)
        )
    return payload


def stage_procutil_layout(repository: Path, *, materialized: bool) -> Path:
    """Select an explicit procutil layout instead of inheriting the checkout's layout."""
    from test_installer_publication_provisioning import stage_index_policy_sources

    payload = stage_index_policy_sources(repository, materialized=True)
    local = payload / "libs" / "agent-procutil"
    assert (local / "pyproject.toml").is_file()
    if not materialized:
        canonical = repository / "libs" / "agent-procutil"
        shutil.copytree(local, canonical, dirs_exist_ok=True)
        sources = _materializer()["uer"]
        project = payload / "pyproject.toml"
        assert sources.can_rewrite_uv_source_to_editable(project, "agent-procutil")
        sources.rewrite_uv_source_to_editable(
            project, "agent-procutil", "../../libs/agent-procutil",
        )
        shutil.rmtree(local)
        assert not local.exists()
        assert (canonical / "pyproject.toml").is_file()
    return payload


def posix_venv_uv(tmp_path: Path, *, failure: bool = False, transient: bool = False) -> Path:
    """An offline executable with real venv identity and independent settings admission."""
    uv = fake_uv(tmp_path, f"""
import subprocess, venv
root = pathlib.Path({str(tmp_path)!r})
args = sys.argv[1:]
with (root / 'calls').open('a') as stream:
 stream.write(' '.join(args) + '\\n')
if args == ['--version']:
 print('uv 0.8.0')
elif args[0] == 'venv':
 attempt = root / 'venv-attempt'
 if {transient!r} and not attempt.exists():
  attempt.touch()
  raise SystemExit(0)
 destination = pathlib.Path(args[1])
 venv.EnvBuilder(with_pip=False, symlinks=True).create(destination)
 python = destination / 'bin/python'
 site = pathlib.Path(subprocess.check_output([str(python), '-I', '-c',
  'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip())
 for name in ('torch.py', 'agent_index.py'):
  (site / name).write_text('')
 app = site / 'agent_index_engine'
 app.mkdir(exist_ok=True)
 (app / '__init__.py').write_text('')
 (app / 'app.py').write_text('')
elif {failure!r} and any('agent-procutil' in arg for arg in args):
 raise SystemExit(37)
""")
    executable = tmp_path / "uv"
    uv.rename(executable)
    return executable


def function(name: str, ext: str) -> str:
    text = (PLUGIN / "scripts" / f"install.{ext}").read_text(encoding="utf-8")
    start = f"function {name}" if ext == "ps1" else f"{name}() {{"
    if ext == "sh" and start not in text:
        start = f"{name}() ("
    tail = text.split(start, 1)[1]
    next_function = r"\nfunction \w" if ext == "ps1" else r"\n[a-z_]+\(\) [\{(]"
    return start + re.split(next_function, tail, maxsplit=1)[0]


def environment(tmp_path: Path) -> dict[str, str]:
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENT_", "COPILOT_", "PYTHON", "UV_", "PIP_", "GIT_"))
    }
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME",
                "XDG_CACHE_HOME", "XDG_DATA_HOME", "TMP", "TEMP", "TMPDIR"):
        root = tmp_path / key.lower()
        root.mkdir(exist_ok=True)
        env[key] = str(root)
    env["COPILOT_EXTENSIONS_TEST_CONTAINED"] = "1"
    return env


def run_ps(tmp_path: Path, script: str) -> subprocess.CompletedProcess[str]:
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")
    harness = tmp_path / "harness.ps1"
    formatter = next(
        line for line in (PLUGIN / "scripts/install.ps1").read_text().splitlines()
        if line.startswith("function Write-Step ")
    )
    harness.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$SrcVersion = '1.0.0'; $Force = $false\n"
        "function Write-Ok { param($Msg) }\n"
        "function Write-Warn { param($Msg) }\n"
        + formatter + "\n"
        "function Write-Skip { param($Msg) }\n"
        "function Write-Fail { param($Msg) throw $Msg }\n"
        "function Start-Process { throw 'Forbidden process launch' }\n"
        "function Stop-Process { throw 'Forbidden process termination' }\n"
        "function Register-ScheduledTask { throw 'Forbidden task registration' }\n"
        "function Start-ScheduledTask { throw 'Forbidden task activation' }\n"
        "function Restart-Service { throw 'Forbidden service restart' }\n"
        "function Invoke-WebRequest { throw 'Forbidden bootstrap download' }\n"
        f". '{ENGINE / 'installer-engine.ps1'}'\n"
        + "\n".join(function(name, "ps1") for name in (
            "Get-VerTuple", "Test-VersionLt", "New-IndexMutex", "Enter-IndexStampLock", "Enter-IndexBuildLock", "Get-IndexUv",
            "Test-IndexPublicationFresh",
        )) + "\n" + script,
        encoding="utf-8",
    )
    return subprocess.run(
        [pwsh, "-NoProfile", "-File", str(harness)],
        cwd=tmp_path, env=environment(tmp_path), capture_output=True, text=True, timeout=25,
    )


@pytest.mark.parametrize("materialized", [False, True], ids=["dev", "materialized"])
def test_canonical_references_and_both_runtime_builds(tmp_path: Path, materialized: bool) -> None:
    payload = stage_index_installer(
        tmp_path / "repo" / "plugins" / "agent-index", materialized=materialized,
    )
    sh = (payload / "scripts/install.sh").read_text(encoding="utf-8")
    ps = (payload / "scripts/install.ps1").read_text(encoding="utf-8")
    for directory in ("$VENV_DIR", "$ENGINE_VENV", "$server_venv_dir"):
        assert f'_new_index_venv "{directory}"' in sh
    assert 'invoke_uv_venv_resilient "${UV_COMMAND:-uv}" "$dir" --allow-existing' in sh
    for directory in ("$VenvDir", "$EngineVenv", "$serverVenvDir"):
        assert f"New-IndexVenv -Dir {directory}" in ps
    assert "Invoke-UvVenvResilient" in function("New-IndexVenv", "ps1")
    assert "Invoke-UvPipInstallResilient" in function("Invoke-IndexUvPipInstall", "ps1")


@pytest.mark.parametrize(
    ("extension", "name", "sources"),
    [
        ("ps1", "Install-Engine", '"$PluginDir", "$PluginDir\\server"'),
        ("sh", "_install_engine", '"$PLUGIN_DIR" "$PLUGIN_DIR/server"'),
    ],
)
def test_engine_transactions_keep_local_base_for_uv_and_fallback(extension, name, sources):
    transactions = [
        line for line in function(name, extension).splitlines()
        if ("$pipArgs =" in line or "local uv_args=" in line or "local pip_args=" in line)
        and ("/server" in line or "\\server" in line)
    ]
    assert len(transactions) == 2
    assert all(sources in line for line in transactions)


def test_engine_upgrade_resolves_unpublished_base_offline(tmp_path):
    uv = shutil.which("uv")
    assert uv is not None
    wheels = []
    for name, dependency in (("agent_index", ""), ("agent_index_engine", "Requires-Dist: agent-index\n")):
        wheel = tmp_path / f"{name}-1.0.0-py3-none-any.whl"
        metadata = f"{name}-1.0.0.dist-info"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr(
                f"{metadata}/METADATA",
                f"Metadata-Version: 2.1\nName: {name.replace('_', '-')}\nVersion: 1.0.0\n{dependency}",
            )
            archive.writestr(
                f"{metadata}/WHEEL",
                "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            )
            archive.writestr(f"{metadata}/RECORD", "")
        wheels.append(wheel)
    target = tmp_path / "venv"
    env = environment(tmp_path)
    created = subprocess.run(
        [uv, "venv", "--offline", "--python", sys.executable, str(target)],
        env=env, capture_output=True, text=True, timeout=15,
    )
    assert created.returncode == 0, created.stderr
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def install(*args):
        return subprocess.run(
            [uv, "pip", "install", "--offline", "--no-index", "--python", str(python), *args],
            env=env, capture_output=True, text=True, timeout=15,
        )

    base, engine = map(str, wheels)
    missing_source = install("--upgrade", engine)
    assert missing_source.returncode != 0
    assert "agent-index" in missing_source.stderr
    resolved = install("--upgrade", base, engine)
    assert resolved.returncode == 0, resolved.stderr


@pytest.mark.parametrize("failure_stage", ["zdd", "agent-procutil", "server", "torch"])
def test_native_engine_failed_upgrade_preserves_selection_and_retries(tmp_path, failure_stage):
    for name in (
        "versioned_runtime_leases.py", "versioned_runtime_construction.py",
        "versioned_runtime_source.py", "versioned_runtime.py",
    ):
        shutil.copyfile(REPO / "libs/versioned-runtime" / name, tmp_path / name)
    base = str(Path(sys._base_executable)).replace("'", "''")
    script = f"""
$EngineHome = '{tmp_path / "engine"}'
$PluginDir = '{tmp_path / "plugin"}'
$env:AGENT_INDEX_TORCH_INDEX = 'https://example.invalid/cuda'
$script:FailStage = ''
function Get-BootstrapPython {{ '{base}' }}
function Get-PayloadHash {{ 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' }}
function Get-IndexUv {{ 'fixture-uv' }}
function Resolve-Zdd {{ Join-Path $PluginDir 'libs/zdd' }}
function Resolve-VendoredLib {{ param($LibName) Join-Path $PluginDir "libs/$LibName" }}
function New-IndexVenv {{
    param($Dir, $Python, $PythonCmd, $PreferSignedPython)
    & '{base}' -I -m venv --without-pip $Dir
    if ($LASTEXITCODE -ne 0) {{ throw 'Fixture venv creation failed' }}
    & '{base}' -I -c 'import os, pathlib, sys; root = pathlib.Path(sys.argv[1]); expected = pathlib.Path(sys.argv[2]); expected.parent.mkdir(exist_ok=True); expected.symlink_to(root / "bin/python") if os.name != "nt" and not expected.exists() else None' $Dir $Python
    if ($LASTEXITCODE -ne 0) {{ throw 'Fixture interpreter layout failed' }}
    $site = & $Python -I -c 'import sysconfig; print(sysconfig.get_path("purelib"))'
    if ($LASTEXITCODE -ne 0) {{ throw 'Fixture site discovery failed' }}
    Set-Content (Join-Path $site 'torch.py') ''
    Set-Content (Join-Path $site 'agent_index.py') ''
    $app = Join-Path $site 'agent_index_engine'
    New-Item -ItemType Directory $app | Out-Null
    Set-Content (Join-Path $app '__init__.py') ''
    Set-Content (Join-Path $app 'app.py') ''
    return $true
}}
function Invoke-IndexUvPipInstall {{
    $server = @($args | Where-Object {{ "$_" -eq "$PluginDir\\server" }})
    if ($server.Count -gt 0 -and "$PluginDir" -notin $args) {{
        throw 'Engine dependency resolution lost the unpublished local base source'
    }}
    $stage = if ($args[-1] -eq 'torch') {{ 'torch' }} else {{
        @($args | Where-Object {{ "$_" -like "$PluginDir*" }})[-1] | Split-Path -Leaf
    }}
    $global:LASTEXITCODE = if ($stage -eq $script:FailStage) {{ 37 }} else {{ 0 }}
}}
{function("Install-Engine", "ps1")}
{function("Publish-FileAtomically", "ps1")}
if (-not (Install-Engine)) {{ throw 'Initial engine installation failed' }}
$before = Get-Content (Join-Path $EngineHome 'current-version') -Raw
$good = Join-Path $EngineHome "versions/$($before.Trim())"
$completion = [IO.File]::ReadAllText((Join-Path $good '.install-complete.json'))
$script:FailStage = '{failure_stage}'
$failed = $false
try {{ if (-not (Install-Engine -Upgrade)) {{ $failed = $true }} }} catch {{
    if ($_ -notmatch 'construction aborted') {{ throw }}
    $failed = $true
}}
if (-not $failed) {{ throw 'Selected dependency failure was swallowed' }}
if ((Get-Content (Join-Path $EngineHome 'current-version') -Raw) -cne $before) {{ throw 'Selection changed on failure' }}
if ((Get-Content (Join-Path $EngineHome 'last-known-good') -Raw) -cne $before) {{ throw 'Last known good changed on failure' }}
if ([IO.File]::ReadAllText((Join-Path $good '.install-complete.json')) -cne $completion) {{ throw 'Healthy completion was overwritten' }}
if (@(Get-ChildItem (Join-Path $EngineHome 'versions') -Directory).Count -ne 1) {{ throw 'Failed engine candidate was not reclaimed' }}
$script:FailStage = ''
if (-not (Install-Engine -Upgrade)) {{ throw 'Clean retry failed' }}
$after = (Get-Content (Join-Path $EngineHome 'current-version') -Raw).Trim()
if ($after -eq $before.Trim()) {{ throw 'Retry did not select a distinct repair' }}
if (-not (Test-Path (Join-Path $EngineHome "versions/$after/.install-complete.json"))) {{ throw 'Retry selected an incomplete slot' }}
if (-not (Install-Engine)) {{ throw 'Completed engine reuse failed' }}
if (@(Get-ChildItem (Join-Path $EngineHome 'versions') -Directory).Count -ne 2) {{ throw 'Completed reuse constructed another engine' }}
"""
    result = run_ps(tmp_path, script)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("transient", [False, True])
def test_posix_engine_build_order_and_durable_skip(tmp_path: Path, failure: bool, transient: bool):
    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        pytest.skip("native POSIX bash is unavailable")
    log = tmp_path / "calls"
    posix_venv_uv(tmp_path, failure=failure, transient=transient)
    scripts = tmp_path / "scripts"
    construction_bundle(scripts)
    for library in ("zdd", "agent-procutil"):
        directory = tmp_path / "plugin/libs" / library
        directory.mkdir(parents=True)
        (directory / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    env = environment(tmp_path)
    env["PATH"] = str(tmp_path) + os.pathsep + env["PATH"]
    env["AGENT_INDEX_TORCH_INDEX"] = "https://example.invalid/cuda"
    script = f"""
set -uo pipefail
. '{ENGINE / "installer-engine.sh"}'
_ok() {{ :; }}; _skip() {{ :; }}; _step() {{ :; }}; _warn() {{ :; }}
_fail() {{ echo "$*" >&2; }}
sleep() {{ :; }}
systemctl() {{ echo FORBIDDEN >&2; exit 97; }}
curl() {{ echo FORBIDDEN >&2; exit 97; }}
wget() {{ echo FORBIDDEN >&2; exit 97; }}
_find_python() {{ echo '{tmp_path / "forbidden-base-python"}'; }}
_bootstrap_python() {{ echo '{sys._base_executable}'; }}
_payload_hash() {{ printf '%s' aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; }}
_ensure_uv_index() {{ :; }}
SCRIPT_DIR='{scripts}'
SRC_VERSION='1.0.0'
INSTALL_DIR='{tmp_path / "runtime"}'
VENV_DIR="$INSTALL_DIR/versions/$SRC_VERSION"
PLUGIN_DIR='{tmp_path / "plugin"}'
ENGINE_HOME='{tmp_path / "engine"}'
ENGINE_VENV="$ENGINE_HOME/.venv"
ENGINE_VENV_PYTHON="$ENGINE_VENV/bin/python"
{function("_ensure_uv", "sh")}
{function("_uv_pip_install", "sh")}
_uv_pip_install() {{ "$UV_COMMAND" pip install "$@"; }}
{function("_test_index_venv", "sh")}
{function("_new_index_venv", "sh")}
{function("_with_index_build_lock", "sh")}
{function("_with_index_advisory_lock", "sh")}
{function("_resolve_vendored_lib", "sh")}
_resolve_zdd() {{ _resolve_vendored_lib zdd; }}
{function("_install_engine", "sh")}
_install_engine
rc=$?
if [[ $rc -eq 0 ]]; then _install_engine; fi
exit "$rc"
"""
    result = subprocess.run([bash, "-c", script], env=env, capture_output=True,
                            text=True, timeout=20)
    assert result.returncode == (1 if failure else 0), result.stderr
    calls = log.read_text().splitlines()
    assert calls[0] == "--version"
    assert sum(line.startswith("venv ") for line in calls) == (2 if transient else 1)
    installs = [line for line in calls if line.startswith("pip install")]
    assert all("--python" not in line for line in calls if line.startswith("venv "))
    assert "zdd" in installs[0] and "agent-procutil" in installs[1]
    if failure:
        assert len(installs) == 2
    else:
        assert str(tmp_path / "plugin") in installs[2]
        assert str(tmp_path / "plugin/server") in installs[3]
        assert shlex.split(installs[3]).count(str(tmp_path / "plugin")) == 1
        assert "--index-url https://example.invalid/cuda --reinstall-package torch torch" in installs[4]
        assert len(installs) == 5
        assert (tmp_path / "engine/versions/1.0.0/.install-complete.json").is_file()
        assert (tmp_path / "engine/current-version").read_text().strip() == "1.0.0"
    if failure:
        engine = tmp_path / "engine"
        assert not os.path.lexists(engine / "current-version")
        assert not os.path.lexists(engine / "last-known-good")
        versions = engine / "versions"
        entries = list(versions.iterdir())
        assert {entry.name for entry in entries} <= {".1.0.0.build.lock"}
        assert all(entry.is_file() and not entry.is_symlink() for entry in entries)
        assert not list(versions.rglob(".install-complete.json"))


@pytest.mark.parametrize("ext", ["sh", "ps1"])
def test_shared_pip_retry_preserves_exact_exit(tmp_path: Path, ext: str):
    uv = fake_uv(tmp_path, "print('ordinary-failure')\nraise SystemExit(37)\n")
    if ext == "sh":
        bash = shutil.which("bash")
        if os.name == "nt" or not bash:
            pytest.skip("native POSIX bash is unavailable")
        script = f"""
. '{ENGINE / "installer-engine.sh"}'
_warn() {{ :; }}; sleep() {{ :; }}
{function("_uv_pip_install", "sh")}
UV_COMMAND='{uv}'
_uv_pip_install --python fixture package
exit $?
"""
        result = subprocess.run([bash, "-c", script], env=environment(tmp_path),
                                capture_output=True, text=True, timeout=20)
    else:
        result = run_ps(tmp_path, f"""
$script:UvCommand = '{uv}'
{function("Invoke-IndexUvPipInstall", "ps1")}
Invoke-IndexUvPipInstall --python fixture package
exit $LASTEXITCODE
""")
    assert result.returncode == 37, result.stderr
    assert "ordinary-failure" in result.stdout


@pytest.mark.parametrize("ext", ["sh", "ps1"])
def test_shared_pip_retries_only_transient_failures(tmp_path: Path, ext: str):
    counter = tmp_path / "counter"
    uv = fake_uv(tmp_path, f"""
counter = pathlib.Path({str(counter)!r})
if not counter.exists():
    counter.write_text('1')
    print('SRE module mismatch')
    raise SystemExit(37)
counter.write_text('2')
print('recovered')
""")
    if ext == "sh":
        bash = shutil.which("bash")
        if os.name == "nt" or not bash:
            pytest.skip("native POSIX bash is unavailable")
        script = f"""
. '{ENGINE / "installer-engine.sh"}'
_warn() {{ :; }}; sleep() {{ :; }}
{function("_uv_pip_install", "sh")}
UV_COMMAND='{uv}'
_uv_pip_install --python fixture package
exit $?
"""
        result = subprocess.run([bash, "-c", script], env=environment(tmp_path),
                                capture_output=True, text=True, timeout=20)
    else:
        result = run_ps(tmp_path, f"""
function Start-Sleep {{ param($Seconds) }}
$script:UvCommand = '{uv}'
{function("Invoke-IndexUvPipInstall", "ps1")}
Invoke-IndexUvPipInstall --python fixture package
exit $LASTEXITCODE
""")
    assert result.returncode == 0, result.stderr
    assert counter.read_text().strip() == "2"
    assert "recovered" in result.stdout


def test_powershell_existing_invalid_interpreter_cannot_bypass_rebuild(tmp_path: Path):
    result = run_ps(tmp_path, function("New-IndexVenv", "ps1") + f"""
$env:OS = 'Windows_NT'
$env:SystemDrive = '/'
$script:UvCommand = 'fixture-only-uv'
$Dir = '{tmp_path / "venv"}'
New-Item -ItemType Directory -Path $Dir -Force | Out-Null
$Python = Join-Path $Dir 'python'
Set-Content $Python 'invalid'
Set-Content (Join-Path $Dir 'pyvenv.cfg') 'present'
function Get-SignedBasePython {{ 'fixture-only-python' }}
function Get-AuthenticodeSignature {{ param($Path) [pscustomobject]@{{ Status='Unknown' }} }}
function Test-IndexVenv {{ param($Dir, $Python) return $script:Healthy }}
function Invoke-NativeCapture {{
    param($Command)
    if (Test-Path $Python) {{ throw 'invalid interpreter survived rebuild' }}
    $script:Rebuilt = $true; $script:Healthy = $true
    [pscustomobject]@{{ ExitCode=0; Output='' }}
}}
if (-not (New-IndexVenv -Dir $Dir -Python $Python -PythonCmd 'forbidden')) {{ throw 'failed' }}
if (-not $script:Rebuilt) {{ throw 'existing invalid python bypassed rebuild' }}
""")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("exit_code,healthy", [(0, True), (1, True), (0, False), (1, False)])
def test_signed_result_health_not_exit_controls_recovery(
    tmp_path: Path, exit_code: int, healthy: bool,
):
    result = run_ps(tmp_path, f"""
{function("New-IndexVenv", "ps1")}
$env:OS = 'Windows_NT'
$env:SystemDrive = '/'
$script:UvCommand = 'fixture-only-uv'
$Dir = '{tmp_path / "venv"}'
function Get-SignedBasePython {{ 'fixture-only-python' }}
function Set-Location {{ param($Path) }}
function Test-IndexVenv {{ param($Dir, $Python) return $script:Healthy }}
function Invoke-NativeCapture {{
    param($Command)
    $script:Healthy = ${str(healthy).lower()}
    New-Item -ItemType Directory -Path $Dir -Force | Out-Null
    Set-Content (Join-Path $Dir 'signed-attempt') 'fixture'
    [pscustomobject]@{{ ExitCode = {exit_code}; Output = 'fixture' }}
}}
function Invoke-UvVenvResilient {{
    param($UvCommand, $VenvDir, $Arguments)
    if ($UvCommand -ne 'fixture-only-uv') {{ throw 'escaped fake uv' }}
    if ($Arguments -contains '--python') {{ throw 'changed interpreter selection policy' }}
    if (Test-Path (Join-Path $Dir 'signed-attempt')) {{ throw 'unusable signed attempt retained' }}
    $script:Retried = $true
    $script:Healthy = $true
    [pscustomobject]@{{ ExitCode = 0; Output = '' }}
}}
if (-not (New-IndexVenv -Dir $Dir -Python (Join-Path $Dir 'python') -PythonCmd 'forbidden')) {{ throw 'creation failed' }}
if ([bool]$script:Retried -ne ${str(not healthy).lower()}) {{ throw 'wrong recovery path' }}
""")
    assert result.returncode == 0, result.stderr


def test_windows_cli_preserves_python_fallback_when_uv_acquisition_fails(tmp_path: Path):
    runtime = function("Install-Runtime", "ps1")
    acquisition = "    $script:UvCommand = Get-IndexUv" + runtime.split(
        "    $script:UvCommand = Get-IndexUv", 1
    )[1].split("    $buildMutex", 1)[0]
    result = run_ps(tmp_path, function("New-IndexVenv", "ps1") + f"""
$env:OS = 'Installer_Test'
$InstallDir = '{tmp_path / "runtime"}'
$Dir = Join-Path $InstallDir 'venv'
$Python = Join-Path $Dir 'python'
function Ensure-Uv {{ param($InstallRoot) return $null }}
function Invoke-UvVenvResilient {{ throw 'missing uv must not be invoked' }}
function Test-IndexVenv {{
    param($Dir, $Python)
    return ((Test-Path $Python) -and (Test-Path (Join-Path $Dir 'pyvenv.cfg')))
}}
function fixture_python {{
    New-Item -ItemType Directory -Path $Dir -Force | Out-Null
    Set-Content $Python 'fixture'
    Set-Content (Join-Path $Dir 'pyvenv.cfg') 'fixture'
    $script:PythonFallbackUsed=$true
    $global:LASTEXITCODE=0
}}
{acquisition}
if (-not (New-IndexVenv -Dir $Dir -Python $Python -PythonCmd 'fixture_python')) {{ throw 'Python fallback failed' }}
if (-not $script:PythonFallbackUsed) {{ throw 'Python fallback was bypassed' }}
""")
    assert result.returncode == 0, result.stderr


def test_windows_engine_retains_uv_policy_without_signed_rebuild(tmp_path: Path):
    assert "-PreferSignedPython $false" in function("Install-Engine", "ps1")
    result = run_ps(tmp_path, function("New-IndexVenv", "ps1") + f"""
$env:OS = 'Windows_NT'
$env:SystemDrive = '/'
$Dir = '{tmp_path / "engine"}'
$script:UvCommand = 'fixture-only-uv'
function Get-SignedBasePython {{ throw 'engine policy must not discover signed Python' }}
function Get-AuthenticodeSignature {{ throw 'engine policy must not replace a healthy interpreter' }}
function Set-Location {{ param($Path) }}
function Test-IndexVenv {{ param($Dir, $Python) return $script:Healthy }}
function Invoke-UvVenvResilient {{
    param($UvCommand, $VenvDir, $Arguments)
    if ($UvCommand -ne 'fixture-only-uv' -or $Arguments -contains '--python') {{ throw 'changed uv policy' }}
    $script:Healthy=$true
    $script:UvCalls=1+$script:UvCalls
    [pscustomobject]@{{ ExitCode=0; Output='' }}
}}
function Invoke-NativeCapture {{ throw 'engine unexpectedly used base Python' }}
if (-not (New-IndexVenv -Dir $Dir -Python (Join-Path $Dir 'python') -PythonCmd 'forbidden' -PreferSignedPython $false)) {{ throw 'engine build failed' }}
if (-not (New-IndexVenv -Dir $Dir -Python (Join-Path $Dir 'python') -PythonCmd 'forbidden' -PreferSignedPython $false)) {{ throw 'healthy engine reuse failed' }}
if ($script:UvCalls -ne 1) {{ throw 'healthy durable engine was rebuilt' }}
""")
    assert result.returncode == 0, result.stderr


def snapshot_source(tmp_path: Path) -> Path:
    # Copy only into a disposable tree; the original source is never mutated.
    source = tmp_path / "source/plugins/agent-index"
    (source / "scripts").mkdir(parents=True)
    for name in ("pyproject.toml", "README.md", "plugin.json"):
        shutil.copy2(PLUGIN / name, source / name)
    for ext in ("sh", "ps1"):
        shutil.copy2(PLUGIN / "scripts" / f"install.{ext}", source / "scripts" / f"install.{ext}")
    for name in ("installer_dependency_policy.py", "installer_dependency_config.py"):
        shutil.copy2(ENGINE / name, source / "scripts" / name)
    (source / "server").mkdir()
    shutil.copy2(PLUGIN / "server/pyproject.toml", source / "server/pyproject.toml")
    for library in ("zdd", "agent-procutil", "dropin-registry"):
        origin = (PLUGIN if library == "dropin-registry" else REPO) / "libs" / library
        destination = (source if library == "dropin-registry" else source.parents[1]) / "libs" / library
        destination.mkdir(parents=True)
        shutil.copy2(origin / "pyproject.toml", destination / "pyproject.toml")
        (destination / "fixture.py").write_text("identity = 1\n", encoding="utf-8")
    shutil.copytree(ENGINE, source.parents[1] / "libs/installer-engine")
    (source / "Case.txt").write_text("upper", encoding="utf-8")
    (source / "case.txt").write_text("lower", encoding="utf-8")
    return source


def snapshot_functions() -> str:
    names = ("Get-PayloadHash", "Resolve-VendoredLib", "Publish-FileAtomically",
             "Enter-IndexStampLock", "Materialize-IndexSnapshot", "New-IndexSnapshot",
             "Invoke-Stamp")
    return "\n".join(function(name, "ps1") for name in names)


@pytest.mark.parametrize("library,filename", [
    ("installer-engine", "installer-engine.sh"),
    ("zdd", "fixture.py"),
])
def test_powershell_snapshot_dependency_content_changes_identity(tmp_path: Path, library, filename):
    source = snapshot_source(tmp_path)
    changed = source.parents[1] / "libs" / library / filename
    result = run_ps(tmp_path, snapshot_functions() + f"""
$PluginDir = '{source}'
$PSScriptRoot = '{source / "scripts"}'
$InstallDir = '{tmp_path / "runtime"}'
$first = New-IndexSnapshot
Add-Content '{changed}' '# fixture edit'
$changed = New-IndexSnapshot
if ($first -ceq $changed) {{ throw 'dependency content omitted from identity' }}
""")
    assert result.returncode == 0, result.stderr


def test_powershell_snapshot_is_immutable_self_contained_and_ordered(tmp_path: Path):
    source = snapshot_source(tmp_path)
    setup = snapshot_functions() + f"""
$PluginDir = '{source}'
$PSScriptRoot = '{source / "scripts"}'
$InstallDir = '{tmp_path / "runtime"}'
$LocalBin = '{tmp_path / "bin"}'
$SrcVersion = '1.0.0'
$probePayload = $PluginDir
function Test-VersionLt {{ param($A, $B) return $false }}
function Deploy-SetupGatedBinstub {{
    param($PayloadRoot)
    if ([IO.File]::ReadAllText((Join-Path $InstallDir 'payload-dir')) -cne $PayloadRoot) {{ throw 'wrong payload marker' }}
    $script:Launchers = 1 + $script:Launchers
}}
"""
    result = run_ps(tmp_path, setup + """
Invoke-Stamp
$first = [IO.File]::ReadAllText((Join-Path $InstallDir 'payload-dir'))
Invoke-Stamp
if ([IO.File]::ReadAllText((Join-Path $InstallDir 'payload-dir')) -cne $first) { throw 'unchanged stamp drift' }
Set-Content -LiteralPath (Join-Path $PluginDir 'case.txt') 'changed'
Invoke-Stamp
$newer = [IO.File]::ReadAllText((Join-Path $InstallDir 'payload-dir'))
if ($newer -ceq $first) { throw 'case-distinct content omitted' }
$first
$newer
""")
    assert result.returncode == 0, result.stderr
    snapshots = [Path(line) for line in result.stdout.splitlines() if line.startswith(str(tmp_path))]
    assert len(snapshots) == 2
    result = run_ps(tmp_path, setup + f"""
$first = '{snapshots[0]}'
$newer = '{snapshots[1]}'
$before = $script:Launchers
function New-IndexSnapshot {{ return $first }}
Invoke-Stamp
if ($script:Launchers -ne $before) {{ throw 'older snapshot overwrote launchers' }}
if ([IO.File]::ReadAllText((Join-Path $InstallDir 'payload-dir')) -cne $newer) {{ throw 'older stamp replaced markers' }}
""")
    assert result.returncode == 0, result.stderr
    for snapshot in snapshots:
        for ext in ("sh", "ps1"):
            assert (snapshot / "scripts" / f"installer-engine.{ext}").read_bytes() == (
                ENGINE / f"installer-engine.{ext}"
            ).read_bytes()
        project = (snapshot / "pyproject.toml").read_text()
        assert "../../libs/" not in project and "editable = true" not in project
        for library in ("zdd", "agent-procutil", "dropin-registry"):
            assert (snapshot / "libs" / library / "pyproject.toml").is_file()
        assert (snapshot / "server/pyproject.toml").is_file()
    source.parents[1].rename(tmp_path / "unavailable-source")
    latest = snapshots[1]
    # Exercise the snapshot-local shared package mechanics with explicit fake uv.
    # The stub validates the complete dependency closure without installing it.
    uv = fake_uv(tmp_path, f"""
root = pathlib.Path({str(latest)!r})
for name in ('zdd', 'agent-procutil', 'dropin-registry'):
    assert (root / 'libs' / name / 'pyproject.toml').is_file()
assert (root / 'server/pyproject.toml').is_file()
assert '--exclude-newer' in sys.argv and '--reinstall' in sys.argv
""")
    result = run_ps(tmp_path, f"""
. '{latest / "scripts/installer-engine.ps1"}'
$result = Invoke-UvPipInstallResilient -UvCommand '{uv}' -Arguments @('--python', 'fixture', '{latest}')
if ($result.ExitCode -ne 0) {{ Write-Host $result.Output }}
exit $result.ExitCode
""")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("staged", [False, True])
def test_manifest_fields_use_shared_writer(tmp_path: Path, staged: bool):
    original = tmp_path / ".copilot/installed-plugins/example/agent-index" if staged else PLUGIN
    result = run_ps(tmp_path, function("Write-Manifest", "ps1") + f"""
$InstallDir = '{tmp_path}'
$PluginDir = '{PLUGIN}'
$LinkDir = '{tmp_path / "versions/1.0.0"}'
$env:COPILOT_PLUGIN_STAGED_FROM = '{original if staged else ""}'
function Get-SourceKind {{ param($PluginPath) 'local' }}
function Get-GitInfo {{ param($Path) @{{ commit='abc'; branch='dev'; dirty=$false }} }}
Write-Manifest
""")
    assert result.returncode == 0, result.stderr
    manifest = json.loads((tmp_path / "deploy-manifest.json").read_text(encoding="utf-8-sig"))
    assert manifest["schema_version"] == 3
    assert manifest["service"] == manifest["source"]["plugin"] == "agent-index"
    assert manifest["source"]["kind"] == ("marketplace" if staged else "local")
    assert manifest["source"]["commit"] == (None if staged else "abc")
    assert manifest["source"]["path"] == str(original).replace("\\", "/")
    assert manifest["venv"] == str(tmp_path / "versions/1.0.0").replace("\\", "/")

def test_snapshot_provision_keeps_snapshot_marker_without_original_source(tmp_path: Path):
    snapshot = tmp_path / "runtime/snapshots/1.0.0-identity"
    snapshot.mkdir(parents=True)
    result = run_ps(tmp_path,
                    function("Publish-FileAtomically", "ps1") +
                    function("Deploy-SetupGatedBinstub", "ps1") + f"""
$InstallDir = '{snapshot.parents[1]}'
$LocalBin = '{tmp_path / "bin"}'
$PluginDir = '{snapshot}'
$probePayload = '{tmp_path / "missing-authoring-source"}'
Deploy-SetupGatedBinstub
Deploy-SetupGatedBinstub
if ([IO.File]::ReadAllText((Join-Path $InstallDir 'payload-dir')) -cne $PluginDir) {{
    throw 'provision rewrote snapshot pointer to unavailable origin'
}}
""")
    assert result.returncode == 0, result.stderr


def test_posix_cli_build_uses_shared_venv_and_keeps_engine_lazy(tmp_path: Path):
    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        pytest.skip("native POSIX bash is unavailable")
    log = tmp_path / "calls"
    posix_venv_uv(tmp_path)
    plugin = tmp_path / "plugin"
    for library in ("zdd", "agent-procutil", "dropin-registry"):
        directory = plugin / "libs" / library
        directory.mkdir(parents=True)
        (directory / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (plugin / "src").mkdir()
    (plugin / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n', encoding="utf-8")
    (plugin / "scripts").mkdir()
    for name in ("session-stamp.py", "stamp_paths.py", "stamp_repair.py"):
        shutil.copy2(PLUGIN / "scripts" / name, plugin / "scripts" / name)
    construction_bundle(plugin / "scripts")
    env = environment(tmp_path)
    env["PATH"] = str(tmp_path) + os.pathsep + env["PATH"]
    functions = "\n".join(function(name, "sh") for name in (
        "_ensure_uv", "_uv_pip_install", "_resolve_vendored_lib",
        "_bootstrap_python", "_versioned_slot_clean", "_versioned_mark_complete",
        "_test_index_venv", "_new_index_venv", "_with_index_build_lock", "_with_index_advisory_lock",
        "_with_index_publication_lock", "_index_publication_fresh", "_version_lt", "_install_server_venv", "_ensure_runtime",
    ))
    result = subprocess.run([bash, "-c", f"""
set -uo pipefail
. '{ENGINE / "installer-engine.sh"}'
_ok() {{ :; }}; _skip() {{ :; }}; _step() {{ :; }}; _warn() {{ :; }}
_fail() {{ echo "$*" >&2; }}
_stop() {{ echo forbidden-stop >&2; exit 97; }}
_install_engine() {{ echo forbidden-engine >&2; exit 97; }}
curl() {{ echo forbidden-download >&2; exit 97; }}
wget() {{ echo forbidden-download >&2; exit 97; }}
_find_python() {{ echo '{tmp_path / "forbidden-base-python"}'; }}
_ensure_uv_index() {{ :; }}
_activation_role() {{ echo client; }}
_runtime_origin_under() {{ return 0; }}
_payload_hash() {{ printf '%s' aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; }}
_versioned_activate() {{ :; }}
_versioned_current() {{ :; }}
_versioned_gc() {{ :; }}
deploy_binstub() {{ :; }}
_write_manifest() {{ :; }}
INSTALL_DIR='{tmp_path / "runtime"}'
LINK_DIR='{tmp_path / "absent-link"}'
LOCAL_BIN='{tmp_path / "bin"}'
PLUGIN_DIR='{plugin}'
PKG_SRC_DIR="$PLUGIN_DIR/src"
SCRIPT_DIR="$PLUGIN_DIR/scripts"
VERSIONED_RUNTIME=1
SRC_VERSION=1.0.0
VENV_DIR="$INSTALL_DIR/versions/$SRC_VERSION"
VENV_PYTHON="$VENV_DIR/bin/python"
LINK_PYTHON="$VENV_PYTHON"
_resolve_zdd() {{ _resolve_vendored_lib zdd; }}
{functions}
_bootstrap_python() {{ echo '{sys._base_executable}'; }}
_uv_pip_install() {{ "$UV_COMMAND" pip install "$@"; }}
_ensure_runtime
"""], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    calls = log.read_text().splitlines()
    assert calls[0] == "--version"
    assert sum(line.startswith("venv ") for line in calls) == 1
    installs = [line for line in calls if line.startswith("pip install")]
    assert len(installs) == 4
    assert "zdd" in installs[0] and "agent-procutil" in installs[1]
    assert "dropin-registry" in installs[2]
    assert installs[3].endswith(str(plugin))
    assert all("torch" not in line and "[store,server]" not in line for line in installs)
    assert not (tmp_path / "runtime/engine").exists()
    assert (tmp_path / "runtime/versions/1.0.0/.install-complete.json").is_file()


def test_required_server_package_failure_refuses_activation_under_errexit(tmp_path: Path):
    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        pytest.skip("native POSIX bash is unavailable")
    server = tmp_path / "runtime/server"
    (server / "bin").mkdir(parents=True)
    (server / "pyvenv.cfg").write_text("home = fixture\n", encoding="utf-8")
    python = server / "bin/python3"
    python.write_text(FAKE_PYTHON, encoding="utf-8")
    python.chmod(0o755)
    plugin = tmp_path / "plugin"
    for library in ("zdd", "agent-procutil", "dropin-registry"):
        directory = plugin / "libs" / library
        directory.mkdir(parents=True)
        (directory / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    uv = tmp_path / "uv"
    uv.write_text(
        "#!/usr/bin/env bash\n"
        "for arg in \"$@\"; do\n"
        "  if [[ \"$arg\" == *'[store,server]' ]]; then\n"
        "    echo fixture-package-failure; exit 37\n"
        "  fi\n"
        "done\n"
        "exit 0\n",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    env = environment(tmp_path)
    env["PATH"] = str(tmp_path) + os.pathsep + env["PATH"]
    result = subprocess.run([bash, "-c", f"""
set -euo pipefail
_skip() {{ :; }}; _ok() {{ :; }}; _warn() {{ echo "$*" >&2; }}
{function("_resolve_vendored_lib", "sh")}
{function("_test_index_venv", "sh")}
{function("_new_index_venv", "sh")}
_uv_pip_install() {{ '{uv}' pip install "$@"; }}
VENV_DIR='{server.parent}'
PLUGIN_DIR='{plugin}'
{function("_install_server_venv", "sh")}
_install_server_venv host forbidden
echo forbidden-activation
"""], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 1, result.stderr
    assert "forbidden-activation" not in result.stdout
    assert "fixture-package-failure" in result.stderr
    assert "Server venv package install failed" in result.stderr
