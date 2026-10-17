"""Exercise source selection with the real Windows launcher and GUI parent."""
from __future__ import annotations

import json
import os
import runpy
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.guard,
    pytest.mark.skipif(os.name != "nt", reason="native Windows launcher/window contract"),
]
ROOT = Path(__file__).resolve().parents[3]
HOSTS = [
    pytest.param(shutil.which(name), id=name)
    for name in ("pwsh", "powershell.exe")
]
FIXTURE = runpy.run_path(str(Path(__file__).with_name("test_source_install_transactions.py")))


def _ps(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _harness(tmp_path: Path, plugin: str) -> tuple[Path, str]:
    launcher = shutil.which("py")
    if not launcher:
        pytest.skip("The supported Windows Python launcher is unavailable")
    payload, selected = FIXTURE["_payload"](tmp_path, plugin)
    entry = "init" if plugin == "agent-mcp" else "install"
    installer = ROOT / "plugins" / plugin / "scripts" / f"{entry}.ps1"
    text = installer.read_text(encoding="utf-8")
    load = ". (Join-Path $PSScriptRoot 'versioned_runtime_native.ps1')"
    assert text.index(load) < text.index("Initialize-SourceRuntime")
    bootstrap = FIXTURE["_function"](plugin, "Get-BootstrapPython", "ps1")
    harness = tmp_path / "source-selection.ps1"
    harness.write_text(
        f"""
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$PluginDir = {_ps(payload)}
$ScriptDir = Join-Path $PluginDir 'scripts'
$InstallDir = {_ps(tmp_path / 'install')}
$VenvDir = Join-Path $InstallDir 'missing'
$LinkDir = $VenvDir
$env:PATH = {_ps(Path(launcher).parent)}
if (Get-Command python,python3 -ErrorAction SilentlyContinue) {{
    throw 'Fixture must expose the real py launcher without python or python3'
}}
. (Join-Path $ScriptDir 'versioned_runtime_native.ps1')
{bootstrap}
. (Join-Path $ScriptDir 'source-runtime.ps1')
for ($cycle = 0; $cycle -lt 2; $cycle++) {{
    $SrcVersion = '1.2.3'
    Initialize-SourceRuntime
    Assert-SourceRuntime
    if ($SrcVersion -ne '{selected}') {{ throw 'Source identity lost' }}
    Write-Output "selected:$cycle`:$SrcVersion"
    Start-Sleep -Milliseconds 100
}}
""",
        encoding="utf-8",
    )
    return harness, selected


@pytest.mark.parametrize("plugin", ["agent-mcp", "agent-codespaces"])
@pytest.mark.parametrize("host", HOSTS)
def test_source_selection_works_with_only_real_py_launcher(
    tmp_path: Path, plugin: str, host: str | None,
) -> None:
    if host is None:
        pytest.skip("PowerShell host unavailable")
    harness, selected = _harness(tmp_path, plugin)
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        capture_output=True, text=True, encoding="utf-8", timeout=40,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [f"selected:{cycle}:{selected}" for cycle in range(2)]
    assert not result.stderr


@pytest.mark.parametrize("plugin", ["agent-mcp", "agent-codespaces"])
@pytest.mark.parametrize("host", HOSTS)
def test_source_selection_stays_windowless_from_pythonw_for_two_cycles(
    tmp_path: Path, plugin: str, host: str | None,
) -> None:
    if host is None:
        pytest.skip("PowerShell host unavailable")
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        pytest.skip("GUI Python interpreter unavailable")
    harness, selected = _harness(tmp_path, plugin)
    output = tmp_path / "observed-result.json"
    parent = tmp_path / "windowless-parent.py"
    parent.write_text(
        "import ctypes,json,subprocess,sys\n"
        "from pathlib import Path\n"
        "result=subprocess.run([sys.argv[1],'-NoProfile','-NonInteractive','-File',sys.argv[2]],"
        "capture_output=True,text=True,encoding='utf-8',timeout=40,"
        "creationflags=subprocess.CREATE_NO_WINDOW)\n"
        "Path(sys.argv[3]).write_text(json.dumps({"
        "'console':ctypes.windll.kernel32.GetConsoleWindow(),"
        "'code':result.returncode,'stdout':result.stdout,'stderr':result.stderr}),"
        "encoding='utf-8')\n",
        encoding="utf-8",
    )
    visibility = runpy.run_path(
        str(Path(__file__).with_name("test_windows_launch_integration.py")),
    )
    user32, kernel32, callback_type = visibility["_configure_win32"]()
    visible_windows = visibility["_visible_windows"]
    process_name = visibility["_process_name"]
    names = visibility["_SURFACED_PROCESS_NAMES"] | {"pwsh.exe", "py.exe"}
    baseline = visible_windows(user32, callback_type)
    foreground = user32.GetForegroundWindow()
    surfaced: set[int] = set()
    focus_changes: set[int] = set()
    stopped = threading.Event()

    def observe() -> None:
        while not stopped.wait(0.01):
            surfaced.update(
                hwnd
                for hwnd in visible_windows(user32, callback_type) - baseline
                if process_name(user32, kernel32, hwnd) in names
            )
            current = user32.GetForegroundWindow()
            if current != foreground and process_name(user32, kernel32, current) in names:
                focus_changes.add(current)

    with ThreadPoolExecutor(max_workers=1) as executor:
        observation = executor.submit(observe)
        try:
            result = subprocess.run(
                [str(pythonw), "-I", str(parent), host, str(harness), str(output)],
                capture_output=True, timeout=50, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        finally:
            stopped.set()
            observation.result(timeout=2)
    assert result.returncode == 0, result.stderr
    assert output.is_file(), "Windowless parent failed before writing its captured result"
    recorded = json.loads(output.read_text(encoding="utf-8"))
    assert recorded["console"] == 0
    assert recorded["code"] == 0, recorded
    assert recorded["stdout"].splitlines() == [
        f"selected:{cycle}:{selected}" for cycle in range(2)
    ]
    assert not recorded["stderr"]
    assert not surfaced, f"Visible console windows: {surfaced}"
    assert not focus_changes, f"Console foreground transitions: {focus_changes}"
