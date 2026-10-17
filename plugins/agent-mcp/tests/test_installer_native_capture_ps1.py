"""Execute the installer's native boundary, without provisioning a runtime."""

from __future__ import annotations

import base64
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

INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "init.ps1"
HOSTS = [
    pytest.param(shutil.which(name), id=name)
    for name in ("pwsh", "powershell.exe")
    if name == "pwsh" or os.name == "nt"
]


def _quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _helper() -> str:
    return ". " + _quote(INSTALLER.parent / "versioned_runtime_native.ps1") + "\n"


def _run(host: str | None, script: str) -> subprocess.CompletedProcess[str]:
    if host is None:
        pytest.skip("PowerShell host unavailable")
    script = "$ProgressPreference = 'SilentlyContinue'\n" + script
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


@pytest.mark.parametrize("host", HOSTS)
def test_native_helper_preserves_python_flags_and_literal_argv(host: str | None) -> None:
    arguments = ["--", "two words", 'embedded"quote', r"a\"b", "trailing\\", "", "-I"]
    code = "import json,sys; print(json.dumps([sys.flags.isolated, sys.argv[1:]]))"
    argv = ", ".join(_quote(arg) for arg in ["-I", "-c", code, *arguments])
    result = _run(
        host,
        _helper()
        + f"""
$ErrorActionPreference = 'Stop'
$output = Invoke-Hidden -FilePath {_quote(sys.executable)} -ArgList @({argv})
if ($LASTEXITCODE -ne 0) {{ throw 'native child failed' }}
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    {_quote(INSTALLER)}, [ref]$tokens, [ref]$errors)
if ($errors.Count) {{ throw 'installer syntax errors' }}
$calls = $ast.FindAll({{
    param($node)
    $node -is [Management.Automation.Language.CommandAst] -and
        $node.GetCommandName() -eq 'Invoke-Hidden'
}}, $true)
foreach ($call in $calls) {{
    $parameters = @($call.CommandElements | Where-Object {{
        $_ -is [Management.Automation.Language.CommandParameterAst]
    }} | ForEach-Object {{ $_.ParameterName }})
    if ($parameters -notcontains 'FilePath' -or $parameters -notcontains 'ArgList') {{
        throw 'native call must bind an explicit argv array'
    }}
}}
Write-Output $output
""",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == [1, arguments]
    assert not result.stderr


@pytest.mark.parametrize("host", HOSTS)
def test_native_helper_drains_stderr_and_retains_failure(host: str | None) -> None:
    code = (
        "import sys; "
        "sys.stderr.write('policy-denied\\n' + 'E' * 131072 + '\\n'); "
        "sys.stderr.flush(); print('stdout-retained'); sys.exit(17)"
    )
    result = _run(
        host,
        _helper()
        + f"""
$ErrorActionPreference = 'Stop'
$output = Invoke-Hidden -FilePath {_quote(sys.executable)} -ArgList @('-I', '-c', {_quote(code)})
Write-Output "native-exit=$LASTEXITCODE"
Write-Output $output
""",
    )
    assert result.returncode == 0, result.stdout + result.stderr[:300]
    assert result.stdout.strip().splitlines() == ["native-exit=17", "stdout-retained"]
    assert "policy-denied" in result.stderr
    assert "E" * 131072 in result.stderr


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("have_uv", [True, False])
def test_preinstall_executes_real_native_helper(
    host: str | None,
    have_uv: bool,
    tmp_path: Path,
) -> None:
    text = INSTALLER.read_text(encoding="utf-8")
    start = text.index("foreach ($lib in @(\n    @{ Dir = 'credential-relay';")
    loop = text[start : text.index("\n\nif (Get-Command uv ", start)]
    plugin = tmp_path / "payload with spaces"
    scripts = plugin / "scripts"
    scripts.mkdir(parents=True)
    policy = scripts / "installer_dependency_policy.py"
    policy.write_text(
        "import json,sys\nprint(json.dumps([sys.flags.isolated, sys.argv[1:]]))\n",
        encoding="utf-8",
    )
    libraries = ("credential-relay", "agent-procutil", "single-instance-lease", "zdd")
    for name in libraries:
        lib = plugin / "libs" / name
        lib.mkdir(parents=True)
        (lib / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    # Retain the policy call's stdout only for assertions; the production loop
    # discards it after the real native helper has captured both child streams.
    loop = loop.replace(" | Out-Null", "")
    result = _run(
        host,
        _helper()
        + f"""
$ErrorActionPreference = 'Stop'
$PluginDir = {_quote(plugin)}
$PSScriptRoot = {_quote(scripts)}
$VenvPython = {_quote(sys.executable)}
function Get-Command {{
    param($Name)
    if ($Name -ne 'uv') {{ throw 'unexpected command discovery' }}
    if (${str(have_uv).lower()}) {{ return 'fixture-uv' }}
}}
function Write-Fail {{ param($Message) throw $Message }}
{loop}
""",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert len(records) == len(libraries)
    for record, name in zip(records, libraries, strict=True):
        assert record[0] == 1
        expected = ["--uv", "uv", "--", "--python", sys.executable]
        if have_uv:
            package = name if name == "agent-procutil" else f"agent-{name}"
            expected += ["--reinstall-package", package, str(plugin / "libs" / name), "--quiet"]
        else:
            expected += ["--quiet", str(plugin / "libs" / name)]
        assert record[1] == expected


@pytest.mark.parametrize("host", HOSTS)
def test_owned_construction_cleanup_preserves_primary_failure(
    host: str | None,
    tmp_path: Path,
) -> None:
    text = INSTALLER.read_text(encoding="utf-8")
    lock_start = text.index("# === install-transaction lock (fail-closed) ===")
    lock_end = text.index("# === end install-transaction lock ===", lock_start)
    lock = text[lock_start:lock_end]
    finalization = text[text.rindex("\n} catch {\n    $script:McpInstallFailure = $_") :]
    helper = INSTALLER.parent / "versioned_runtime.py"
    adapter = runpy.run_path(str(INSTALLER.parent / "versioned_runtime_construction.py"))
    shell = adapter["construction_shell_helpers"]("ps1")
    native = _helper()
    denial = "import sys; sys.stderr.write('dependency-fixture-denied\\n'); sys.exit(17)"
    root = tmp_path / "owned runtime"
    old = root / "versions" / "1.0"
    old.mkdir(parents=True)
    (old / "previous").write_text("unchanged", encoding="utf-8")
    (root / "current-version").write_text("1.0", encoding="utf-8")
    result = _run(
        host,
        f"""
$ErrorActionPreference = 'Stop'
$WarningPreference = 'Stop'
Set-StrictMode -Version 2.0
$InstallDir = {_quote(root)}
{lock}
{shell}
{native}
$script:McpInstallFailure = $null
$code = 0
if (-not (Enter-InstallLock -TimeoutSec 0)) {{ throw 'initial lock failed' }}
try {{
    try {{
        $reuse = Start-RuntimeConstruction -Python {_quote(sys.executable)} `
            -Helper {_quote(helper)} -Root $InstallDir -Version '2.0' `
            -PayloadHash ('a' * 64) -LinkName '.venv'
        [IO.File]::WriteAllText({_quote(root / "versions" / "2.0" / "partial")}, 'partial')
        Invoke-Hidden -FilePath {_quote(sys.executable)} `
            -ArgList @('-I', '-c', {_quote(denial)}) | Out-Null
        if ($LASTEXITCODE -ne 0) {{ throw 'original-policy-failure' }}
        throw 'native denial was not exercised'
{finalization}
}} catch {{
    [Console]::Error.WriteLine($_.Exception.Message)
    $code = 1
}}
if ($script:InstallLockHandle) {{ throw 'install lock survived cleanup' }}
if (-not (Enter-InstallLock -TimeoutSec 0)) {{ throw 'lock could not be reacquired' }}
Exit-InstallLock
Write-Output 'lock-reacquired'
exit $code
""",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "original-policy-failure" in result.stderr
    assert "dependency-fixture-denied" in result.stderr
    assert "owned incomplete candidate was reclaimed" in result.stdout + result.stderr
    assert "lock-reacquired" in result.stdout
    assert not (root / "versions" / "2.0").exists()
    assert (root / "current-version").read_text(encoding="utf-8") == "1.0"
    assert (old / "previous").read_text(encoding="utf-8") == "unchanged"


@pytest.mark.skipif(os.name != "nt", reason="native Windows visibility contract")
@pytest.mark.parametrize("host", HOSTS)
def test_native_helper_stays_windowless_for_two_cycles(host: str | None) -> None:
    visibility = runpy.run_path(
        str(Path(__file__).with_name("test_windows_launch_integration.py"))
    )
    user32, kernel32, callback_type = visibility["_configure_win32"]()
    visible_windows = visibility["_visible_windows"]
    process_name = visibility["_process_name"]
    names = visibility["_SURFACED_PROCESS_NAMES"] | {"pwsh.exe"}
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

    child = "import ctypes,time; print(ctypes.windll.kernel32.GetConsoleWindow()); time.sleep(0.1)"
    with ThreadPoolExecutor(max_workers=1) as executor:
        observation = executor.submit(observe)
        try:
            result = _run(
                host,
                _helper()
                + f"""
$ErrorActionPreference = 'Stop'
for ($cycle = 0; $cycle -lt 2; $cycle++) {{
    Invoke-Hidden -FilePath {_quote(sys.executable)} -ArgList @('-I', '-c', {_quote(child)})
    if ($LASTEXITCODE -ne 0) {{ throw 'console descendant failed' }}
    Start-Sleep -Milliseconds 100
}}
""",
            )
        finally:
            stopped.set()
            observation.result(timeout=2)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.split() == ["0", "0"]
    assert not surfaced, f"visible console windows: {surfaced}"
    assert not focus_changes, f"console foreground transitions: {focus_changes}"
