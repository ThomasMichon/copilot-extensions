"""PowerShell installer regressions for agent-vault's stamped snapshot path."""

from __future__ import annotations

import os
import json
import runpy
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import agent_procutil
import pytest


_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_PS1 = _PLUGIN_ROOT / "scripts" / "install.ps1"
_HARNESS_TIMEOUT_SECONDS = 20
_UV_INDEX_BLOCK_START = "function Test-UvConfiguredIndex {"
_UV_INDEX_BLOCK_END = "\n# === install-contract:v3 source-kind -- keep byte-identical across plugins ==="
_UV_LAUNCHER_BYTES: bytes | None = None
_UV_SETTINGS = """PipInstallSettings { settings: PipSettings {
    index_locations: IndexLocations {
        flat_index: [],
    },
    exclude_newer: ExcludeNewer {
        global: None,
        package: ExcludeNewerPackage({}),
    },
}, }
"""
_UV_DRIVER = r'''
import json
import os
import re
import runpy
import shutil
import sys
import tempfile
import venv
from datetime import datetime, timedelta, timezone
from pathlib import Path

args = sys.argv[1:]
log = Path(os.environ["FIXTURE_UV_LOG"])
def record(phase, **values):
    with log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(phase=phase, **values)) + "\n")

if args == ["--version"]:
    print("uv 0.12.6 (offline fixture)")
elif args and args[0] == "venv":
    target = Path(args[1])
    venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(target)
    record("venv", target=str(target))
elif args[:2] == ["pip", "install"]:
    if "--show-settings" in args:
        record("preflight")
        if not any(arg == "--quiet" or re.fullmatch(r"-q+", arg) for arg in args):
            print(os.environ["FIXTURE_UV_SETTINGS"], end="")
        raise SystemExit(0)
    assert args[-3] == "--exclude-newer" and args[-1] == "--reinstall"
    assert "--quiet" in args, "Installer verbosity must survive admission"
    cutoff = datetime.fromisoformat(args[-2].replace("Z", "+00:00"))
    assert cutoff <= datetime.now(timezone.utc) - timedelta(days=7)
    assert not any(arg.startswith(("--no-deps", "--no-build-isolation")) for arg in args)
    sources = [Path(arg) for arg in args[2:] if (Path(arg) / "pyproject.toml").is_file()]
    assert len(sources) == 1, "Only one local snapshot artifact is supported"
    source = sources[0].resolve()
    config = Path(args[args.index("--config-file") + 1]).resolve()
    private = config.parent
    assert config.is_file() and not config.is_symlink() and private.name == "inputs"
    assert private.parent.parent.samefile(Path(tempfile.gettempdir()))
    assert source.is_relative_to(private)
    if os.name != "nt":
        assert private.stat().st_uid == os.getuid() and private.stat().st_mode & 0o077 == 0
    snapshot = Path(os.environ["FIXTURE_SNAPSHOT_ROOT"]).resolve()
    parse = runpy.run_path(str(snapshot / "scripts" / "installer_dependency_policy.py"))["_toml"]
    project = parse(source / "pyproject.toml")["project"]
    candidates = [snapshot, *(path for path in (snapshot / "libs").iterdir()
                             if (path / "pyproject.toml").is_file())]
    matches = [path for path in candidates
               if parse(path / "pyproject.toml")["project"]["name"] == project["name"]]
    assert len(matches) == 1, "Retained project must match exactly one snapshot identity"
    original = matches[0]
    assert source != original and original.is_relative_to(snapshot)
    assert project == parse(original / "pyproject.toml")["project"]
    for member in original.rglob("*.py"):
        assert (source / member.relative_to(original)).read_bytes() == member.read_bytes()
    python = Path(args[args.index("--python") + 1])
    target = python.parent.parent
    cfg = (target / "pyvenv.cfg").read_text(encoding="utf-8")
    runtime_version = re.search(r"(?m)^version = (\d+\.\d+)", cfg).group(1)
    site = target / "Lib" / "site-packages" if os.name == "nt" else (
        target / "lib" / ("python" + runtime_version) / "site-packages"
    )
    site.mkdir(parents=True, exist_ok=True)
    src = source / "src"
    assert src.is_dir(), "Snapshot source package is missing"
    for child in src.iterdir():
        destination = site / child.name
        if child.is_dir():
            shutil.copytree(child, destination, dirs_exist_ok=True)
        else:
            shutil.copyfile(child, destination)
    name = project["name"]
    version = project["version"].replace("-dev", ".dev")
    metadata = site / (name.replace("-", "_") + "-" + version + ".dist-info")
    metadata.mkdir(exist_ok=True)
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: " + name + "\nVersion: " + version + "\n",
        encoding="utf-8",
    )
    if name == "agent-vault":
        (metadata / "entry_points.txt").write_text(
            "[agent_vault.extensions]\ncore-delegation = agent_vault.core_ext:register\n",
            encoding="utf-8",
        )
    record("install", source=str(original), retained_source=str(source), name=name)
else:
    raise SystemExit("Unsupported offline UV operation")
'''


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
        "AGENT_VAULT_HOME": home / ".agent-vault",
    }
    for path in roots.values():
        path.mkdir(parents=True, exist_ok=True)
    env.update({name: str(path) for name, path in roots.items()})
    env["COPILOT_PLUGIN_INSTALL_STAGED"] = "1"
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        env.pop(name, None)
    return env


def _stage_payload(tmp_path: Path) -> Path:
    payload = tmp_path / "plugins" / "agent-vault"
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
    previous_path = sys.path[:]
    try:
        release = runpy.run_path(str(_PLUGIN_ROOT.parents[1] / "tools" / "materialize_main.py"))
        materialized = release["materialize_uv_editable_ref_into"](
            source_consumer_dir=_PLUGIN_ROOT,
            dest_consumer_dir=payload,
            canonical_root=_PLUGIN_ROOT.parents[1],
        )
    finally:
        sys.path[:] = previous_path
    assert not any(message.startswith("SKIP ") for message in materialized), materialized
    policy = runpy.run_path(str(payload / "scripts" / "installer_dependency_policy.py"))
    project = policy["_toml"](payload / "pyproject.toml")
    manifest = json.loads((payload / "plugin.json").read_text(encoding="utf-8"))
    assert project["project"]["name"] == manifest["name"] == "agent-vault"
    assert project["project"]["version"] == manifest["version"]
    assert project["tool"]["uv"]["sources"] == {
        "agent-procutil": {"path": "libs/agent-procutil"},
        "agent-zdd": {"path": "libs/zdd"},
        "agent-single-instance-lease": {"path": "libs/single-instance-lease"},
    }
    staged_libs = tmp_path / "libs"
    staged_libs.mkdir(parents=True, exist_ok=True)
    for lib in ("installer-engine",):
        shutil.copytree(
            _PLUGIN_ROOT.parents[1] / "libs" / lib, staged_libs / lib,
            ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", ".pytest_cache", "tests", "build", "dist", "*.egg-info",
            ),
        )
    return payload


def _offline_uv(tmp_path: Path, env: dict[str, str]) -> Path:
    """Mock artifact delivery, not venv/health/ownership or release-age evidence."""
    global _UV_LAUNCHER_BYTES
    directory = tmp_path / "offline-uv"
    directory.mkdir()
    driver = directory / "driver.py"
    driver.write_text(_UV_DRIVER, encoding="utf-8")
    if os.name == "nt" and _UV_LAUNCHER_BYTES is None:
        provider = directory / "uv.exe"
        launcher = directory / "launcher.cs"
        launcher.write_text(r'''
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Threading.Tasks;
public class OfflineUv {
    static string Quote(string value) {
        var result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char character in value) {
            if (character == '\\') { slashes++; continue; }
            result.Append('\\', character == '"' ? slashes * 2 + 1 : slashes);
            result.Append(character);
            slashes = 0;
        }
        result.Append('\\', slashes * 2);
        return result.Append('"').ToString();
    }
    public static int Main(string[] args) {
        if (args.Length == 1 && args[0] == "--version") {
            Console.Out.WriteLine("uv 0.12.6 (offline fixture)");
            return 0;
        }
        if (args.Length >= 2 && args[0] == "pip" && args[1] == "install"
            && Array.IndexOf(args, "--show-settings") >= 0) {
            File.AppendAllText(Environment.GetEnvironmentVariable("FIXTURE_UV_LOG"),
                "{\"phase\": \"preflight\"}\n");
            bool quiet = false;
            foreach (var arg in args) {
                if (arg == "--quiet" || System.Text.RegularExpressions.Regex.IsMatch(arg, "^-q+$"))
                    quiet = true;
            }
            if (!quiet) Console.Out.Write(Environment.GetEnvironmentVariable("FIXTURE_UV_SETTINGS"));
            return 0;
        }
        var driver = Path.Combine(Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location), "driver.py");
        var arguments = new StringBuilder(Quote(driver));
        foreach (string argument in args) { arguments.Append(" ").Append(Quote(argument)); }
        var info = new ProcessStartInfo(PYTHON, arguments.ToString());
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        info.RedirectStandardOutput = true;
        info.RedirectStandardError = true;
        using (var process = Process.Start(info)) {
            var output = process.StandardOutput.ReadToEndAsync();
            var errors = process.StandardError.ReadToEndAsync();
            process.WaitForExit();
            Task.WaitAll(output, errors);
            Console.Out.Write(output.Result);
            Console.Error.Write(errors.Result);
            return process.ExitCode;
        }
    }
}
'''.replace("PYTHON", json.dumps(sys._base_executable)),
            encoding="utf-8",
        )
        compiler = (
            Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell"
            / "v1.0" / "powershell.exe"
        )
        compilation = subprocess.run(
            [str(compiler), "-NoProfile", "-Command",
             "Add-Type -TypeDefinition ([IO.File]::ReadAllText($env:FIXTURE_LAUNCHER_SOURCE)) "
             "-OutputAssembly $env:FIXTURE_LAUNCHER_EXE -OutputType ConsoleApplication"],
            env={**env, "FIXTURE_LAUNCHER_SOURCE": str(launcher), "FIXTURE_LAUNCHER_EXE": str(provider)},
            capture_output=True, text=True, timeout=_HARNESS_TIMEOUT_SECONDS,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert compilation.returncode == 0 and provider.is_file(), compilation.stderr
        _UV_LAUNCHER_BYTES = provider.read_bytes()
    elif os.name == "nt":
        provider = directory / "uv.exe"
        provider.write_bytes(_UV_LAUNCHER_BYTES)
    else:
        provider = directory / "uv"
        provider.write_text(
            f"#!/bin/sh\nexec {shlex.quote(sys._base_executable)} {shlex.quote(str(driver))} \"$@\"\n",
            encoding="utf-8",
        )
        provider.chmod(0o755)
    if os.name == "nt":
        # This snapshot lane exercises UV construction, not the host's optional
        # signed-Python discovery or a Python install manager's acquisition.
        (directory / "py.cmd").write_text("@echo off\nexit /b 1\n", encoding="utf-8")
    env.update({
        "PATH": str(directory) + os.pathsep + env.get("PATH", ""),
        "FIXTURE_UV_LOG": str(tmp_path / "uv-events.jsonl"),
        "FIXTURE_UV_SETTINGS": _UV_SETTINGS,
        "UV_DEFAULT_INDEX": "https://fixture.invalid/simple",
    })
    return provider


def _uv_events(env: dict[str, str]) -> list[dict]:
    path = Path(env["FIXTURE_UV_LOG"])
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _stamp_uv_guard(tmp_path: Path, env: dict[str, str]) -> Path:
    """Detect any UV use without compiling the deferred runtime provider."""
    directory = tmp_path / "stamp-uv"
    directory.mkdir()
    provider = directory / ("uv.cmd" if os.name == "nt" else "uv")
    provider.write_text(
        '@echo off\n>> "%FIXTURE_UV_LOG%" echo {"phase":"unexpected-stamp-uv"}\nexit /b 1\n'
        if os.name == "nt" else
        "#!/bin/sh\nprintf '%s\\n' '{\"phase\":\"unexpected-stamp-uv\"}' "
        '>> "$FIXTURE_UV_LOG"\nexit 1\n',
        encoding="utf-8",
    )
    if os.name != "nt":
        provider.chmod(0o755)
    env.update({
        "PATH": str(directory) + os.pathsep + env.get("PATH", ""),
        "FIXTURE_UV_LOG": str(tmp_path / "uv-events.jsonl"),
    })
    return provider


def test_stamp_uv_guard_rejects_and_records_any_invocation(tmp_path: Path) -> None:
    env = _isolated_install_env(tmp_path / "home")
    provider = _stamp_uv_guard(tmp_path, env)
    command = [str(provider), "--version"]
    if os.name == "nt":
        command = [os.environ["COMSPEC"], "/d", "/c", *command]
    result = subprocess.run(
        command, env=env, capture_output=True, text=True, timeout=10,
        **agent_procutil.no_window_kwargs(),
    )
    assert result.returncode == 1, result.stderr
    assert _uv_events(env) == [{"phase": "unexpected-stamp-uv"}]


def test_offline_uv_settings_preflight_is_read_only(tmp_path: Path) -> None:
    env = _isolated_install_env(tmp_path / "home")
    provider = _offline_uv(tmp_path, env)
    result = subprocess.run(
        [str(provider), "pip", "install", "--show-settings", "unused-artifact"],
        env=env, capture_output=True, text=True, timeout=_HARNESS_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, result.stderr
    policy = runpy.run_path(str(_PLUGIN_ROOT / "scripts" / "installer_dependency_policy.py"))
    assert policy["cutoff_arguments"](result.stdout)[::2] == ["--exclude-newer", "--reinstall"]
    assert _uv_events(env) == [{"phase": "preflight"}]


def test_offline_uv_is_native_discoverable(tmp_path: Path) -> None:
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")
    env = _isolated_install_env(tmp_path / "home")
    provider = _offline_uv(tmp_path, env)
    engine = _PLUGIN_ROOT.parents[1] / "libs" / "installer-engine" / "installer-engine.ps1"
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    result = subprocess.run(
        [pwsh, "-NoProfile", "-Command",
         "$ErrorActionPreference='Stop'; "
         f". {quote(engine)}; "
         f"$uv=Ensure-Uv -InstallRoot {quote(tmp_path / 'runtime')} -AcquireIfMissing $false; "
         "if (-not $uv) { throw 'Offline provider was not resolved' }; "
         "[Console]::WriteLine($uv); & $uv --version; exit $LASTEXITCODE"],
        env=env, capture_output=True, text=True, timeout=_HARNESS_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.strip().splitlines()
    assert Path(lines[0]).resolve() == provider.resolve(), result.stdout
    assert lines[1] == "uv 0.12.6 (offline fixture)"


def test_offline_uv_policy_preflight_preserves_final_quiet(tmp_path: Path) -> None:
    env = _isolated_install_env(tmp_path / "home")
    _offline_uv(tmp_path, env)
    # Windows resolves a bare executable using the caller's PATH, not Popen's
    # environment override. Match the installer: run the policy in that caller.
    result = subprocess.run(
        [sys.executable, "-I", "-c",
         "import json,runpy,sys; p=runpy.run_path(sys.argv[1]); "
         "print(json.dumps(p['prepare_uv_install']("
         "'uv',['--python',sys.executable,'unused-artifact','--quiet'])))",
         str(_PLUGIN_ROOT / "scripts" / "installer_dependency_policy.py")],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=_HARNESS_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, result.stderr
    admitted = json.loads(result.stdout)
    assert "--quiet" in admitted
    assert admitted[-3] == "--exclude-newer" and admitted[-1] == "--reinstall"
    assert _uv_events(env) == [{"phase": "preflight"}]


def _extract_uv_index_block() -> str:
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    return _UV_INDEX_BLOCK_START + text.split(_UV_INDEX_BLOCK_START, 1)[1].split(_UV_INDEX_BLOCK_END, 1)[0]


def test_ensure_uv_index_bridges_pip_only_config_to_uv(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("POSIX shim harness")
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    home = tmp_path / "home"
    env = _isolated_install_env(home)
    env.pop("UV_DEFAULT_INDEX", None)
    env.pop("UV_INDEX_URL", None)

    shim_dir = tmp_path / "bin"
    shim_dir.mkdir(parents=True)
    pip_shim = shim_dir / "pip"
    index_url = "https://example.invalid/simple"
    pip_shim.write_text(
        "#!/usr/bin/env sh\n"
        "if [ \"$1\" = config ] && [ \"$2\" = get ] && [ \"$3\" = global.index-url ]; then\n"
        f"  printf '%s\\n' '{index_url}'\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    pip_shim.chmod(0o755)
    env["PATH"] = str(shim_dir) + os.pathsep + env.get("PATH", "")

    script = f"""
function Write-Step {{ param([string]$Message) }}
{_extract_uv_index_block()}
Ensure-UvIndex
if (-not $env:UV_DEFAULT_INDEX) {{ exit 1 }}
[Console]::Write($env:UV_DEFAULT_INDEX)
"""
    proc = subprocess.run(
        [pwsh, "-NoProfile", "-Command", script],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=_HARNESS_TIMEOUT_SECONDS,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == index_url


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer behavior")
def test_posix_stamp_wrapper_fails_when_provision_reports_success_without_runtime(
    tmp_path: Path,
) -> None:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("native POSIX bash is unavailable")

    home = tmp_path / "home"
    env = _isolated_install_env(home)
    install_dir = home / ".agent-vault"
    stamp = subprocess.run(
        [
            bash,
            str(_PLUGIN_ROOT / "scripts" / "install.sh"),
            "stamp",
            "--install-dir",
            str(install_dir),
        ],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert stamp.returncode == 0, stamp.stderr

    fake_snapshot = tmp_path / "fake-snapshot"
    installer = fake_snapshot / "scripts" / "install.sh"
    installer.parent.mkdir(parents=True, exist_ok=True)
    installer.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    installer.chmod(0o755)
    (install_dir / "payload-dir").write_text(str(fake_snapshot), encoding="utf-8")

    invoke = subprocess.run(
        [str(home / ".local" / "bin" / "agent-vault"), "--version"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert invoke.returncode == 1, invoke.stderr
    assert "provisioning completed without a resolvable runtime" in invoke.stderr


@pytest.fixture(scope="module")
def stamped_snapshot(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("vault-snapshot")
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    payload = _stage_payload(tmp_path)
    home = tmp_path / "home"
    version = next(
        line.split('"')[1]
        for line in (_PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines()
        if line.startswith("version = ")
    )
    expected_version = version.replace("-dev", ".dev")
    env = _isolated_install_env(home)
    _stamp_uv_guard(tmp_path, env)
    if os.name != "nt":
        system_root = home / "systemroot"
        where_exe = system_root / "System32" / "where.exe"
        powershell_exe = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        where_exe.parent.mkdir(parents=True, exist_ok=True)
        powershell_exe.parent.mkdir(parents=True, exist_ok=True)
        where_exe.write_text(
            "#!/usr/bin/env sh\n"
            "if [ \"$1\" = pwsh ]; then\n"
            f"  printf '%s\\n' '{pwsh}'\n"
            "fi\n",
            encoding="utf-8",
        )
        where_exe.chmod(0o755)
        powershell_exe.write_text(
            "#!/usr/bin/env sh\n"
            f"exec '{pwsh}' \"$@\"\n",
            encoding="utf-8",
        )
        powershell_exe.chmod(0o755)
        env["SystemRoot"] = str(system_root)

    stamp = [
        pwsh,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(payload / "scripts" / "install.ps1"),
        "stamp",
    ]

    stamped = subprocess.run(
        stamp,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert stamped.returncode == 0, stamped.stderr

    snapshot = Path((home / ".agent-vault" / "payload-dir").read_text(encoding="utf-8").strip())
    assert (snapshot / "scripts" / "installer-engine.sh").is_file()
    assert (snapshot / "scripts" / "installer-engine.ps1").is_file()
    assert '. "$SCRIPT_DIR/installer-engine.sh"' in (
        snapshot / "scripts" / "install.sh"
    ).read_text(encoding="utf-8")
    assert ". (Join-Path $PSScriptRoot 'installer-engine.ps1')" in (
        snapshot / "scripts" / "install.ps1"
    ).read_text(encoding="utf-8")
    assert (snapshot / "libs" / "zdd" / "pyproject.toml").is_file()
    assert (snapshot / "libs" / "agent-procutil" / "pyproject.toml").is_file()
    assert (snapshot / "libs" / "single-instance-lease" / "pyproject.toml").is_file()
    for helper in (
        "versioned_runtime.py", "versioned_runtime_leases.py",
        "versioned_runtime_construction.py", "installer_dependency_policy.py",
        "versioned_runtime_source.py",
        "versioned_runtime_publication.py",
        "installer_dependency_config.py",
    ):
        assert (snapshot / "scripts" / helper).is_file()
    policy = runpy.run_path(str(snapshot / "scripts" / "installer_dependency_policy.py"))
    assert policy["_anchor_roots"]() == (snapshot.resolve(),)
    assert not (home / ".agent-vault" / "current-version").exists()
    assert not list((home / ".agent-vault" / "versions").glob("*/pyvenv.cfg"))
    env["FIXTURE_SNAPSHOT_ROOT"] = str(snapshot)

    shutil.rmtree(payload)
    shutil.rmtree(tmp_path / "libs")
    assert not payload.exists() and not (tmp_path / "libs").exists()
    return SimpleNamespace(
        home=home, snapshot=snapshot, env=env, pwsh=pwsh, fixture_root=tmp_path,
        version=version, expected_version=expected_version,
        stamp_status={
            "selected": (home / ".agent-vault" / "current-version").exists(),
            "venvs": list((home / ".agent-vault" / "versions").glob("*/pyvenv.cfg")),
            "events": _uv_events(env),
        },
    )


def test_stamp_snapshot_retains_resources_without_creating_runtime(stamped_snapshot) -> None:
    state = stamped_snapshot
    assert (state.snapshot / "scripts" / "installer-engine.ps1").is_file()
    assert (state.snapshot / "scripts" / "installer-engine.sh").is_file()
    # Capture stamp status before provisioning so contract order is irrelevant.
    assert state.stamp_status == {"selected": False, "venvs": [], "events": []}


@pytest.fixture(scope="module")
def provisioned_snapshot(stamped_snapshot):
    state = stamped_snapshot
    home, snapshot, env, pwsh = state.home, state.snapshot, state.env, state.pwsh
    _offline_uv(state.fixture_root, env)
    if os.name == "nt":
        provision = subprocess.run(
            [
                pwsh,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(snapshot / "scripts" / "install.ps1"),
                "provision",
                "-NoService",
                "-InstallDir",
                str(home / ".agent-vault"),
            ],
            env=env,
            cwd=snapshot,
            capture_output=True,
            text=True,
            timeout=420,
            check=False,
        )
        assert provision.returncode == 0, provision.stdout + provision.stderr
    else:
        provision = subprocess.run(
            [
                "bash",
                str(snapshot / "scripts" / "install.sh"),
                "provision",
                "--no-service",
                "--install-dir",
                str(home / ".agent-vault"),
            ],
            env=env,
            cwd=snapshot,
            capture_output=True,
            text=True,
            timeout=420,
            check=False,
        )
        assert provision.returncode == 0, provision.stdout + provision.stderr
    return state


@pytest.mark.timeout(60)
def test_stamp_supports_first_use_provision_from_snapshot_only_ps1(provisioned_snapshot) -> None:
    state = provisioned_snapshot
    before_launch = _uv_events(state.env)
    installs = [event for event in before_launch if event["phase"] == "install"]
    assert [event["name"] for event in installs] == [
        "agent-zdd", "agent-procutil", "agent-single-instance-lease", "agent-vault",
    ]
    assert sum(event["phase"] == "preflight" for event in before_launch) == 2 * len(installs)
    slot = state.home / ".agent-vault" / "versions" / state.version
    assert (slot / "pyvenv.cfg").is_file()
    completion = json.loads((slot / ".install-complete.json").read_text(encoding="utf-8"))
    assert completion["version"] == state.version and len(completion["payload_hash"]) == 64
    assert (state.home / ".agent-vault" / "current-version").read_text().strip() == state.version


@pytest.mark.timeout(60)
def test_snapshot_compatibility_launcher_does_not_self_provision(provisioned_snapshot) -> None:
    state = provisioned_snapshot
    before_launch = _uv_events(state.env)
    env = {**state.env, "AGENT_VAULT_NO_SELFPROVISION": "1"}
    if os.name == "nt":
        command = [
            state.pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(state.home / ".local" / "bin" / "agent-vault.ps1"), "--version",
        ]
    else:
        command = [str(state.home / ".local" / "bin" / "agent-vault"), "--version"]
    invoke = subprocess.run(
        command, env=env, capture_output=True, text=True,
        timeout=420 if os.name == "nt" else 60, check=False,
    )
    assert invoke.returncode == 0, invoke.stderr
    assert f"agent-vault {state.expected_version}" in invoke.stdout
    assert _uv_events(env) == before_launch, "Compatibility launch attempted self-provisioning"
