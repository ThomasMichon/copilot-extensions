"""Native Windows contracts for Dispatch's explicit signed interpreter choice."""
from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
HEADLESS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


@pytest.fixture(params=["pwsh", "powershell"])
def shell(request: pytest.FixtureRequest) -> str:
    executable = shutil.which(request.param)
    if os.name != "nt" or executable is None:
        pytest.skip("native Windows PowerShell required")
    return executable


def native(tmp_path: Path, shell: str, body: str) -> subprocess.CompletedProcess[str]:
    installer = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
    atomic = re.search(r"(?ms)^function Publish-FileAtomically \{.*?^\}", installer)
    assert atomic
    harness = tmp_path / "base-python.ps1"
    harness.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f". '{SCRIPTS / 'base-python.ps1'}'\n"
        + atomic.group(0) + "\n"
        f"$InstallDir = '{tmp_path / 'runtime'}'\n"
        "New-Item -ItemType Directory $InstallDir -Force | Out-Null\n"
        "$base = Join-Path $InstallDir 'python.exe'\n"
        "[IO.File]::WriteAllText($base, 'fixture-not-an-executable')\n"
        "$env:AGENT_DISPATCH_BASE_PYTHON = $null\n"
        "$script:probes = 0\n"
        "function Get-AuthenticodeSignature {\n"
        " param($LiteralPath)\n"
        " $cert = New-Object PSObject\n"
        " $cert | Add-Member ScriptMethod GetNameInfo { param($type,$issuer) "
        "return 'Python Software Foundation' }\n"
        " return [pscustomobject]@{Status='Valid';SignerCertificate=$cert}\n"
        "}\n"
        "function Invoke-Hidden {\n"
        " param($FilePath,$ArgList,[ref]$ErrorOutput)\n"
        " $script:probes++; $global:LASTEXITCODE = 0\n"
        " return (@{implementation='cpython';version=@(3,13,16);"
        "machine='AMD64';bits=64;executable=$base;base_executable=$base}"
        " | ConvertTo-Json -Compress)\n"
        "}\n"
        + body,
        encoding="utf-8",
    )
    return subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        capture_output=True, text=True, timeout=25, creationflags=HEADLESS,
    )


def test_choice_priority_and_later_update_persistence(tmp_path: Path, shell: str) -> None:
    result = native(tmp_path, shell, """
if ($null -ne (Resolve-DispatchBasePython)) { throw 'default discovery changed' }
if ($script:probes -ne 0) { throw 'default unnecessarily probed' }
$env:AGENT_DISPATCH_BASE_PYTHON = $base
$selected = Resolve-DispatchBasePython
if ($selected -cne $base) { throw 'explicit choice ignored' }
if (Test-Path (Join-Path $InstallDir 'base-python.json')) { throw 'choice persisted before publication' }
Publish-DispatchBasePython -Python $selected
$env:AGENT_DISPATCH_BASE_PYTHON = $null
$SrcVersion = '9.9.9'
if ((Resolve-DispatchBasePython) -cne $base) { throw 'next update lost base' }
$other = Join-Path $InstallDir 'other.exe'
[IO.File]::WriteAllText($other, 'fixture')
$base = $other
$env:AGENT_DISPATCH_BASE_PYTHON = $other
if ((Resolve-DispatchBasePython) -cne $other) { throw 'stored choice beat explicit input' }
$stored = Get-Content (Join-Path $InstallDir 'base-python.json') -Raw | ConvertFrom-Json
if ($stored.python -ceq $other) { throw 'unpublished override changed stored choice' }
""")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("invalid", [
    "missing", "relative", "unsigned", "publisher", "version", "implementation", "architecture", "probe", "array",
])
def test_invalid_choice_fails_closed(tmp_path: Path, shell: str, invalid: str) -> None:
    mutation = {
        "missing": "$base = Join-Path $InstallDir 'missing.exe'",
        "relative": "$base = 'python.exe'",
        "unsigned": "function Get-AuthenticodeSignature { param($LiteralPath) return @{Status='NotSigned'} }",
        "publisher": """function Get-AuthenticodeSignature {
param($LiteralPath)
$cert = New-Object PSObject
$cert | Add-Member ScriptMethod GetNameInfo { param($type,$issuer) return 'Other Publisher' }
return @{Status='Valid';SignerCertificate=$cert}
}""",
        "version": """function Invoke-Hidden { param($FilePath,$ArgList,[ref]$ErrorOutput)
$script:probes++; $global:LASTEXITCODE=0
return (@{implementation='cpython';version=@(3,9,0);machine='AMD64';bits=64;
    executable=$base;base_executable=$base} | ConvertTo-Json -Compress)
}""",
        "probe": """function Invoke-Hidden { param($FilePath,$ArgList,[ref]$ErrorOutput)
$script:probes++; $global:LASTEXITCODE=17; return 'not Python'
}""",
        "implementation": """function Invoke-Hidden { param($FilePath,$ArgList,[ref]$ErrorOutput)
$script:probes++; $global:LASTEXITCODE=0
return (@{implementation='pypy';version=@(3,13,0);machine='AMD64';bits=64;
    executable=$base;base_executable=$base} | ConvertTo-Json -Compress)
}""",
        "architecture": """function Invoke-Hidden { param($FilePath,$ArgList,[ref]$ErrorOutput)
$script:probes++; $global:LASTEXITCODE=0
return (@{implementation='cpython';version=@(3,13,0);machine='unsupported';bits=64;
    executable=$base;base_executable=$base} | ConvertTo-Json -Compress)
}""",
        "array": """function Invoke-Hidden { param($FilePath,$ArgList,[ref]$ErrorOutput)
$script:probes++; $global:LASTEXITCODE=0
return ('[' + (@{implementation='cpython';version=@(3,13,0);machine='AMD64';bits=64;
    executable=$base;base_executable=$base} | ConvertTo-Json -Compress) + ']')
}""",
    }[invalid]
    result = native(tmp_path, shell, mutation + """
$env:AGENT_DISPATCH_BASE_PYTHON = $base
$refused = $false
try { Resolve-DispatchBasePython | Out-Null } catch { $refused = $true }
if (-not $refused) { throw 'invalid explicit base accepted' }
if (Test-Path (Join-Path $InstallDir 'base-python.json')) { throw 'failure published preference' }
""" + (
        "if ($script:probes -ne 0) { throw 'untrusted path executed' }\n"
        if invalid in {"missing", "relative", "unsigned", "publisher"} else ""
    ))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("document", ["bad-json", '{"version":2,"python":"missing"}', '{"version":1}', '{"version":1,"python":7}', "array"])
def test_invalid_stored_preference_refuses(
    tmp_path: Path, shell: str, document: str,
) -> None:
    if document == "array":
        document = json.dumps([{"version": 1, "python": str(tmp_path / "runtime" / "python.exe")}])
    result = native(tmp_path, shell, f"""
[IO.File]::WriteAllText((Join-Path $InstallDir 'base-python.json'), '{document}')
$refused = $false
try {{ Resolve-DispatchBasePython | Out-Null }} catch {{ $refused = $true }}
if (-not $refused -or $script:probes -ne 0) {{ throw 'malformed preference executed or ignored' }}
""")
    assert result.returncode == 0, result.stdout + result.stderr


def test_candidate_base_mismatch_preserves_candidate(tmp_path: Path, shell: str) -> None:
    result = native(tmp_path, shell, """
$candidate = Join-Path $InstallDir 'candidate.exe'
[IO.File]::WriteAllText($candidate, 'immutable-candidate')
Assert-DispatchCandidateBase -Python $candidate -BasePython $base
$different = Join-Path $InstallDir 'other.exe'
$refused = $false
try { Assert-DispatchCandidateBase -Python $candidate -BasePython $different } catch { $refused = $true }
if (-not $refused) { throw 'mismatched completed candidate accepted' }
if ([IO.File]::ReadAllText($candidate) -cne 'immutable-candidate') { throw 'candidate mutated' }
""")
    assert result.returncode == 0, result.stdout + result.stderr


def test_preference_directory_is_not_followed(tmp_path: Path, shell: str) -> None:
    result = native(tmp_path, shell, """
New-Item -ItemType Directory (Join-Path $InstallDir 'base-python.json') | Out-Null
$env:AGENT_DISPATCH_BASE_PYTHON = $base
$refused = $false
try { Resolve-DispatchBasePython | Out-Null } catch { $refused = $true }
if (-not $refused -or $script:probes -ne 0) { throw 'non-file preference followed' }
""")
    assert result.returncode == 0, result.stdout + result.stderr


def test_linked_preference_is_not_read_or_written(tmp_path: Path, shell: str) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    target = tmp_path / "foreign-preference.json"
    original = '{"version":1,"python":"foreign"}'
    target.write_text(original, encoding="utf-8")
    try:
        (root / "base-python.json").symlink_to(target)
    except OSError as error:
        if error.winerror == 1314:
            pytest.skip("Windows does not permit unprivileged symbolic links")
        raise
    result = native(tmp_path, shell, """
$env:AGENT_DISPATCH_BASE_PYTHON = $base
$refusals = 0
try { Resolve-DispatchBasePython | Out-Null } catch { $refusals++ }
try { Publish-DispatchBasePython -Python $base } catch { $refusals++ }
if ($refusals -ne 2 -or $script:probes -ne 0) { throw 'linked preference followed' }
""")
    assert result.returncode == 0, result.stdout + result.stderr
    assert target.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("outcome", [
    "published", "constructed", "dependencies", "health", "active-superseded", "stamp-superseded",
    "publication", "activation", "base-mismatch",
])
def test_runtime_publication_gates_preference(
    tmp_path: Path, shell: str, outcome: str,
) -> None:
    installer = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
    runtime = re.search(r"(?ms)^function Install-Runtime \{.*?^\}", installer)
    assert runtime
    fixture_python = tmp_path / "fixture-python.ps1"
    fixture_python.write_text(
        "if ($args -contains '-c') {\n"
        + ("exit 23\n" if outcome == "health" else "exit 0\n")
        + "}\nexit 0\n",
        encoding="utf-8",
    )
    body = runtime.group(0) + f"""
$env:AGENT_DISPATCH_BASE_PYTHON = $base
$SrcVersion = '1.2.3'
$VersionedRuntime = $true
$Force = $false
$PluginDir = $InstallDir
$PkgSrcDir = $PluginDir
$LocalBin = $InstallDir
$LinkPython = '{fixture_python}'
$VenvPython = $LinkPython
$VenvDir = Join-Path $InstallDir 'candidate'
$script:InstallerDependencyPolicy = 'original-policy'
$script:SourceRuntimeIdentity = $null
$script:GlobalActivationLockTimeoutSeconds = 5
$script:RuntimeConstructionReuse = ${'false' if outcome in {'dependencies', 'constructed'} else 'true'}
$script:created = $false
$script:mcpInstalled = $false
[IO.File]::WriteAllText((Join-Path $InstallDir 'current-version'), 'previous-healthy')
$originalPreference = '{{"version":1,"python":"previous-choice"}}'
[IO.File]::WriteAllText((Join-Path $InstallDir 'base-python.json'), $originalPreference)
function Assert-SourceRuntime {{ }}
function Enter-PluginSnapshotLock {{ param($InstallDir,$Version,$TimeoutSeconds) return [Threading.Mutex]::new($true) }}
function New-PluginBuildSnapshot {{ param($PluginDir,$InstallDir,$Version,[switch]$BestEffort) return $PluginDir }}
function Ensure-Uv {{ param($InstallRoot) return 'fixture-uv' }}
function Invoke-VersionedSlotClean {{ }}
function Test-DispatchVenv {{
    param($Dir,$Python)
    if ('{outcome}' -eq 'constructed') {{ return $script:created }}
    return $true
}}
function Write-Ok {{ param($Msg) }}
function Write-Warn {{ param($Msg) }}
function Write-Skip {{ param($Msg) }}
function Write-Step {{ param($Msg) }}
function Write-Fail {{ param($Msg) throw $Msg }}
function Remove-ConsoleTrampolines {{ param($VenvDir) }}
function Remove-PluginBuildArtifacts {{ param($PluginDir,$ExtraDir) }}
function Resolve-Zdd {{ param($LibRoot) return $InstallDir }}
function Resolve-VendoredLib {{ param($LibName,$LibRoot) return $InstallDir }}
function Invoke-UvPipInstallResilient {{
    param($UvCommand,$Arguments)
    if ('{outcome}' -eq 'constructed') {{
        if ($Arguments -contains "$($InstallDir)[mcp]") {{ $script:mcpInstalled = $true }}
        return @{{ExitCode=0;Output=''}}
    }}
    return @{{ExitCode=19;Output='dependency-build-refused'}}
}}
function Get-VersionedCurrent {{ return 'previous-healthy' }}
function Invoke-VersionedMarkComplete {{
    if ('{outcome}' -eq 'constructed' -and (-not $script:created -or -not $script:mcpInstalled)) {{
        throw 'construction or required MCP gate bypassed'
    }}
    return $true
}}
function Invoke-SupervisorAudit {{ param($Event,$Reason,$Outcome) }}
function Invoke-VersionedActivate {{
    if ('{outcome}' -eq 'activation') {{ return $false }}
    if ('{outcome}' -eq 'health') {{ throw 'failed health reached activation' }}
    if ('{outcome}' -notin @('active-superseded','stamp-superseded')) {{
        [IO.File]::WriteAllText((Join-Path $InstallDir 'current-version'), $SrcVersion)
    }}
    return $true
}}
function Test-ActivationSupersededNow {{ return ('{outcome}' -eq 'active-superseded') }}
function Test-VersionLt {{ param($A,$B) return $true }}
function Deploy-SelfProvisioningBinstub {{ }}
function Write-Manifest {{ if ('{outcome}' -eq 'publication') {{ throw 'publication-refused' }} }}
function Register-PickerPivot {{ }}
function Invoke-VersionedGc {{ param($KeepPrev) throw 'publication-boundary' }}
if ('{outcome}' -eq 'stamp-superseded') {{
    [IO.File]::WriteAllText((Join-Path $InstallDir 'stamped-version'), '9.9.9')
}}
if ('{outcome}' -eq 'base-mismatch') {{
    function Invoke-Hidden {{
        param($FilePath,$ArgList,[ref]$ErrorOutput)
        $global:LASTEXITCODE=0
        $reported = if ($FilePath -ceq $base) {{ $base }} else {{ Join-Path $InstallDir 'wrong.exe' }}
        return (@{{implementation='cpython';version=@(3,13,16);machine='AMD64';bits=64;
            executable=$reported;base_executable=$reported}} | ConvertTo-Json -Compress)
    }}
}}
if ('{outcome}' -eq 'constructed') {{
    $script:identityProbe = ${{function:Invoke-Hidden}}
    function Invoke-Hidden {{
        param($FilePath,$ArgList,[ref]$ErrorOutput)
        if ($ArgList -contains 'venv') {{
            if ($FilePath -cne $base -or $ArgList -notcontains '--copies') {{ throw 'wrong venv base or copy policy' }}
            $script:created = $true
            $global:LASTEXITCODE = 0
            return ''
        }}
        & $script:identityProbe -FilePath $FilePath -ArgList $ArgList -ErrorOutput $ErrorOutput
    }}
    function Get-Command {{ param($Name) throw 'explicit base fell through to discovery' }}
}}
try {{
    Install-Runtime
    if ('{outcome}' -notin @('active-superseded','stamp-superseded')) {{ throw 'unexpected-return' }}
}} catch {{
    $message = $_.Exception.Message
    $expected = switch ('{outcome}') {{
        'published' {{ 'publication-boundary' }}
        'constructed' {{ 'publication-boundary' }}
        'dependencies' {{ 'zdd install failed' }}
        'health' {{ 'failed its health gate' }}
        'publication' {{ 'publication-refused' }}
        'base-mismatch' {{ 'different base Python' }}
        default {{ throw }}
    }}
    if (-not $message.Contains($expected)) {{ throw }}
}}
$after = [IO.File]::ReadAllText((Join-Path $InstallDir 'base-python.json'))
if ('{outcome}' -in @('published','constructed')) {{
    if (($after | ConvertFrom-Json).python -cne $base) {{ throw 'successful publication lost choice' }}
}} elseif ($after -cne $originalPreference) {{ throw 'unsuccessful publication changed preference' }}
if ('{outcome}' -in @('dependencies','health','activation','base-mismatch')) {{
    if ([IO.File]::ReadAllText((Join-Path $InstallDir 'current-version')) -cne 'previous-healthy') {{
        throw 'failure changed previously selected runtime'
    }}
}}
if ($script:InstallerDependencyPolicy -cne 'original-policy') {{ throw 'policy not restored' }}
"""
    result = native(tmp_path, shell, body)
    assert result.returncode == (1 if outcome == "activation" else 0), result.stdout + result.stderr
    root = tmp_path / "runtime"
    preference = json.loads((root / "base-python.json").read_text(encoding="utf-8"))
    assert preference["python"] == (
        str(root / "python.exe") if outcome in {"published", "constructed"} else "previous-choice"
    )
    if outcome in {"dependencies", "health", "activation", "base-mismatch"}:
        assert (root / "current-version").read_text() == "previous-healthy"


def test_real_signed_base_and_candidate(
    tmp_path: Path, shell: str, record_property: Callable[[str, object], None],
) -> None:
    import ctypes
    from ctypes import wintypes

    from .test_windows_durable_launch_lifetime import DesktopProbe

    selected = os.environ.get("AGENT_DISPATCH_TEST_SIGNED_BASE")
    if not selected:
        pytest.skip("explicit read/execute opt-in for an installed PSF-signed base required")
    harness = tmp_path / "real-signed-base.ps1"
    harness.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f". '{SCRIPTS / 'versioned_runtime_native.ps1'}'\n"
        f". '{SCRIPTS / 'base-python.ps1'}'\n"
        "$script:realHidden = ${function:Invoke-Hidden}\n"
        "$script:probeCount = 0\n"
        "function Invoke-Hidden {\n"
        " param($FilePath,$ArgList,[ref]$ErrorOutput)\n"
        " if ($ArgList -contains '-c') {\n"
        "  $script:probeCount++\n"
        f"  $receipt = Join-Path '{tmp_path}' \"console-$script:probeCount.json\"\n"
        "  $prefix = \"import ctypes,json,os; from pathlib import Path; Path(r'$receipt').write_text(json.dumps(dict(pid=os.getpid(),hwnd=ctypes.windll.kernel32.GetConsoleWindow()))); \"\n"
        "  $ArgList = @($ArgList)\n"
        "  $ArgList[-1] = $prefix + $ArgList[-1]\n"
        " }\n"
        f" $phase = Join-Path '{tmp_path}' 'construction-active'\n"
        " $constructing = $ArgList -contains 'venv'\n"
        " if ($constructing) { [IO.File]::WriteAllText($phase, '1') }\n"
        " try { & $script:realHidden -FilePath $FilePath -ArgList $ArgList -ErrorOutput $ErrorOutput }\n"
        " finally { if ($constructing) { [IO.File]::WriteAllText($phase, '0') } }\n"
        "}\n"
        f"$InstallDir = '{tmp_path / 'runtime'}'\n"
        "$env:AGENT_DISPATCH_BASE_PYTHON = $env:AGENT_DISPATCH_TEST_SIGNED_BASE\n"
        "$base = Resolve-DispatchBasePython\n"
        "for ($cycle = 0; $cycle -lt 2; $cycle++) {\n"
        " if ((Assert-DispatchSignedBasePython -Python $base) -cne $base) { throw 'base changed' }\n"
        "}\n"
        f"$candidate = '{tmp_path / 'candidate'}'\n"
        "$diagnostic = ''\n"
        "$output = Invoke-Hidden -FilePath $base -ArgList @('-I','-B','-m','venv','--copies',$candidate) -ErrorOutput ([ref]$diagnostic)\n"
        "if ($LASTEXITCODE -ne 0) { throw \"$output$diagnostic\" }\n"
        "Assert-DispatchCandidateBase -Python (Join-Path $candidate 'Scripts\\python.exe') -BasePython $base\n"
        "if (Test-Path (Join-Path $InstallDir 'base-python.json')) { throw 'probe published preference' }\n"
        "Write-Output 'real-signed-base-and-candidate'\n",
        encoding="utf-8",
    )
    desktop = DesktopProbe()
    record_property("interactive_desktop_observation", desktop.available)
    if not desktop.available:
        pytest.skip("interactive desktop required for the opt-in visibility proof")
    stopped = threading.Event()
    observations: list[BaseException] = []
    construction: set[int] = set()
    nested_python: set[int] = set()

    class Entry(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD), ("usage", wintypes.DWORD),
            ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
            ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
            ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
            ("flags", wintypes.DWORD), ("name", wintypes.WCHAR * 260),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ("Process32FirstW", "Process32NextW"):
        getattr(kernel, name).argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]

    def parents() -> dict[int, tuple[int, str]]:
        snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = Entry()
            entry.size = ctypes.sizeof(entry)
            result = {}
            present = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while present:
                result[entry.pid] = (entry.parent, entry.name.lower())
                present = kernel.Process32NextW(snapshot, ctypes.byref(entry))
            if ctypes.get_last_error() != 18:
                raise ctypes.WinError(ctypes.get_last_error())
            return result
        finally:
            kernel.CloseHandle(snapshot)

    def observe() -> None:
        try:
            while not stopped.wait(0.005):
                processes = parents()
                previous = -1
                while previous != len(desktop.owned):
                    previous = len(desktop.owned)
                    desktop.owned.update(pid for pid, (parent, _) in processes.items() if parent in desktop.owned)
                phase = tmp_path / "construction-active"
                if phase.exists() and phase.read_text() == "1":
                    construction.update(desktop.owned & processes.keys())
                    nested_python.update(
                        pid for pid in construction
                        if pid in processes and processes[pid][1] == "python.exe"
                        and processes.get(processes[pid][0], (0, ""))[1] == "python.exe"
                    )
                for receipt in tmp_path.glob("console-*.json"):
                    try:
                        document = json.loads(receipt.read_text())
                    except json.JSONDecodeError:
                        continue
                    desktop.owned.add(document["pid"])
                    if document["hwnd"]:
                        desktop.console_windows.add(document["hwnd"])
                desktop.sample()
        except BaseException as error:
            observations.append(error)

    with subprocess.Popen(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=HEADLESS,
    ) as process:
        desktop.owned.add(process.pid)
        observer = threading.Thread(target=observe)
        observer.start()
        try:
            stdout, stderr = process.communicate(timeout=25)
            result = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
        finally:
            stopped.set()
            observer.join()
    assert not observations, observations
    assert result.returncode == 0, result.stdout + result.stderr
    assert "real-signed-base-and-candidate" in result.stdout
    assert len(list(tmp_path.glob("console-*.json"))) >= 4
    assert nested_python, "ensurepip Python descendants were not observed during construction"
    record_property("construction_processes_observed", len(construction))
    assert not desktop.surfaced, desktop.surfaced
    assert not desktop.focus, desktop.focus
