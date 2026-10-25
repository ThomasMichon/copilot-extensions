"""Native contracts for the independently admitted evaluator interpreter."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agent_procutil import no_window_kwargs

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "libs/installer-engine/bootstrap-python.ps1"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows bootstrap")


@pytest.fixture(params=("pwsh", "powershell"))
def host(request):
    executable = shutil.which(request.param)
    if not executable:
        pytest.skip(f"{request.param} is unavailable")
    return executable


def _run(host, tmp_path, body, *, environment=None, success=True):
    script = tmp_path / "probe.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)\n"
        ". $env:SELECTION_HELPER\n" + body,
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True, text=True, encoding="utf-8",
        env={
            **os.environ, "SELECTION_HELPER": str(HELPER), "PYTHONUTF8": "1",
            **(environment or {}),
        },
        timeout=20, **no_window_kwargs(),
    )
    if success:
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        return json.loads(result.stdout)
    return result


def _registration_fixture():
    return (
        "function Get-EvaluatorBootstrapRegistrySnapshot {\n"
        "    [pscustomobject]@{Root=$env:FIXTURE_ROOT; Python=$null; "
        "Tag='opaque'; Rank=0; Arguments=$null}\n"
        "}\n"
    )


@pytest.fixture
def base_copy(tmp_path):
    directory = tmp_path / "base's \u00e9"
    directory.mkdir()
    fixture_python = os.environ.get("EVALUATOR_BOOTSTRAP_TEST_PYTHON", sys._base_executable)
    shutil.copy2(fixture_python, directory / "python.exe")
    return directory


def test_registration_precedence_is_case_insensitive_and_does_not_probe(host, tmp_path):
    result = _run(host, tmp_path, r"""
function Get-EvaluatorBootstrapRegistrySnapshot {
    [pscustomobject]@{Root='C:\machine'; Python=$null; Tag='SAME'; Rank=1; Arguments=$null}
    [pscustomobject]@{Root='C:\user'; Python=$null; Tag='same'; Rank=0; Arguments=$null}
    [pscustomobject]@{Root='C:\other'; Python='C:\other\python.exe'; Tag='opaque'; Rank=2; Arguments=$null}
    [pscustomobject]@{Root=$null; Python=$null; Tag='blocked'; Rank=0; Arguments=$null}
    [pscustomobject]@{Root='C:\shadowed'; Python=$null; Tag='BLOCKED'; Rank=1; Arguments=$null}
    [pscustomobject]@{Root='C:\argument'; Python=$null; Tag='arg'; Rank=0; Arguments='-m other'}
    [pscustomobject]@{Root='C:\typed'; Python=$null; Tag='typed'; Rank=0; Arguments=0}
}
function Get-Command { throw 'Discovery must not resolve a command.' }
@(Get-EvaluatorBootstrapRegistrations) | ConvertTo-Json -Compress
""")
    assert [(row["Tag"], row["Python"]) for row in result] == [
        ("same", r"C:\user\python.exe"),
        ("opaque", r"C:\other\python.exe"),
    ]


def test_discovery_has_no_command_or_candidate_execution_surface(host, tmp_path):
    result = _run(host, tmp_path, r"""
$errors=$null
$tokens=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:SELECTION_HELPER, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Bootstrap helper must parse cleanly.' }
$commands=@($ast.FindAll({
    param($node)
    $node -is [Management.Automation.Language.CommandAst]
}, $true))
$forbidden=@($commands | Where-Object {
    $_.InvocationOperator -ne [Management.Automation.Language.TokenKind]::Unknown -or
    $_.GetCommandName() -in @('Get-Command','Start-Process','Invoke-Expression','Invoke-Command')
})
[pscustomobject]@{forbidden=$forbidden.Count} | ConvertTo-Json -Compress
""")
    assert result == {"forbidden": 0}
    text = HELPER.read_text(encoding="utf-8")
    assert "Process.Start" not in text
    assert "CreateProcess" not in text
    assert "data.Flags = 0x1000;" in text
    assert "data.Ui = 2;" in text


@pytest.mark.parametrize(
    "kind", ("relative", "remote", "outside", "venv", "venv-parent", "embedded", "excluded")
)
def test_unfit_registration_never_reaches_trust_or_candidate_execution(
    host, tmp_path, base_copy, kind
):
    root = str(base_copy)
    python = str(base_copy / "python.exe")
    excluded = ""
    if kind == "relative":
        python = "python.exe"
    elif kind == "remote":
        python = r"\\server\share\python.exe"
    elif kind == "outside":
        root = str(tmp_path)
    elif kind == "venv":
        (base_copy / "pyvenv.cfg").write_text("home = outside\n", encoding="utf-8")
    elif kind == "venv-parent":
        root += "\\"
        (tmp_path / "pyvenv.cfg").write_text("home = outside\n", encoding="utf-8")
    elif kind == "embedded":
        (base_copy / "python._pth").write_text("import site\n", encoding="utf-8")
    elif kind == "excluded":
        excluded = str(base_copy)
    result = _run(
        host, tmp_path,
        "function Get-EvaluatorBootstrapRegistrySnapshot {\n"
        "  [pscustomobject]@{Root=$env:FIXTURE_ROOT; Python=$env:FIXTURE_PYTHON; "
        "Tag='opaque'; Rank=0; Arguments=$null}\n"
        "}\n"
        "function Initialize-EvaluatorBootstrapTrust { throw 'Unfit path reached trust initialization.' }\n"
        "$s=Resolve-EvaluatorBootstrapPython -ExcludedRoots @($env:EXCLUDED_ROOT)\n"
        "[pscustomobject]@{status=$s.Status; python=$s.Python} | ConvertTo-Json -Compress\n",
        environment={
            "FIXTURE_ROOT": root, "FIXTURE_PYTHON": python, "EXCLUDED_ROOT": excluded,
        },
    )
    assert result == {"status": "unavailable", "python": None}


def test_absent_bootstrap_does_not_initialize_native_trust(host, tmp_path):
    result = _run(host, tmp_path, r"""
function Get-EvaluatorBootstrapRegistrySnapshot {}
function Initialize-EvaluatorBootstrapTrust { throw 'No candidate requires no compiler or native verifier.' }
$s=Resolve-EvaluatorBootstrapPython
[pscustomobject]@{status=$s.Status; reason=$s.Reason; python=$s.Python} | ConvertTo-Json -Compress
""")
    assert result == {
        "status": "unavailable", "reason": "no-independent-base", "python": None,
    }


def test_registry_failure_is_terminal_not_missing_bootstrap(host, tmp_path):
    result = _run(host, tmp_path, r"""
function Get-EvaluatorBootstrapRegistrySnapshot { throw 'synthetic registry read refused' }
$ErrorActionPreference='Continue'
Resolve-EvaluatorBootstrapPython
""", success=False)
    assert result.returncode != 0
    assert "synthetic registry read refused" in result.stderr
    assert "unavailable" not in result.stdout


def test_native_verifier_failure_is_terminal(host, tmp_path, base_copy):
    result = _run(host, tmp_path, _registration_fixture() + r"""
function Initialize-EvaluatorBootstrapTrust { throw 'synthetic native verifier unavailable' }
Resolve-EvaluatorBootstrapPython
""", environment={"FIXTURE_ROOT": str(base_copy)}, success=False)
    assert result.returncode != 0
    assert "synthetic native verifier unavailable" in result.stderr
    assert "unavailable" not in result.stdout


def test_native_signature_selects_synthetic_base_without_running_it(host, tmp_path, base_copy):
    result = _run(host, tmp_path, _registration_fixture() + r"""
$s=Resolve-EvaluatorBootstrapPython
try {
    if ($s.Status -eq 'selected') {
        $locked=$false
        try {
            $writer=[IO.File]::Open($s.Python, [IO.FileMode]::Open, [IO.FileAccess]::Write, [IO.FileShare]::ReadWrite)
            $writer.Dispose()
        } catch [IO.IOException] { $locked=$true }
        [pscustomobject]@{status=$s.Status; python=$s.Python; locked=$locked} | ConvertTo-Json -Compress
    } else {
        [pscustomobject]@{status=$s.Status; reason=$s.Reason} | ConvertTo-Json -Compress
    }
} finally {
    if ($s.Guard) { $s.Guard.Dispose() }
}
""", environment={"FIXTURE_ROOT": str(base_copy)})
    if result["status"] != "selected":
        if os.environ.get("EVALUATOR_BOOTSTRAP_TEST_PYTHON"):
            pytest.fail(f"explicit native admission fixture was refused: {result['reason']}")
        pytest.skip(f"fixture interpreter is not independently admissible: {result['reason']}")
    assert Path(result["python"]) == base_copy / "python.exe"
    assert result["locked"] is True
    # No runtime packages or stdlib are constructed in this synthetic base.
    # Signature and PE admission above are native checks, not a --version mock.
    assert sorted(path.name for path in base_copy.iterdir()) == ["python.exe"]
    with (base_copy / "python.exe").open("r+b"):
        pass


def test_valid_other_publisher_is_not_bootstrap_authority(host, tmp_path, base_copy):
    other = shutil.which("pwsh")
    if not other:
        pytest.skip("embedded-signature other-publisher fixture requires pwsh")
    shutil.copy2(other, base_copy / "python.exe")
    result = _run(host, tmp_path, _registration_fixture() + r"""
Initialize-EvaluatorBootstrapTrust
$path=Join-Path $env:FIXTURE_ROOT 'python.exe'
$guard=[IO.File]::OpenRead($path)
try {
    $observed=[CopilotExtensions.EvaluatorBootstrapTrustV1]::Verify($path, $guard.SafeFileHandle)
} finally { $guard.Dispose() }
function Get-EvaluatorBootstrapVersion { param([string]$Path); [version]'3.12.1.0' }
$s=Resolve-EvaluatorBootstrapPython
try {
    [pscustomobject]@{status=$s.Status; trust=$observed.Status; publisher=$observed.Publisher} |
        ConvertTo-Json -Compress
} finally { if ($s.Guard) { $s.Guard.Dispose() } }
""", environment={"FIXTURE_ROOT": str(base_copy)})
    assert result["trust"] == 0
    assert result["publisher"]
    assert result["publisher"] != "Python Software Foundation"
    assert result["status"] == "unavailable"


def test_equal_version_candidates_have_ordinal_path_order(host, tmp_path, base_copy):
    roots = [tmp_path / "\u00e5-base", tmp_path / "z-base"]
    for root in roots:
        root.mkdir()
        shutil.copy2(base_copy / "python.exe", root / "python.exe")
    result = _run(host, tmp_path, r"""
function Get-EvaluatorBootstrapRegistrySnapshot {
    [pscustomobject]@{Root=$env:ROOT_FIRST; Python=$null; Tag='opaque-a'; Rank=0; Arguments=$null}
    [pscustomobject]@{Root=$env:ROOT_SECOND; Python=$null; Tag='opaque-z'; Rank=0; Arguments=$null}
}
$s=Resolve-EvaluatorBootstrapPython
try {
    [pscustomobject]@{status=$s.Status; python=$s.Python; reason=$s.Reason} | ConvertTo-Json -Compress
} finally { if ($s.Guard) { $s.Guard.Dispose() } }
""", environment={"ROOT_FIRST": str(roots[0]), "ROOT_SECOND": str(roots[1])})
    if result["status"] != "selected":
        if os.environ.get("EVALUATOR_BOOTSTRAP_TEST_PYTHON"):
            pytest.fail(f"explicit native ordering fixture was refused: {result['reason']}")
        pytest.skip(f"fixture interpreter is not independently admissible: {result['reason']}")
    assert Path(result["python"]) == roots[1] / "python.exe"


@pytest.mark.parametrize("change", ("signature", "architecture", "unsigned"))
def test_modified_native_executable_is_not_executed(host, tmp_path, base_copy, change):
    python = base_copy / "python.exe"
    with python.open("r+b") as stream:
        if change == "signature":
            stream.seek(64)
            original = stream.read(1)
            stream.seek(64)
            stream.write(bytes([original[0] ^ 1]))
        elif change == "architecture":
            stream.seek(0x3C)
            pe = int.from_bytes(stream.read(4), "little")
            stream.seek(pe + 4)
            stream.write(b"\x00\x00")
        else:
            stream.seek(0x3C)
            optional = int.from_bytes(stream.read(4), "little") + 24
            stream.seek(optional)
            magic = int.from_bytes(stream.read(2), "little")
            assert magic in (0x10B, 0x20B)
            directories = optional + (112 if magic == 0x20B else 96)
            stream.seek(directories + 4 * 8)
            stream.write(b"\x00" * 8)
    control = ""
    if change == "unsigned":
        control = r"""
Initialize-EvaluatorBootstrapTrust
$path=Join-Path $env:FIXTURE_ROOT 'python.exe'
$guard=[IO.File]::OpenRead($path)
try {
    $trust=[CopilotExtensions.EvaluatorBootstrapTrustV1]::Verify($path, $guard.SafeFileHandle)
    if ($trust.Status -ne [Convert]::ToUInt32('800B0100',16)) { throw 'Expected native missing-signature refusal.' }
} finally { $guard.Dispose() }
"""
    result = _run(
        host, tmp_path,
        _registration_fixture() + control
        + "$s=Resolve-EvaluatorBootstrapPython\n"
        + "[pscustomobject]@{status=$s.Status; python=$s.Python} | ConvertTo-Json -Compress\n",
        environment={"FIXTURE_ROOT": str(base_copy)},
    )
    assert result == {"status": "unavailable", "python": None}
    assert sorted(path.name for path in base_copy.iterdir()) == ["python.exe"]


def test_linked_base_is_not_admitted(host, tmp_path, base_copy):
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(base_copy, target_is_directory=True)
    except OSError as error:
        if error.winerror == 1314:
            pytest.skip("Windows symlink permission is unavailable")
        raise
    result = _run(
        host, tmp_path, _registration_fixture()
        + "function Initialize-EvaluatorBootstrapTrust { throw 'Linked base reached verifier.' }\n"
        + "$s=Resolve-EvaluatorBootstrapPython\n"
        + "[pscustomobject]@{status=$s.Status; python=$s.Python} | ConvertTo-Json -Compress\n",
        environment={"FIXTURE_ROOT": str(alias)},
    )
    assert result == {"status": "unavailable", "python": None}


@pytest.mark.parametrize("alias_location", ("candidate", "exclusion"))
def test_short_path_cannot_alias_an_excluded_runtime(host, tmp_path, base_copy, alias_location):
    import ctypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    get_short = kernel.GetShortPathNameW
    get_short.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
    get_short.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    size = get_short(str(base_copy), buffer, len(buffer))
    assert 0 < size < len(buffer), ctypes.get_last_error()
    short = buffer.value
    if short.casefold() == str(base_copy).casefold():
        pytest.skip("the fixture volume does not supply a distinct short-path alias")
    result = _run(
        host, tmp_path, _registration_fixture()
        + "$s=Resolve-EvaluatorBootstrapPython -ExcludedRoots @($env:EXCLUDED_ROOT)\n"
        + "try { [pscustomobject]@{status=$s.Status; python=$s.Python} | ConvertTo-Json -Compress }\n"
        + "finally { if ($s.Guard) { $s.Guard.Dispose() } }\n",
        environment={
            "FIXTURE_ROOT": short if alias_location == "candidate" else str(base_copy),
            "EXCLUDED_ROOT": str(base_copy) if alias_location == "candidate" else short,
        },
    )
    assert result == {"status": "unavailable", "python": None}


def test_linked_exclusion_cannot_hide_the_addressed_runtime(host, tmp_path, base_copy):
    alias = tmp_path / "runtime-alias"
    try:
        alias.symlink_to(base_copy, target_is_directory=True)
    except OSError as error:
        if error.winerror == 1314:
            pytest.skip("Windows symlink permission is unavailable")
        raise
    result = _run(
        host, tmp_path, _registration_fixture()
        + "$control=Resolve-EvaluatorBootstrapPython\n"
        + "if ($control.Status -cne 'selected') { throw 'explicit fixture was not admitted' }\n"
        + "$control.Guard.Dispose()\n"
        + "function Initialize-EvaluatorBootstrapTrust { throw 'Linked exclusion reached verifier.' }\n"
        + "$s=Resolve-EvaluatorBootstrapPython -ExcludedRoots @($env:EXCLUDED_ROOT)\n"
        + "try { [pscustomobject]@{status=$s.Status; python=$s.Python} | ConvertTo-Json -Compress }\n"
        + "finally { if ($s.Guard) { $s.Guard.Dispose() } }\n",
        environment={"FIXTURE_ROOT": str(base_copy), "EXCLUDED_ROOT": str(alias)},
    )
    assert result == {"status": "unavailable", "python": None}


@pytest.fixture(params=("subst", "native"))
def drive_alias(request, tmp_path):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.DefineDosDeviceW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR)
    kernel.DefineDosDeviceW.restype = wintypes.BOOL
    kernel.QueryDosDeviceW.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD)
    kernel.QueryDosDeviceW.restype = wintypes.DWORD
    kernel.GetLogicalDrives.restype = wintypes.DWORD
    used = kernel.GetLogicalDrives()
    assert used, ctypes.WinError(ctypes.get_last_error())
    device = next((chr(65 + bit) + ":" for bit in range(25, 12, -1) if not used & (1 << bit)), None)
    assert device is not None, "native alias fixture requires an unused drive letter"
    buffer = ctypes.create_unicode_buffer(32768)
    assert not kernel.QueryDosDeviceW(device, buffer, len(buffer))
    assert ctypes.get_last_error() in (2, 3)
    if request.param == "native":
        assert kernel.QueryDosDeviceW(tmp_path.drive, buffer, len(buffer))
        target = buffer.value + str(tmp_path)[2:]
        flags = 0x1 | 0x8
    else:
        target = str(tmp_path)
        flags = 0x8
    assert kernel.DefineDosDeviceW(flags, device, target), ctypes.WinError(ctypes.get_last_error())
    try:
        yield Path(device + "\\")
    finally:
        exact_target = target if request.param == "native" else "\\??\\" + target
        assert kernel.DefineDosDeviceW(0xF, device, exact_target), ctypes.WinError(ctypes.get_last_error())


@pytest.mark.parametrize("location", ("candidate", "exclusion", "missing-exclusion"))
def test_dos_device_alias_cannot_supply_independence(
    host, tmp_path, base_copy, drive_alias, location,
):
    alias = drive_alias / base_copy.name
    result = _run(
        host, tmp_path, _registration_fixture()
        + "$control=Resolve-EvaluatorBootstrapPython\n"
        + "if ($control.Status -cne 'selected') { throw 'explicit fixture was not admitted' }\n"
        + "$control.Guard.Dispose()\n"
        + "$env:FIXTURE_ROOT=$env:TEST_REGISTRATION_ROOT\n"
        + "$s=Resolve-EvaluatorBootstrapPython -ExcludedRoots @($env:EXCLUDED_ROOT)\n"
        + "try { [pscustomobject]@{status=$s.Status; python=$s.Python} | ConvertTo-Json -Compress }\n"
        + "finally { if ($s.Guard) { $s.Guard.Dispose() } }\n",
        environment={
            "FIXTURE_ROOT": str(base_copy),
            "TEST_REGISTRATION_ROOT": str(alias if location == "candidate" else base_copy),
            "EXCLUDED_ROOT": str(
                base_copy if location == "candidate" else
                alias / "missing/runtime" if location == "missing-exclusion" else alias
            ),
        },
    )
    assert result == {"status": "unavailable", "python": None}


def test_remote_exclusion_never_reaches_verification(host, tmp_path, base_copy):
    result = _run(
        host, tmp_path, _registration_fixture()
        + "function Initialize-EvaluatorBootstrapTrust { throw 'Remote exclusion reached verifier.' }\n"
        + "$s=Resolve-EvaluatorBootstrapPython -ExcludedRoots @('\\\\not-contacted.invalid\\share')\n"
        + "[pscustomobject]@{status=$s.Status; python=$s.Python} | ConvertTo-Json -Compress\n",
        environment={"FIXTURE_ROOT": str(base_copy)},
    )
    assert result == {"status": "unavailable", "python": None}


def test_missing_exclusion_suffix_keeps_independent_admission(host, tmp_path, base_copy):
    result = _run(
        host, tmp_path, _registration_fixture()
        + "$s=Resolve-EvaluatorBootstrapPython -ExcludedRoots @($env:EXCLUDED_ROOT)\n"
        + "try { [pscustomobject]@{status=$s.Status; python=$s.Python} | ConvertTo-Json -Compress }\n"
        + "finally { if ($s.Guard) { $s.Guard.Dispose() } }\n",
        environment={"FIXTURE_ROOT": str(base_copy), "EXCLUDED_ROOT": str(tmp_path / "missing/runtime")},
    )
    assert result == {"status": "selected", "python": str(base_copy / "python.exe")}


def test_guarded_metadata_drift_is_terminal(host, tmp_path, base_copy):
    result = _run(host, tmp_path, _registration_fixture() + r"""
$script:metadataReads=0
function Get-EvaluatorBootstrapVersion {
    param([string]$Path)
    $script:metadataReads++
    if ($script:metadataReads -eq 1) { return [version]'3.12.1.0' }
    return [version]'3.12.2.0'
}
Resolve-EvaluatorBootstrapPython
""", environment={"FIXTURE_ROOT": str(base_copy)}, success=False)
    assert result.returncode != 0
    assert "changed during admission" in result.stderr
    assert "selected" not in result.stdout
