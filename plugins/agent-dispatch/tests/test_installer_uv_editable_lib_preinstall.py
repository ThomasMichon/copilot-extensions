"""Guard for install.ps1's policy-admitted native UV preinstall loop covering the
uv-editable canonical-reference libs (vendor-pointer-generalization effort):
dropin-registry, plugin-resolve, single-instance-lease, agent-procutil, worktree-identity,
plugin-activation. Exercises the loop via real pwsh execution with an offline
native UV and Resolve-VendoredLib's own canonical fallback to confirm both
resolution and the required dropin-registry/plugin-resolve-before-
plugin-activation install order -- a regression here would still pass
every other installer guard while silently reintroducing the bare-pip
dependency failure Copilot's review caught on PR #4420."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_FIXTURE_SPEC = importlib.util.spec_from_file_location(
    "_dispatch_owned_fixtures", Path(__file__).with_name("test_installer_engine_adoption.py")
)
assert _FIXTURE_SPEC is not None and _FIXTURE_SPEC.loader is not None
_FIXTURES = importlib.util.module_from_spec(_FIXTURE_SPEC)
_FIXTURE_SPEC.loader.exec_module(_FIXTURES)
LIBRARIES = _FIXTURES.LIBRARIES
_stage_owned_source = _FIXTURES._stage_owned_source

PLUGIN = Path(__file__).resolve().parents[1]
INSTALLER = PLUGIN / "scripts" / "install.ps1"

pytestmark = pytest.mark.guard


def _extract(start: str, end: str) -> str:
    text = INSTALLER.read_text(encoding="utf-8")
    return start + text.split(start, 1)[1].split(end, 1)[0]


def _resolve_vendored_lib_fn() -> str:
    return _extract("function Resolve-VendoredLib {", "\nfunction Resolve-Zdd")


def _preinstall_loop() -> str:
    return _extract(
        "foreach ($lib in @(\n        @{ Dir = 'dropin-registry';",
        "\n\n    # -ReinstallPackage",
    )


def _owned_fixture_process(argv: list[str], *, cwd: Path, env: dict) -> subprocess.CompletedProcess[str]:
    """Bound the entire extracted-loop descendant tree and both captured pipes."""
    from agent_procutil.owned_process import run_caller_owned
    child = """
import json, os, subprocess, sys, time
from pathlib import Path
from agent_procutil.owned_process import run_caller_owned
request = json.load(sys.stdin)
progress = Path(request["env"]["TEST_UV_PROGRESS"])
with progress.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps({"stage": "owner-start", "pid": os.getpid(), "parent": os.getppid()}) + "\\n")
os.chdir(request["cwd"])
os.environ.clear()
os.environ.update(request["env"])
remaining = request["deadline"] - time.monotonic()
if remaining <= 0:
    raise subprocess.TimeoutExpired(request["argv"], 30)
result = run_caller_owned(request["argv"], timeout=remaining,
                         max_stdout_bytes=1048576, max_stderr_bytes=1048576)
with progress.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps({"stage": "scope-reaped", "returncode": result.returncode}) + "\\n")
sys.stdout.write(result.stdout)
sys.stderr.write(result.stderr)
raise SystemExit(result.returncode)
"""
    request = json.dumps({"argv": argv, "cwd": str(cwd), "env": env,
                          "deadline": time.monotonic() + 30})
    return run_caller_owned(
        [sys.executable, "-I", "-c", child], timeout=30, input_text=request,
        max_stdout_bytes=1048576, max_stderr_bytes=1048576,
    )


def _run(pwsh: str, plugin_dir: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    local = (plugin_dir / "libs").is_dir()
    library_root = plugin_dir / "libs" if local else tmp_path / "libs"
    engine = _stage_owned_source(
        plugin_dir,
        {package: library_root / lib for package, lib in LIBRARIES.items() if lib != "zdd"},
        repository=None if local else tmp_path,
    )
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    driver = fake_bin / "uv-driver.py"
    driver.write_text(
        """import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with Path(os.environ["TEST_UV_PROGRESS"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps({"stage": "uv-enter", "pid": os.getpid(), "argv": args}) + "\\n")
if args[:3] == ["pip", "install", "--show-settings"]:
    print("PipInstallSettings { settings: PipSettings {\\n    index_locations: IndexLocations {\\n        flat_index: [],\\n    },\\n    exclude_newer: ExcludeNewer {\\n        global: None,\\n        package: ExcludeNewerPackage({}),\\n    },\\n}, }")
    raise SystemExit(0)
""" + _FIXTURES._retained_uv_fixture_prelude() + """
assert args[:2] == ["pip", "install"], args
with Path(os.environ["TEST_UV_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\\n")
""",
        encoding="utf-8",
    )
    uv = fake_bin / ("uv.cmd" if os.name == "nt" else "uv")
    uv.write_text(
        f'@"{sys.executable}" "{driver}" %*\n@exit /b %ERRORLEVEL%\n'
        if os.name == "nt"
        else f'#!/bin/sh\nexec "{sys.executable}" "{driver}" "$@"\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)
    env = os.environ.copy()
    for key in tuple(env):
        if key.startswith(("AGENT_", "COPILOT_PLUGIN_", "PYTHON", "UV_")):
            env.pop(key)
    for key in (
        "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
        "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
        "XDG_RUNTIME_DIR", "TEMP", "TMP", "TMPDIR", "COPILOT_HOME",
    ):
        root = tmp_path / key.lower()
        root.mkdir()
        env[key] = str(root)
    env["PATH"] = str(fake_bin)
    env["TEST_UV_LOG"] = str(tmp_path / "uv.jsonl")
    env["TEST_UV_PROGRESS"] = str(tmp_path / "progress.jsonl")
    env["TEST_UV_EXPECTED_SOURCES"] = json.dumps({
        package: str(library_root / lib) for package, lib in LIBRARIES.items() if lib != "zdd"
    })
    env["COPILOT_EXTENSIONS_TEST_CONTAINED"] = "1"
    script = f"""
$ErrorActionPreference = 'Stop'
function Remove-PluginBuildArtifacts {{ param([string]$PluginDir, [string]$ExtraDir) }}
function Write-Fail {{ param([string]$Message) throw $Message }}
function Write-Ok {{ param([string]$Message) [Console]::Out.Write($Message + "|") }}
[IO.File]::AppendAllText($env:TEST_UV_PROGRESS, '{{"stage":"powershell-start","pid":' + $PID + '}}' + [Environment]::NewLine)
{_resolve_vendored_lib_fn()}
$PluginDir = '{plugin_dir}'
$BuildSrcDir = '{plugin_dir}'
$VenvPython = '{sys.executable}'
$UvCommand = '{uv}'
$prevEAP = 'Stop'
. '{engine / 'installer-engine.ps1'}'
function Write-Warn {{ param($Message) Write-Host $Message }}
function Start-Sleep {{ param($Seconds) }}
{_preinstall_loop()}
[IO.File]::AppendAllText($env:TEST_UV_PROGRESS, '{{"stage":"loop-complete"}}' + [Environment]::NewLine)
"""
    script_path = tmp_path / "extracted-preinstall-loop.ps1"
    script_path.write_text(script, encoding="utf-8-sig")
    result = None
    try:
        result = _owned_fixture_process(
            [pwsh, "-NoProfile", "-NonInteractive", "-File", str(script_path)],
            cwd=tmp_path, env=env,
        )
        return result
    except subprocess.TimeoutExpired:
        progress = Path(env["TEST_UV_PROGRESS"])
        detail = progress.read_text(encoding="utf-8") if progress.exists() else "no child progress"
        pytest.fail(f"extracted offline UV loop exceeded unchanged 30s owned scope; {detail}")
    finally:
        if destination := os.environ.get("EXTRACTED_UV_EVIDENCE_DIR"):
            directory = Path(destination).resolve()
            if not directory.is_relative_to(PLUGIN.parents[1]):
                raise ValueError("extracted-loop evidence must remain in the owned source checkout")
            directory.mkdir(parents=True, exist_ok=True)
            receipt = {
                "argv": [pwsh, "-NoProfile", "-NonInteractive", "-File", str(script_path)],
                "script_sha256": hashlib.sha256(script_path.read_bytes()).hexdigest(),
                "deadline_seconds": 30,
                "owned_scope_completed": result is not None,
                "returncode": result.returncode if result is not None else None,
            }
            for name in ("progress", "uv"):
                file = tmp_path / f"{name}.jsonl"
                receipt[name] = ([json.loads(line) for line in file.read_text(encoding="utf-8").splitlines()]
                                 if file.exists() else [])
            (directory / f"{tmp_path.name}.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")


def _installed_paths(tmp_path: Path) -> list[str]:
    calls = [
        json.loads(line)
        for line in (tmp_path / "uv.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    packages = (
        "agent-dropin-registry", "agent-plugin-resolve",
        "agent-single-instance-lease", "agent-procutil", "agent-worktree-identity",
        "agent-plugin-activation",
    )
    assert len(calls) == len(packages)
    for call, package in zip(calls, packages):
        assert call[:4] == ["pip", "install", "--python", sys.executable]
        assert call[5:10] == [
            "--reinstall-package", package, "--refresh-package", package, "--quiet",
        ]
        assert len(call) == 13
        assert call[-3] == "--exclude-newer"
        assert call[-1] == "--reinstall"
        cutoff = datetime.fromisoformat(call[-2].replace("Z", "+00:00"))
        assert cutoff <= datetime.now(timezone.utc) - timedelta(seconds=604800)
    return [call[4] for call in calls]


def test_preinstall_loop_resolves_each_lib_and_installs_in_order(tmp_path: Path):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    plugin_dir = tmp_path / "plugins" / "agent-dispatch"
    for lib in ("dropin-registry", "plugin-resolve", "single-instance-lease", "agent-procutil",
                "worktree-identity", "plugin-activation"):
        lib_dir = plugin_dir / "libs" / lib
        lib_dir.mkdir(parents=True)
        (lib_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    proc = _run(pwsh, plugin_dir, tmp_path)
    assert proc.returncode == 0, proc.stderr

    installs = _installed_paths(tmp_path)
    assert len(installs) == 6
    order = [Path(p).name for p in installs]
    assert order.index("plugin-activation") > order.index("dropin-registry")
    assert order.index("plugin-activation") > order.index("plugin-resolve")
    assert order.index("plugin-activation") > order.index("worktree-identity")
    assert all(Path(p).parent == plugin_dir / "libs" for p in installs)
    assert "PipInstallSettings" not in proc.stdout + proc.stderr

    oks = [seg for seg in proc.stdout.split("|") if seg]
    assert oks == [
        "dropin-registry installed",
        "plugin-resolve installed",
        "single-instance-lease installed",
        "agent-procutil installed",
        "worktree-identity installed",
        "plugin-activation installed",
    ]


def test_preinstall_loop_falls_back_to_repo_root_canonical_via_resolve_vendored_lib(
    tmp_path: Path,
):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    plugin_dir = tmp_path / "plugins" / "agent-dispatch"
    plugin_dir.mkdir(parents=True)
    # No plugins/agent-dispatch/libs/<lib> for any of them -- dev checkout,
    # uv-editable canonical reference with no per-plugin copy.
    canonical = tmp_path / "libs"
    for lib in ("dropin-registry", "plugin-resolve", "single-instance-lease", "agent-procutil",
                "worktree-identity", "plugin-activation"):
        lib_dir = canonical / lib
        lib_dir.mkdir(parents=True)
        (lib_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    proc = _run(pwsh, plugin_dir, tmp_path)
    assert proc.returncode == 0, proc.stderr
    installs = _installed_paths(tmp_path)
    assert len(installs) == 6
    for resolved in installs:
        assert Path(resolved).resolve().parent == canonical.resolve()
    assert "PipInstallSettings" not in proc.stdout + proc.stderr
