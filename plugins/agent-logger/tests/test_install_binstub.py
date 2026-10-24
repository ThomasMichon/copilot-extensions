"""POSIX installer regressions for the agent-logger binstub."""

from __future__ import annotations

import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import sysconfig
import venv
from importlib import metadata
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = _PLUGIN_ROOT / "scripts" / "install.sh"
_INSTALL_PS1 = _PLUGIN_ROOT / "scripts" / "install.ps1"
_COMMANDS = (
    "agent-logger",
    "collate-session",
    "read-session-digest",
    "prepare-session-log",
    "ramp-up-session",
    "session-sync",
)
_HARNESS_TIMEOUT_SECONDS = 20


def _offline_uv(tmp_path: Path, env: dict[str, str]) -> Path:
    """Emulate UV's package effects offline, not runtime construction or health."""
    fixture = tmp_path / "offline-uv"
    fixture.mkdir()
    for name in ("PyYAML",):
        distribution = metadata.distribution(name)
        target = fixture / name
        for file in distribution.files or ():
            if ".." in file.parts or file.suffix == ".pyc":
                continue
            source = Path(distribution.locate_file(file))
            if source.is_file():
                destination = target / file
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
        assert any(target.rglob("METADATA")), name
    # The double copies local source instead of building wheels; setuptools is
    # build-only here, while every runtime import still uses real package code.
    build_info = fixture / "setuptools" / "setuptools-83.0.0.dist-info"
    build_info.mkdir(parents=True)
    (build_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: setuptools\nVersion: 83.0.0\n",
        encoding="utf-8",
    )
    calls = fixture / "installs.jsonl"
    driver = fixture / "driver.py"
    driver.write_text(
        f"""#!{sys._base_executable}
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import venv
from datetime import datetime, timedelta, timezone
from pathlib import Path

fixture = Path({str(fixture)!r})
root = Path({str(tmp_path)!r})
args = sys.argv[1:]
if args == ['--version']:
    print('uv 0.12.5 (offline test fixture)')
    raise SystemExit(0)
if args[:1] == ['venv']:
    target = Path(args[1])
    assert target.resolve().is_relative_to(root.resolve())
    venv.EnvBuilder(with_pip=False, symlinks=False).create(target)
    raise SystemExit(0)
assert args[:2] == ['pip', 'install'], args
if '--show-settings' in args:
    assert not any(a == '--quiet' or re.fullmatch('-q+', a) for a in args)
    print('''PipInstallSettings {{ settings: PipSettings {{
    index_locations: IndexLocations {{ indexes: [], flat_index: [], }},
    exclude_newer: ExcludeNewer {{ global: None, package: ExcludeNewerPackage({{}}), }},
}}, }}''')
    raise SystemExit(0)
assert '--reinstall' in args and '--quiet' in args
assert '--no-deps' not in args and '--no-build-isolation' not in args
cutoff = datetime.fromisoformat(args[args.index('--exclude-newer') + 1].replace('Z', '+00:00'))
assert cutoff <= datetime.now(timezone.utc) - timedelta(days=7)
python = Path(args[args.index('--python') + 1])
assert python.resolve().is_relative_to(root.resolve())
result = subprocess.run(
    [str(python), '-I', '-c', 'import sysconfig; print(sysconfig.get_paths()["purelib"])'],
    capture_output=True, text=True, check=True, timeout=20,
    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
)
site = Path(result.stdout.strip())
assert site.resolve().is_relative_to(root.resolve())

def install_local(project, installed):
    project = project.resolve()
    assert project.resolve().is_relative_to(root.resolve())
    if project in installed:
        return
    installed.add(project)
    data = tomllib.loads((project / 'pyproject.toml').read_text())
    metadata = data['project']
    local_sources = data.get('tool', {{}}).get('uv', {{}}).get('sources', {{}})
    for requirement in metadata.get('dependencies', []):
        match = re.match(r'[A-Za-z0-9][A-Za-z0-9._-]*', requirement)
        assert match is not None, requirement
        dependency = local_sources.get(match.group())
        if dependency is not None:
            assert isinstance(dependency, dict) and isinstance(dependency.get('path'), str)
            install_local(project / dependency['path'], installed)
    name = metadata['name']
    version = metadata['version']
    source = project / 'src'
    assert source.is_dir()
    info = site / (name.replace('-', '_') + '-' + version + '.dist-info')
    info.mkdir(exist_ok=True)
    (info / 'METADATA').write_text('Metadata-Version: 2.1\\nName: ' + name + '\\nVersion: ' + version + '\\n')
    shutil.copytree(source, site, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
    if name == 'agent-logger' and os.name != 'nt':
        launcher = python.parent / 'agent-logger'
        launcher.write_text('#!' + str(python) + '\\nfrom agent_logger.__main__ import main\\nraise SystemExit(main())\\n')
        launcher.chmod(0o755)

if 'setuptools>=83.0.0' in args:
    shutil.copytree(fixture / 'setuptools', site, dirs_exist_ok=True)
elif 'pyyaml>=6.0' in args:
    shutil.copytree(fixture / 'PyYAML', site, dirs_exist_ok=True)
else:
    sources = [Path(a) for a in args if (Path(a) / 'pyproject.toml').is_file()]
    assert len(sources) == 1, args
    install_local(sources[0], set())
with (fixture / 'installs.jsonl').open('a') as log:
    log.write(json.dumps(args) + '\\n')
""",
        encoding="utf-8",
    )
    if os.name == "nt":
        # A batch wrapper would interpret `>` in version requirements as
        # redirection before Python receives the install/probe arguments.
        command = fixture / "uv.exe"
        launcher = fixture / "launcher.cs"
        launcher.write_text(
            r"""
using System;
using System.Diagnostics;
using System.Linq;
class Launcher {
    static string Quote(string value) {
        return "\"" + value.Replace("\"", "\\\"") + "\"";
    }
    static int Main(string[] args) {
        var info = new ProcessStartInfo(@"__PYTHON__",
            String.Join(" ", new[] { @"__DRIVER__" }.Concat(args).Select(Quote)));
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        info.RedirectStandardOutput = true;
        info.RedirectStandardError = true;
        using (var process = Process.Start(info)) {
            var output = process.StandardOutput.ReadToEndAsync();
            var error = process.StandardError.ReadToEndAsync();
            process.WaitForExit();
            Console.Out.Write(output.Result);
            Console.Error.Write(error.Result);
            return process.ExitCode;
        }
    }
}
""".replace("__PYTHON__", sys._base_executable.replace('"', '""'))
            .replace("__DRIVER__", str(driver).replace('"', '""')),
            encoding="utf-8",
        )
        compiler_shell = shutil.which("powershell.exe")
        assert compiler_shell is not None
        subprocess.run(
            [compiler_shell, "-NoProfile", "-Command",
             f"Add-Type -Path '{launcher}' -OutputAssembly '{command}' "
             "-OutputType ConsoleApplication"],
            env=env, capture_output=True, text=True, check=True, timeout=20,
        )
    else:
        command = fixture / "uv"
        shutil.copyfile(driver, command)
        command.chmod(0o755)
    env["PATH"] = f"{fixture}{os.pathsep}{env['PATH']}"
    if os.name == "nt":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        selected = subprocess.run(
            [shell, "-NoProfile", "-Command",
             "(Get-Command uv -CommandType Application | Select-Object -First 1).Source"],
            env=env, capture_output=True, text=True, check=True, timeout=20,
        )
        assert Path(selected.stdout.strip()) == command, "installer must select offline UV"
    probe = subprocess.run(
        [str(command), "pip", "install", "--show-settings", "setuptools>=83.0.0"],
        env=env, capture_output=True, text=True, check=True, timeout=20,
    )
    assert probe.stdout.startswith("PipInstallSettings {")
    assert not calls.exists()
    return calls


def _assert_complete_runtime(home: Path) -> None:
    root = home / ".agent-logger"
    version = (root / "current-version").read_text(encoding="utf-8").strip()
    slot = root / "versions" / version
    marker = json.loads((slot / ".install-complete.json").read_text(encoding="utf-8"))
    assert marker["version"] == version
    assert re.fullmatch(r"[0-9a-f]{64}", marker["payload_hash"])
    assert (slot / "pyvenv.cfg").is_file()
    assert not (root / ".venv").exists(), "runtime selection is marker-only"
    python = slot / "Scripts" / "python.exe" if os.name == "nt" else slot / "bin" / "python"
    assert python.is_file()


def _isolated_install_env(home: Path) -> dict[str, str]:
    """Mirror the runner's containment roots for direct installer subprocesses."""
    env = os.environ.copy()
    roots = {
        "HOME": home,
        "USERPROFILE": home,
        "APPDATA": home / "AppData" / "Roaming",
        "LOCALAPPDATA": home / "AppData" / "Local",
        "PROGRAMDATA": home / "ProgramData",
        "XDG_CONFIG_HOME": home / ".config",
        "XDG_CACHE_HOME": home / ".cache",
        "XDG_DATA_HOME": home / ".local" / "share",
        "XDG_STATE_HOME": home / ".local" / "state",
        "XDG_RUNTIME_DIR": home / "run",
        "TEMP": home / "tmp",
        "TMP": home / "tmp",
        "TMPDIR": home / "tmp",
        "COPILOT_HOME": home / ".copilot",
        "AGENT_HOME": home,
        "AGENT_LOGGER_HOME": home / ".agent-logger",
    }
    for path in roots.values():
        path.mkdir(parents=True, exist_ok=True)
    env.update({name: str(path) for name, path in roots.items()})
    env["COPILOT_PLUGIN_INSTALL_STAGED"] = "1"
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        env.pop(name, None)
    return env


def _stage_payload(tmp_path: Path) -> Path:
    payload = tmp_path / "plugins" / "agent-logger"
    payload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        _PLUGIN_ROOT,
        payload,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            "tests",
        ),
    )
    staged_libs = tmp_path / "libs"
    staged_libs.mkdir(parents=True, exist_ok=True)
    for lib in (
        "installer-engine",
        "versioned-runtime",
        "config-migrate",
        "agent-procutil",
        "dropin-registry",
        "plugin-resolve",
        "single-instance-lease",
        "update-driver",
        "plugin-activation",
    ):
        shutil.copytree(_PLUGIN_ROOT.parents[1] / "libs" / lib, staged_libs / lib)
    return payload


def _stage_release_payload(tmp_path: Path) -> Path:
    publisher = _stage_payload(tmp_path)
    repository = _PLUGIN_ROOT.parents[1]
    roster_path = Path(".github") / "plugin" / "marketplace.json"
    roster = json.loads((repository / roster_path).read_text(encoding="utf-8"))
    roster["plugins"] = [
        entry for entry in roster["plugins"] if entry["name"] == "agent-logger"
    ]
    assert len(roster["plugins"]) == 1
    roster["plugins"][0]["source"] = "release/agent-logger"
    staged_roster = tmp_path / roster_path
    staged_roster.parent.mkdir(parents=True)
    staged_roster.write_text(json.dumps(roster), encoding="utf-8")
    payload = tmp_path / "release" / "agent-logger"
    shutil.copytree(publisher, payload)
    # A shipped snapshot must own its library graph, not retain DEV references
    # that escape to ../../libs after its original publisher disappears.
    previous_path = sys.path[:]
    try:
        materializer = runpy.run_path(str(repository / "tools" / "materialize_main.py"))
        log = materializer["materialize_uv_editable_ref_into"](
            source_consumer_dir=publisher,
            dest_consumer_dir=payload,
            canonical_root=tmp_path,
            dest_root=tmp_path,
        )
        log += materializer["mie"].materialize_ref_into(
            source_consumer_dir=publisher,
            dest_consumer_dir=payload,
            canonical_root=tmp_path,
            dest_root=tmp_path,
        )
    finally:
        sys.path[:] = previous_path
    assert not any(line.startswith("SKIP ") for line in log), log
    source_paths = re.findall(
        r'path\s*=\s*"([^"]+)"',
        (payload / "pyproject.toml").read_text(encoding="utf-8"),
    )
    assert source_paths
    for source in source_paths:
        library = (payload / source).resolve()
        assert library.is_relative_to(payload.resolve())
        assert (library / "pyproject.toml").is_file()
    return payload


@pytest.mark.guard
def test_release_payload_materializes_activation_closure(tmp_path: Path) -> None:
    payload = _stage_release_payload(tmp_path)
    lease = payload / "libs" / "single-instance-lease"
    assert (lease / "pyproject.toml").is_file()
    assert (lease / "src" / "single_instance_lease" / "operation.py").is_file()
    driver = payload / "libs" / "update-driver"
    assert (driver / "src/update_driver/first_use.py").is_file()
    assert (driver / "scripts/first-use.ps1").is_file()
    assert (driver / "scripts/first-use.sh").is_file()


@pytest.mark.guard
def test_offline_uv_installs_transitive_local_sources(tmp_path: Path) -> None:
    for lib in (
        "plugin-activation", "dropin-registry", "plugin-resolve", "single-instance-lease",
    ):
        shutil.copytree(
            _PLUGIN_ROOT.parents[1] / "libs" / lib,
            tmp_path / "libs" / lib,
            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "tests"),
        )
    env = _isolated_install_env(tmp_path / "home")
    _offline_uv(tmp_path, env)
    uv = shutil.which("uv", path=env["PATH"])
    assert uv is not None
    runtime = tmp_path / "runtime"
    venv.EnvBuilder(with_pip=False, symlinks=False).create(runtime)
    site = Path(sysconfig.get_path(
        "purelib", vars={"base": str(runtime), "platbase": str(runtime)},
    ))
    shutil.copytree(tmp_path / "offline-uv" / "PyYAML", site, dirs_exist_ok=True)
    python = (
        runtime / "Scripts" / "python.exe" if os.name == "nt" else runtime / "bin" / "python"
    )
    commands = [
        [
            uv, "pip", "install", "--python", str(python), "--reinstall", "--quiet",
            "--exclude-newer", "2000-01-01T00:00:00Z",
            str(tmp_path / "libs" / "plugin-activation"),
        ],
        [
            str(python), "-I", "-c",
            "import json, plugin_activation, single_instance_lease;"
            "print(json.dumps([plugin_activation.__file__,"
            "plugin_activation.preservation.__file__, single_instance_lease.__file__,"
            "single_instance_lease.operation.__file__]))",
        ],
    ]
    for command in commands:
        result = subprocess.run(
            command, env=env, capture_output=True, text=True, timeout=10,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    assert all(
        Path(module).is_relative_to(runtime) for module in json.loads(result.stdout)
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer behavior")
# Regression (coverage-guided-ci's full-matrix local validation pass,
# 2026-10-04): same class of gap -- two sequential timeout=30 subprocess
# calls mean a worst case near 60s, past the 30s blanket default.
@pytest.mark.timeout(90)
def test_stamp_replaces_dangling_legacy_binstub(tmp_path: Path) -> None:
    payload = _stage_payload(tmp_path)
    home = tmp_path / "home"
    env = _isolated_install_env(home)
    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    binstub = local_bin / "agent-logger"
    binstub.symlink_to(home / ".agent-logger" / ".venv" / "bin" / "agent-logger")
    result = subprocess.run(
        ["bash", str(payload / "scripts" / "install.sh"), "stamp"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert binstub.is_file()
    assert not binstub.is_symlink()
    assert "agent-logger binstub -- self-provisioning" in binstub.read_text(
        encoding="utf-8"
    )
    assert "stamped-version: No such file or directory" not in result.stderr
    for command in _COMMANDS:
        command_path = local_bin / command
        assert command_path.is_file(), command
        assert os.access(command_path, os.X_OK), command
    auxiliary = (local_bin / "collate-session").read_text(encoding="utf-8")
    assert 'exec "$_shim" "$@"' in auxiliary
    assert "command -v collate-session" not in auxiliary
    payload_dir = Path(
        (home / ".agent-logger" / "payload-dir").read_text(encoding="utf-8").strip()
    )
    assert payload_dir.is_dir()
    assert payload_dir != _PLUGIN_ROOT
    assert (payload_dir / "bin" / "collate-session").is_file()
    shutil.rmtree(payload)
    shadow_bin = tmp_path / "shadow-bin"
    shadow_bin.mkdir()
    shadow = shadow_bin / "collate-session"
    shadow.write_text("#!/bin/sh\nexit 91\n", encoding="utf-8")
    shadow.chmod(0o755)
    env["AGENT_LOGGER_NO_SELFPROVISION"] = "1"
    env["PATH"] = f"{shadow_bin}{os.pathsep}{env.get('PATH', '')}"
    delegated = subprocess.run(
        [str(local_bin / "collate-session"), "--example-argument"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert delegated.returncode == 1, delegated.stdout + delegated.stderr
    assert "runtime not provisioned" in delegated.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows installer behavior")
# Regression (coverage-guided-ci's full-matrix local validation pass,
# 2026-10-04): same class of gap -- two sequential subprocess calls
# (timeout=60, timeout=30) mean a worst case near 90s, past the 30s
# blanket default.
@pytest.mark.timeout(120)
def test_windows_stamp_publishes_complete_command_family(tmp_path: Path) -> None:
    payload = _stage_payload(tmp_path)
    home = tmp_path / "home"
    env = _isolated_install_env(home)
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    assert powershell is not None
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(payload / "scripts" / "install.ps1"),
            "stamp",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    local_bin = home / ".local" / "bin"
    for command in _COMMANDS:
        assert (local_bin / f"{command}.ps1").is_file(), command
        assert (local_bin / f"{command}.cmd").is_file(), command
    auxiliary = (local_bin / "collate-session.ps1").read_text(encoding="utf-8")
    assert "COPILOT_PLUGIN_ROOT" in auxiliary
    assert r"bin\$($_command).ps1" in auxiliary
    shutil.rmtree(payload)
    env["AGENT_LOGGER_NO_SELFPROVISION"] = "1"
    delegated = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(local_bin / "collate-session.ps1"),
            "--example-argument",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert delegated.returncode == 1, delegated.stdout + delegated.stderr
    assert "runtime not provisioned" in delegated.stderr


def test_installers_preserve_payload_delegating_auxiliary_wrappers() -> None:
    install_ps1 = _INSTALL_PS1.read_text(encoding="utf-8")
    install_sh = _INSTALL_SH.read_text(encoding="utf-8")

    assert "function Write-Binstubs" in install_ps1
    assert "Deploy-AuxiliaryCompatibilityBinstubs" in install_ps1.split(
        "function Write-Binstubs", 1
    )[1].split("function Install-Package", 1)[0]
    install_package = install_sh.split("install_package() {", 1)[1].split(
        "write_units() {", 1
    )[0]
    assert "deploy_auxiliary_compatibility_binstubs" in install_package
    assert 'ln -sf "${LINK_DIR}/bin/${name}"' not in install_package
    assert "publish_payload_snapshot" in install_package
    assert "Publish-PayloadSnapshot | Out-Null" in install_ps1.split(
        "function Install-Package", 1
    )[1].split("function Register-SyncTask", 1)[0]


def test_windows_update_rebinds_existing_sync_task_runtime() -> None:
    install_ps1 = _INSTALL_PS1.read_text(encoding="utf-8")
    update_binding = install_ps1.split(
        "function Update-SyncTaskBinding", 1
    )[1].split("function Deploy-SelfProvisioningBinstub", 1)[0]
    update_action = install_ps1.split("'update' {", 1)[1].split(
        "'uninstall' {", 1
    )[0]

    assert "Set-ScheduledTask" in update_binding
    assert "$action = New-SyncTaskAction" in update_binding
    assert "-Action $action" in update_binding
    assert "no provisioned runtime" in update_binding
    assert "-Trigger" not in update_binding
    assert "Update-SyncTaskBinding" in update_action


def test_windows_sync_task_writes_wrap_both_register_and_update_branches() -> None:
    """Both Register-SyncTask branches (existing-task update AND new-task
    creation), not just Update-SyncTaskBinding, must downgrade an Access
    Denied failure to a warning instead of aborting the install (a stale
    admin-only task ACL can just as easily block *creation* on a machine that
    has no task yet, e.g. after `schtasks /Delete`)."""
    install_ps1 = _INSTALL_PS1.read_text(encoding="utf-8")
    register_sync_task = install_ps1.split(
        "function Register-SyncTask", 1
    )[1].split("function Test-IsAccessDenied", 1)[0]

    assert register_sync_task.count("try {") == 2
    assert register_sync_task.count("Write-TaskAccessDeniedWarning") == 2
    assert "Write-TaskAccessDeniedWarning $_ 'update'" in register_sync_task
    assert "Write-TaskAccessDeniedWarning $_ 'register'" in register_sync_task


def _extract_ps1_functions(*names: str) -> str:
    install_ps1 = _INSTALL_PS1.read_text(encoding="utf-8")
    chunks = []
    for name in names:
        rest = install_ps1.split(f"function {name}", 1)[1]
        # Each of these functions is closed by a `}` at column 0 (the file's
        # top-level function-closing convention).
        body = rest.split("\n}\n", 1)[0]
        chunks.append(f"function {name}{body}\n}}")
    return "\n\n".join(chunks)


@pytest.mark.guard
@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
def test_windows_task_access_denied_classifier_downgrades_and_rethrows(
    tmp_path: Path, shell: str
) -> None:
    """Regression for both catch outcomes of the stale-ACL handling: an
    Access Denied error is downgraded to a warning (install continues), while
    any other exception still propagates (install still fails loudly). Runs
    under both Windows PowerShell 5.1 (`powershell.exe`) and PowerShell 7
    (`pwsh`) since the installer must work on either."""
    exe = shutil.which(shell)
    if not exe:
        pytest.skip(f"{shell} is not installed")
    env = _isolated_install_env(tmp_path / "home")
    env.update(
        POWERSHELL_TELEMETRY_OPTOUT="1",
        POWERSHELL_UPDATECHECK="Off",
    )
    harness = tmp_path / f"harness-{shell.replace('.exe', '')}.ps1"
    harness.write_text(
        "[Console]::Error.WriteLine('CLASSIFIER-HARNESS-STARTED')\n"
        + _extract_ps1_functions("Test-IsAccessDenied", "Write-TaskAccessDeniedWarning")
        + """

function Write-Warn2 { param([string]$m) Write-Host "WARN: $m" }
$TaskName = 'Test Task'

$deniedRecord = $null
try { throw [System.UnauthorizedAccessException]::new('Access is denied.') }
catch { $deniedRecord = $_ }

$otherRecord = $null
try { throw [System.IO.IOException]::new('disk full') }
catch { $otherRecord = $_ }

Write-TaskAccessDeniedWarning $deniedRecord 'update'
Write-Host 'DENIED-HANDLED-WITHOUT-THROW'

try {
    Write-TaskAccessDeniedWarning $otherRecord 'update'
    Write-Host 'SHOULD-NOT-REACH-HERE'
} catch {
    Write-Host "OTHER-RETHROWN: $($_.Exception.Message)"
}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=_HARNESS_TIMEOUT_SECONDS,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.stderr == "CLASSIFIER-HARNESS-STARTED\n"
    assert "WARN: could not update scheduled task 'Test Task' (Access is denied)" in result.stdout
    assert "DENIED-HANDLED-WITHOUT-THROW" in result.stdout
    assert "OTHER-RETHROWN: disk full" in result.stdout
    assert "SHOULD-NOT-REACH-HERE" not in result.stdout


def test_installers_scope_sync_supervision_by_install_root() -> None:
    install_ps1 = _INSTALL_PS1.read_text(encoding="utf-8")
    install_sh = _INSTALL_SH.read_text(encoding="utf-8")

    assert 'TIMER_NAME="agent-logger-sync${SERVICE_SUFFIX:+-$SERVICE_SUFFIX}"' in install_sh
    assert "Environment=AGENT_LOGGER_HOME=${INSTALL_DIR}" in install_sh
    assert "Agent Logger Session Sync - $serviceSuffix" in install_ps1
    assert "$TaskLauncher = Join-Path (Join-Path $InstallDir 'bin') 'session-sync-task.ps1'" in install_ps1
    assert "$env:AGENT_LOGGER_HOME = $_root" in install_ps1


def test_posix_snapshot_uses_self_staged_payload_not_original() -> None:
    install_sh = _INSTALL_SH.read_text(encoding="utf-8")
    publisher = install_sh.split("publish_payload_snapshot() {", 1)[1].split(
        "# Cheap 'stamp'", 1
    )[0]

    assert 'cp -a "${PLUGIN_DIR}/."' in publisher
    assert "COPILOT_PLUGIN_STAGED_FROM" not in publisher


# Keep the existing per-test ceiling above the native provisioning subprocess.
@pytest.mark.timeout(450)
def test_provision_publishes_durable_compatibility_wrappers(
    tmp_path: Path,
) -> None:
    payload = _stage_release_payload(tmp_path)
    home = tmp_path / "home"
    env = _isolated_install_env(home)
    calls = _offline_uv(tmp_path, env)
    if os.name == "nt":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        assert powershell is not None
        command = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(payload / "scripts" / "install.ps1"),
            "provision",
        ]
        wrapper = home / ".local" / "bin" / "collate-session.ps1"
    else:
        command = ["bash", str(payload / "scripts" / "install.sh"), "provision"]
        wrapper = home / ".local" / "bin" / "collate-session"

    provision = subprocess.run(
        command,
        env=env,
        cwd=payload,
        capture_output=True,
        text=True,
        timeout=420,
        check=False,
    )
    assert provision.returncode == 0, provision.stdout + provision.stderr
    snapshot = Path(
        (home / ".agent-logger" / "payload-dir").read_text(encoding="utf-8").strip()
    )
    assert snapshot.is_dir()
    assert wrapper.is_file()
    assert calls.is_file()
    _assert_complete_runtime(home)


# Regression (coverage-guided-ci's full-matrix local validation pass,
# 2026-10-04): same class as `test_provision_publishes_durable_compatibility_
# wrappers` above -- two sequential subprocess.run calls (timeout=60,
# timeout=420) mean a worst case near 480s, well past the 30s blanket
# pytest-timeout default. Real headroom above the combined internal ceiling.
@pytest.mark.timeout(520)
def test_stamp_supports_first_use_provision_from_snapshot_only(tmp_path: Path) -> None:
    payload = _stage_release_payload(tmp_path)
    home = tmp_path / "home"
    version = next(
        line.split('"')[1]
        for line in (_PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines()
        if line.startswith("version = ")
    )
    env = _isolated_install_env(home)
    calls = _offline_uv(tmp_path, env)

    if os.name == "nt":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        assert powershell is not None
        stamp = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(payload / "scripts" / "install.ps1"),
            "stamp",
        ]
        wrapper = home / ".local" / "bin" / "agent-logger.ps1"
        invoke = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(wrapper),
            "version",
        ]
    else:
        stamp = ["bash", str(payload / "scripts" / "install.sh"), "stamp"]
        wrapper = home / ".local" / "bin" / "agent-logger"
        invoke = [str(wrapper), "version"]

    stamped = subprocess.run(
        stamp,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert stamped.returncode == 0, stamped.stderr
    assert not calls.exists(), "stamp must defer dependency installation"
    assert not (home / ".agent-logger" / ".venv").exists()

    snapshot = Path(
        (home / ".agent-logger" / "payload-dir").read_text(encoding="utf-8").strip()
    )
    assert (snapshot / "scripts" / "installer-engine.sh").is_file()
    assert (snapshot / "scripts" / "installer-engine.ps1").is_file()
    assert (snapshot / "snapshot-provenance.json").is_file()
    for component in (
        "versioned_runtime.py",
        "versioned_runtime_leases.py",
        "versioned_runtime_construction.py",
        "versioned_runtime_source.py",
        "versioned_runtime_publication.py",
        "installer_dependency_policy.py",
        "installer_dependency_config.py",
    ):
        assert (snapshot / "scripts" / component).is_file(), component
    assert '. "$SCRIPT_DIR/installer-engine.sh"' in (
        snapshot / "scripts" / "install.sh"
    ).read_text(encoding="utf-8")
    assert ". (Join-Path $PSScriptRoot 'installer-engine.ps1')" in (
        snapshot / "scripts" / "install.ps1"
    ).read_text(encoding="utf-8")

    shutil.rmtree(payload)
    shutil.rmtree(tmp_path / "plugins")
    shutil.rmtree(tmp_path / "libs")

    provision = subprocess.run(
        invoke,
        env=env,
        cwd=snapshot,
        capture_output=True,
        text=True,
        timeout=420,
        check=False,
    )
    assert provision.returncode == 0, provision.stdout + provision.stderr
    assert f"agent-logger {version}" in provision.stdout
    assert calls.is_file()
    _assert_complete_runtime(home)


# Regression (coverage-guided-ci's full-matrix local validation pass,
# 2026-10-04): same class of gap -- two sequential timeout=60 subprocess
# calls mean a worst case near 120s, past the 30s blanket default.
@pytest.mark.timeout(150)
def test_stamp_reuses_pre_adoption_same_version_snapshot(tmp_path: Path) -> None:
    payload = _stage_payload(tmp_path)
    home = tmp_path / "home"
    version = next(
        line.split('"')[1]
        for line in (_PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines()
        if line.startswith("version = ")
    )
    legacy_snapshot = home / ".agent-logger" / "snapshots" / version
    shutil.copytree(payload, legacy_snapshot)
    legacy_bin = legacy_snapshot / "bin" / "agent-logger"
    legacy_bin.parent.mkdir(parents=True, exist_ok=True)
    legacy_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    legacy_bin.chmod(0o755)
    install_sh = legacy_snapshot / "scripts" / "install.sh"
    install_ps1 = legacy_snapshot / "scripts" / "install.ps1"
    install_sh.write_text("#!/usr/bin/env bash\n# legacy self-contained install\n", encoding="utf-8")
    install_ps1.write_text("<# legacy self-contained install #>\n", encoding="utf-8")

    env = _isolated_install_env(home)
    if os.name == "nt":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        assert powershell is not None
        command = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(payload / "scripts" / "install.ps1"),
            "stamp",
        ]
    else:
        command = ["bash", str(payload / "scripts" / "install.sh"), "stamp"]

    result = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "incomplete" not in result.stderr.lower()

    snapshot = Path(
        (home / ".agent-logger" / "payload-dir").read_text(encoding="utf-8").strip()
    )
    assert snapshot == legacy_snapshot

    sentinel = snapshot / ".snapshot-sentinel"
    sentinel.write_text("keep", encoding="utf-8")
    repeat = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert repeat.returncode == 0, repeat.stderr
    assert sentinel.read_text(encoding="utf-8") == "keep"


# Regression (coverage-guided-ci's full-matrix local validation pass,
# 2026-10-04): same class of gap -- a single timeout=60 subprocess call,
# past the 30s blanket default.
@pytest.mark.timeout(90)
def test_scoped_stamp_avoids_global_compatibility_wrappers(tmp_path: Path) -> None:
    payload = _stage_payload(tmp_path)
    home = tmp_path / "home"
    install_dir = (
        home
        / ".copilot-extensions"
        / "marketplaces"
        / "example--1234"
        / "plugins"
        / "agent-logger"
    )
    env = _isolated_install_env(home)
    if os.name == "nt":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        assert powershell is not None
        command = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(payload / "scripts" / "install.ps1"),
            "stamp",
            "-InstallDir",
            str(install_dir),
        ]
    else:
        command = [
            "bash",
            str(payload / "scripts" / "install.sh"),
            "stamp",
            "--install-dir",
            str(install_dir),
        ]
    result = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (install_dir / "payload-dir").is_file()
    local_bin = home / ".local" / "bin"
    for command_name in _COMMANDS:
        assert not (local_bin / command_name).exists()
        assert not (local_bin / f"{command_name}.ps1").exists()
        assert not (local_bin / f"{command_name}.cmd").exists()


# --------------------------------------------------------------------------- #
# vendored-lib install-step regression                                        #
# --------------------------------------------------------------------------- #
#
# A dependency declared only in pyproject.toml is NOT enough for either
# installer: each vendored lib needs an explicit, source-attributable install
# before the main package's seasoned dependency resolution. A bare dependency
# name without its declared local mapping must not resolve from an unaged cache.
# This was missed for three new libs
# when schema v3's registered-project trust gate was added (confirmed live:
# a fresh install produced a package that raised `ModuleNotFoundError: No
# module named 'plugin_activation'` on every CLI invocation). These tests
# derive the expected lib set straight from pyproject.toml's own
# ``[tool.uv.sources]`` (the source of truth for "which packages are vendored
# path dependencies") rather than a hand-maintained list, so a future
# dependency add/rename that isn't matched by an install step fails here
# automatically -- no one has to remember to update a second, parallel list.

_PYPROJECT_TEXT = (_PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8")


def _vendored_path_dependencies() -> dict[str, str]:
    """Map each ``[tool.uv.sources]`` path-pinned package name to its vendored
    ``libs/<dir>`` directory name (e.g. ``"agent-plugin-activation" ->
    "plugin-activation"``). Matches both the real-copy form
    (``path = "libs/<dir>"``) and the `uv`-editable canonical-reference form
    (``path = "../../libs/<dir>", editable = true`` --
    vendor-pointer-generalization effort, Phase 1) -- a lib converted to the
    latter still needs its own explicit install step ahead of this
    installer's final package install, exactly like a real copy does."""
    sources_match = re.search(
        r"\[tool\.uv\.sources\](.*?)(?:\n\[|\Z)", _PYPROJECT_TEXT, re.DOTALL
    )
    assert sources_match, "pyproject.toml has no [tool.uv.sources] table"
    mapping = {}
    for name, lib_dir in re.findall(
        r'^([\w-]+)\s*=\s*\{\s*path\s*=\s*"(?:\.\./)*libs/([\w-]+)"',
        sources_match.group(1),
        re.MULTILINE,
    ):
        mapping[name] = lib_dir
    assert mapping, "no path-pinned dependencies found in [tool.uv.sources]"
    return mapping


_VENDORED_LIB_DIRS = sorted(_vendored_path_dependencies().values())


def _install_sh_lib_positions() -> dict[str, int]:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    main_marker = text.index(
        'invoke_uv_pip_install_resilient "${UV_CMD}" --python "${VENV}/bin/python" "${PLUGIN_DIR}"'
    )
    body = text[:main_marker]
    positions = {}
    for lib in _VENDORED_LIB_DIRS:
        index = body.find(f"libs/{lib}")
        assert index != -1, f"install.sh has no install step for libs/{lib}"
        positions[lib] = index
    return positions


def _install_ps1_lib_positions() -> dict[str, int]:
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    main_marker = text.index("-Arguments @('--python', $VenvPython, \"$PluginDir\", '--quiet')")
    body = text[:main_marker]
    positions = {}
    for lib in _VENDORED_LIB_DIRS:
        index = body.find(f"Dir = '{lib}'")
        assert index != -1, f"install.ps1 has no install step for lib dir '{lib}'"
        positions[lib] = index
    return positions


@pytest.mark.parametrize(
    "positions_fn", [_install_sh_lib_positions, _install_ps1_lib_positions]
)
def test_installers_install_every_pyproject_vendored_lib_before_main_package(
    positions_fn,
) -> None:
    """Every ``[tool.uv.sources]`` path dependency in pyproject.toml gets an
    explicit install step, in both installers, before the final seasoned
    main-package install (asserted by ``_install_*_positions`` itself
    raising if a lib's install step is missing entirely)."""
    positions_fn()


@pytest.mark.parametrize(
    "positions_fn", [_install_sh_lib_positions, _install_ps1_lib_positions]
)
def test_installers_install_plugin_activation_after_its_own_transitive_deps(
    positions_fn,
) -> None:
    """plugin_activation imports dropin_registry and plugin_resolve at module
    load time, so its own install step must come after both of theirs --
    installing it first would leave its own dependencies briefly
    unresolvable mid-install. Every step still resolves its transitive and
    isolated build dependencies through the canonical seasoning policy."""
    positions = positions_fn()
    assert positions["plugin-activation"] > positions["dropin-registry"]
    assert positions["plugin-activation"] > positions["plugin-resolve"]


@pytest.mark.parametrize(
    "positions_fn", [_install_sh_lib_positions, _install_ps1_lib_positions]
)
def test_installers_install_driver_after_its_own_transitive_deps(positions_fn) -> None:
    positions = positions_fn()
    assert positions["update-driver"] > positions["agent-procutil"]
    assert positions["update-driver"] > positions["single-instance-lease"]


def test_install_ps1_falls_back_to_monorepo_libs_dir() -> None:
    """Unlike a plugin-local vendored copy (always present in a deployed
    payload), a local monorepo dev checkout may stage the plugin without its
    own libs\\ copy -- install.ps1 must resolve each lib against the
    top-level libs\\ directory in that case, mirroring install.sh's own
    ${PLUGIN_DIR}/../../libs/<lib> fallback."""
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    assert r"..\..\libs\$($lib.Dir)" in text
