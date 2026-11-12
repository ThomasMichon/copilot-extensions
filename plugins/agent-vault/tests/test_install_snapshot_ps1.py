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
    activation = home / "activation.json"
    activation.write_text('{"enabled":true}', encoding="utf-8")
    env["AGENT_VAULT_CONFIG"] = str(activation)
    env.pop("AGENT_CONFIGURATION_CONTEXT", None)
    env.pop("COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT", None)
    env["COPILOT_PLUGIN_INSTALL_STAGED"] = "1"
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"):
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
    # Match the top-level fixture exclusion without pruning runtime/native assets.
    for library in (payload / "libs").iterdir():
        tests = library / "tests"
        if tests.is_dir():
            shutil.rmtree(tests)
    policy = runpy.run_path(str(payload / "scripts" / "installer_dependency_policy.py"))
    project = policy["_toml"](payload / "pyproject.toml")
    manifest = json.loads((payload / "plugin.json").read_text(encoding="utf-8"))
    assert project["project"]["name"] == manifest["name"] == "agent-vault"
    assert project["project"]["version"] == manifest["version"]
    assert project["tool"]["uv"]["sources"] == {
        "agent-configuration-layers": {"path": "libs/configuration-layers"},
        "agent-procutil": {"path": "libs/agent-procutil"},
        "agent-zdd": {"path": "libs/zdd"},
        "agent-single-instance-lease": {"path": "libs/single-instance-lease"},
        "agent-update-driver": {"path": "libs/update-driver"},
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
        compilers = (
            Path(os.environ["SystemRoot"]) / "Microsoft.NET" / framework
            / "v4.0.30319" / "csc.exe"
            for framework in ("Framework64", "Framework")
        )
        compiler = next((path for path in compilers if path.is_file()), None)
        assert compiler is not None, "Windows .NET Framework C# compiler is unavailable"
        compilation = subprocess.run(
            [str(compiler), "/nologo", "/target:exe", f"/out:{provider}", str(launcher)],
            env=env,
            capture_output=True, text=True, timeout=_HARNESS_TIMEOUT_SECONDS,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert compilation.returncode == 0 and provider.is_file(), (
            compilation.stdout + compilation.stderr
        )
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


def test_offline_uv_settings_preflight_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    global _UV_LAUNCHER_BYTES
    compilations = []
    run = subprocess.run

    def observe(command, **kwargs):
        if os.name == "nt" and Path(command[0]).name.casefold() == "csc.exe":
            compilations.append(command)
            assert command[1:3] == ["/nologo", "/target:exe"]
            assert kwargs["timeout"] == _HARNESS_TIMEOUT_SECONDS == 20
            assert kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW
        return run(command, **kwargs)

    if os.name == "nt":
        _UV_LAUNCHER_BYTES = None
        monkeypatch.setattr(subprocess, "run", observe)
    env = _isolated_install_env(tmp_path / "home")
    provider = _offline_uv(tmp_path, env)
    if os.name == "nt":
        assert provider.read_bytes().startswith(b"MZ")
        reused_root = tmp_path / "reused"
        reused_root.mkdir()
        reused = _offline_uv(reused_root, env.copy())
        assert reused.read_bytes() == provider.read_bytes()
        assert len(compilations) == 1
    result = subprocess.run(
        [str(provider), "pip", "install", "--show-settings", "unused-artifact"],
        env=env, capture_output=True, text=True, timeout=_HARNESS_TIMEOUT_SECONDS,
        **agent_procutil.no_window_kwargs(),
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
        **agent_procutil.no_window_kwargs(),
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
        **agent_procutil.no_window_kwargs(),
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
def test_posix_stamp_wrapper_refuses_snapshot_without_driver_dispatcher(
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
    assert invoke.returncode == 126, invoke.stderr
    assert "snapshot admission helper is unavailable" in invoke.stderr
    assert not (install_dir / ".provision.lock").exists()


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
    assert (snapshot / "libs" / "update-driver" / "pyproject.toml").is_file()
    for helper in (
        "versioned_runtime.py", "versioned_runtime_leases.py",
        "versioned_runtime_construction.py", "installer_dependency_policy.py",
        "versioned_runtime_source.py",
        "versioned_runtime_publication.py",
        "installer_dependency_config.py", "installer_retained_content.py",
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


def _no_service_provision_command(
    home: Path, installer: Path, pwsh: str, *, action: str = "provision",
) -> list[str]:
    runner = home / "tmp" / "provision.ps1"
    runner.write_text(
        "param($Installer, $InstallRoot, $Action)\n"
        "function Get-ScheduledTask {}\n"
        "function Stop-ScheduledTask { throw 'unexpected service mutation' }\n"
        "function Start-ScheduledTask { throw 'unexpected service mutation' }\n"
        "function Register-ScheduledTask { throw 'unexpected service mutation' }\n"
        "function Set-ScheduledTask { throw 'unexpected service mutation' }\n"
        "function Unregister-ScheduledTask { throw 'unexpected service mutation' }\n"
        "& $Installer $Action -NoService -InstallDir $InstallRoot\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    return [
        pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(runner), str(installer), str(home / ".agent-vault"), action,
    ]


@pytest.mark.parametrize("profile", ["base", "ssh"])
def test_native_profile_stamp_persists_selection_without_runtime_or_uv(tmp_path, profile):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")
    payload = _stage_payload(tmp_path)
    home = tmp_path / "home"
    env = _isolated_install_env(home)
    _stamp_uv_guard(tmp_path, env)
    command = [
        pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(payload / "scripts" / "install.ps1"), "stamp", "-Profile", profile,
    ]
    result = subprocess.run(
        command, env=env, cwd=home, capture_output=True, text=True,
        timeout=60, **agent_procutil.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    root = home / ".agent-vault"
    assert json.loads((root / "runtime-profile.json").read_text()) == {
        "schema": "agent-vault.runtime-profile", "version": 1, "profile": profile,
    }
    snapshot = Path((root / "payload-dir").read_text().strip())
    assert (snapshot / "scripts" / "runtime_profile.py").is_file()
    assert not (root / "current-version").exists()
    assert not list((root / "versions").glob("*/pyvenv.cfg"))
    assert _uv_events(env) == []


def test_prepared_profile_inputs_preserve_source_and_published_provenance(tmp_path):
    from .test_payload_invocation import _installer_function

    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")
    snapshot = tmp_path / "source"
    (snapshot / "tests").mkdir(parents=True)
    (snapshot / "scripts").mkdir()
    (snapshot / "tests" / "sentinel.py").write_text("RETAIN = True\n")
    (snapshot / "scripts" / "runtime_profile.py").write_text("retained helper fixture\n")
    (snapshot / "runtime-source.json").write_text("retained original identity fixture\n")
    receipt = snapshot / "snapshot-provenance.json"
    receipt.write_text("separate source-owner receipt fixture\n")
    before = {str(p.relative_to(snapshot)): (p.read_bytes(), p.stat().st_mtime_ns)
              for p in snapshot.rglob("*") if p.is_file()}
    root = tmp_path / "runtime"
    root.mkdir()
    runner = tmp_path / "inputs.ps1"
    runner.write_text(
        "param($Source, $Root)\n$ErrorActionPreference='Stop'\n"
        "$InstallDir=$Root; $script:SourceRuntimeIdentity='prepared'\n"
        "function Publish-SourceSnapshot { return $Source }\n"
        + _installer_function("Get-RuntimeProfileHelper")
        + _installer_function("Remove-RuntimeProfileInputs")
        + "\n$helper=Get-RuntimeProfileHelper\n"
        "[Console]::Out.WriteLine($script:RuntimeProfileInputs.Path)\n",
    )
    result = subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-File", str(runner), str(snapshot), str(root)],
        capture_output=True, text=True, timeout=15, **agent_procutil.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    inputs = Path(result.stdout.strip())
    assert inputs.parent == root
    assert not (inputs / receipt.name).exists()
    assert {str(p.relative_to(inputs)): p.read_bytes() for p in inputs.rglob("*") if p.is_file()} == {
        name: value[0] for name, value in before.items() if name != receipt.name
    }
    assert {str(p.relative_to(snapshot)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in snapshot.rglob("*") if p.is_file()} == before


@pytest.mark.timeout(60)
def test_stamp_snapshot_retains_resources_without_creating_runtime(stamped_snapshot) -> None:
    state = stamped_snapshot
    assert (state.snapshot / "scripts" / "installer-engine.ps1").is_file()
    assert (state.snapshot / "scripts" / "installer-engine.sh").is_file()
    # Capture stamp status before provisioning so contract order is irrelevant.
    assert state.stamp_status == {"selected": False, "venvs": [], "events": []}
    installer = state.fixture_root / "refusal control.ps1"
    installer.write_text(
        "param($Action, [switch]$NoService, $InstallDir)\n"
        "if ($Action -ne 'provision' -or -not $NoService "
        "-or $InstallDir -ne $env:AGENT_VAULT_HOME) { exit 1 }\n"
        "if (@(Get-ScheduledTask).Count -ne 0) { exit 2 }\n"
        "$commands = 'Stop-ScheduledTask', 'Start-ScheduledTask', "
        "'Register-ScheduledTask', 'Set-ScheduledTask', 'Unregister-ScheduledTask'\n"
        "foreach ($name in $commands) {\n"
        "  if ((Get-Command $name).CommandType -ne 'Function') { exit 3 }\n"
        "  try { & $name; exit 4 } catch {\n"
        "    if ($_.Exception.Message -ne 'unexpected service mutation') { exit 5 }\n"
        "  }\n"
        "}\n"
        "[Console]::WriteLine('all scheduled-task mutations refused')\n"
        "exit 37\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        _no_service_provision_command(state.home, installer, state.pwsh),
        env=state.env, capture_output=True, text=True, timeout=_HARNESS_TIMEOUT_SECONDS,
        **agent_procutil.no_window_kwargs(),
    )
    assert result.returncode == 37, result.stdout + result.stderr
    assert result.stdout.strip() == "all scheduled-task mutations refused"


@pytest.fixture(scope="module")
def provisioned_snapshot(stamped_snapshot):
    state = stamped_snapshot
    home, snapshot, env, pwsh = state.home, state.snapshot, state.env, state.pwsh
    _offline_uv(state.fixture_root, env)
    # Running inside the snapshot makes each library preinstall rediscover
    # and retain the entire consumer UV graph rather than its own inputs.
    if os.name == "nt":
        provision = subprocess.run(
            _no_service_provision_command(home, snapshot / "scripts" / "install.ps1", pwsh),
            env=env,
            cwd=state.fixture_root,
            capture_output=True,
            text=True,
            timeout=420,
            check=False,
            **agent_procutil.no_window_kwargs(),
        )
        assert provision.returncode == 0, provision.stdout + provision.stderr
        assert "Releasing legacy .venv" not in provision.stdout
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
            cwd=state.fixture_root,
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
        "agent-configuration-layers", "agent-zdd", "agent-procutil",
        "agent-single-instance-lease", "agent-vault",
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


@pytest.mark.timeout(60)
def test_provisioned_management_command_cannot_start_after_withdrawal(provisioned_snapshot):
    state = provisioned_snapshot
    selected = Path(state.env["AGENT_VAULT_CONFIG"])
    before = selected.read_bytes()
    environment = dict(state.env)
    slot = state.home / ".agent-vault" / "versions" / state.version
    packages = (
        slot / "Lib" / "site-packages" if os.name == "nt"
        else next((slot / "lib").glob("python*/site-packages"))
    )
    guard = packages / "sitecustomize.py"
    previous_guard = guard.read_bytes() if guard.exists() else None
    guard.write_text(
        "from agent_vault import cli\n"
        "def forbidden(*args, **kwargs):\n"
        "    raise AssertionError('withdrawn activation must not probe a backend')\n"
        "cli.send_command = forbidden\n", encoding="utf-8",
    )
    if os.name == "nt":
        command = [
            state.pwsh, "-NoProfile", "-NonInteractive", "-File",
            str(state.home / ".local" / "bin" / "agent-vault.ps1"), "start",
        ]
    else:
        command = [str(state.home / ".local" / "bin" / "agent-vault"), "start"]
    selected.write_text('{"enabled":false}', encoding="utf-8")
    try:
        result = subprocess.run(
            command, env=environment, cwd=state.home, capture_output=True,
            text=True, timeout=30, **agent_procutil.no_window_kwargs(),
        )
    finally:
        selected.write_bytes(before)
        if previous_guard is None:
            guard.unlink()
        else:
            guard.write_bytes(previous_guard)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "inactive" in result.stderr
    assert not (state.home / ".agent-vault" / "service-stdio.log").exists()


@pytest.mark.timeout(90)
def test_activation_uses_completed_runtime_without_global_python(provisioned_snapshot):
    state = provisioned_snapshot
    tools = state.fixture_root / "no-global-python"
    tools.mkdir()
    if os.name != "nt":
        for name in (
            "bash", "sh", "awk", "basename", "dirname", "cat", "cut", "date", "find",
            "grep", "head", "tail", "tr", "sed", "sort", "stat", "wc", "cksum",
            "sha256sum", "readlink", "realpath", "id", "uname", "od", "ls", "env",
            "mkdir", "rmdir", "mktemp", "rm", "cp", "mv", "chmod", "cmp", "touch",
            "ln", "flock", "hostname", "timeout",
        ):
            executable = shutil.which(name)
            if executable:
                (tools / name).symlink_to(executable)
    env = {**state.env, "PATH": str(tools)}
    assert shutil.which("python", path=env["PATH"]) is None
    assert shutil.which("python3", path=env["PATH"]) is None
    if os.name == "nt":
        runner = state.fixture_root / "activation-reader.ps1"
        runner.write_text(
            "param($Helper,$Python,$Root,$Project)\n"
            ". $Helper\n"
            "Get-VaultRuntimeActivation -Python $Python -RuntimeRoot $Root -Context '' -Project $Project | ConvertTo-Json -Compress\n",
            encoding="utf-8",
        )
        root = state.home / ".agent-vault"
        command = [
            state.pwsh, "-NoProfile", "-NonInteractive", "-File", str(runner),
            str(state.snapshot / "scripts" / "runtime-activation.ps1"),
            str(root / "versions" / state.version / "Scripts" / "python.exe"),
            str(root), str(state.home),
        ]
    else:
        command = [str(state.snapshot / "bin" / "agent-vault"), "--version"]
    result = subprocess.run(
        command, cwd=state.home, env=env, capture_output=True, text=True,
        timeout=30, **agent_procutil.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if os.name == "nt":
        assert json.loads(result.stdout)["state"] == "configured"
    else:
        assert f"agent-vault {state.expected_version}" in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="native PowerShell staged start fallback")
@pytest.mark.timeout(90)
def test_staged_start_fallback_uses_preserved_project(provisioned_snapshot):
    state = provisioned_snapshot
    project = state.fixture_root / "start-project"
    project.mkdir()
    (project / ".agent-vault.json").write_text('{"enabled":true}', encoding="utf-8")
    env = {**state.env, "AGENT_VAULT_CONFIG": str(project / "absent-machine.json")}
    slot = state.home / ".agent-vault" / "versions" / state.version
    guard = slot / "Lib" / "site-packages" / "sitecustomize.py"
    previous_guard = guard.read_bytes() if guard.exists() else None
    guard.write_text(
        "from pathlib import Path\n"
        "import os\n"
        "from agent_vault import cli\n"
        "def ping(*args, **kwargs):\n"
        "    assert Path.cwd().samefile(os.environ['COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT'])\n"
        "    return {'ok':True,'pid':7,'cli':'locked'}\n"
        "cli.send_command = ping\n", encoding="utf-8",
    )
    runner = state.fixture_root / "staged-start.ps1"
    runner.write_text(
        "param($Installer,$Python,$Project,$Root)\n"
        "$ErrorActionPreference='Stop'\n"
        "$LinkPython=$Python;$TaskName='fixture';$InstallDir=$Root\n"
        "$env:COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT=$Project\n"
        "function Get-ScheduledTask {}\n"
        "function Start-ScheduledTask {throw 'unexpected task mutation'}\n"
        "$tokens=$null;$errors=$null\n"
        "$ast=[System.Management.Automation.Language.Parser]::ParseFile($Installer,[ref]$tokens,[ref]$errors)\n"
        "if($errors.Count){throw 'Installer parse failed'}\n"
        "foreach($name in @('Get-ActiveVaultPython','Invoke-Start')) {\n"
        "    $function=$ast.Find({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$false)\n"
        "    if(-not $function){throw \"Required installer function is missing: $name\"}\n"
        "    . ([scriptblock]::Create($function.Extent.Text))\n"
        "}\n"
        "Invoke-Start\n", encoding="utf-8",
    )
    try:
        result = subprocess.run(
            [state.pwsh, "-NoProfile", "-NonInteractive", "-File", str(runner),
             str(state.snapshot / "scripts" / "install.ps1"),
             str(slot / "Scripts" / "python.exe"), str(project),
             str(state.home / ".agent-vault")],
            cwd=state.home, env=env, capture_output=True, text=True,
            timeout=30, **agent_procutil.no_window_kwargs(),
        )
    finally:
        if previous_guard is None:
            guard.unlink()
        else:
            guard.write_bytes(previous_guard)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Already running - PID 7" in result.stdout
