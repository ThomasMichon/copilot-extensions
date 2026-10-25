"""Optional Windows bootstrap policy; package effects use a synthetic owner."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import struct
import sys
import tempfile

import pytest

from test_bootstrap_ps1 import BOOTSTRAP, _quote

pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows system Python bootstrap")


def _functions() -> str:
    source = BOOTSTRAP.read_text(encoding="utf-8")
    names = (
        "Write-BootstrapProgress", "Resolve-BootstrapExecutable", "Get-RegisteredBootstrapPython",
        "Initialize-BootstrapSignatureVerification", "Find-PhysicalBootstrapPython",
        "Test-BootstrapInteractive", "Find-SystemBootstrapPython",
    )
    functions = []
    for name in names:
        match = re.search(rf"^function {name}\b.*?^}}", source, re.M | re.S)
        assert match is not None, name
        functions.append(match[0])
    return "$BootstrapClock = [Diagnostics.Stopwatch]::StartNew()\n" + "\n".join(functions)


def _run(
    script: str, *, host_name: str | None = None, environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    host = (
        shutil.which(host_name) if host_name else
        shutil.which("pwsh") or shutil.which("powershell")
    )
    if host is None:
        pytest.skip(f"{host_name or 'PowerShell'} unavailable")
    with tempfile.TemporaryDirectory(prefix="bootstrap-system-") as temporary:
        entry = Path(temporary) / "probe.ps1"
        entry.write_text(script, encoding="utf-8-sig")
        return subprocess.run(
            [host, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(entry)],
            capture_output=True, text=True, timeout=30,
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )


def test_default_host_uses_builtin_powershell_when_pwsh_is_unavailable(monkeypatch):
    which = shutil.which
    if which("powershell") is None:
        pytest.skip("Windows PowerShell unavailable")
    monkeypatch.setattr(shutil, "which", lambda name: None if name == "pwsh" else which(name))
    try:
        result = _run("@{edition=$PSEdition} | ConvertTo-Json -Compress")
    except pytest.skip.Exception:
        pytest.fail("An available built-in host must not depend on optional PowerShell 7")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["edition"] == "Desktop"


@pytest.mark.parametrize(
    ("host_name", "outcome"),
    [("pwsh", outcome) for outcome in (
        "existing", "installed", "failed", "unqualified", "nonterminal", "no-winget",
    )] + [("powershell", "installed")],
)
def test_system_python_uses_qualified_owner_outcome(tmp_path, host_name, outcome):
    trace = tmp_path / "winget-args.txt"
    winget = tmp_path / "winget.ps1"
    winget.write_text(
        f"[IO.File]::WriteAllText({_quote(trace)}, ($args -join '|'))\n"
        f"exit {7 if outcome == 'failed' else 0}\n",
        encoding="utf-8",
    )
    python = Path(getattr(sys, "_base_executable", sys.executable))
    script = (
        "$ErrorActionPreference = 'Stop'\n" + _functions() + "\n"
        "$beforePath = $env:PATH\n"
        "$script:ProbeCount = 0\n"
        "function Find-PhysicalBootstrapPython([switch]$RequireSignature) {\n"
        "  if (-not $RequireSignature) { throw 'Signature qualification was omitted' }\n"
        "  $script:ProbeCount++\n"
        f"  if ({_quote(outcome)} -eq 'existing' -or "
        f"({_quote(outcome)} -eq 'installed' -and $script:ProbeCount -gt 1)) {{\n"
        f"    return {_quote(python)}\n"
        "  }\n"
        "}\n"
        "function Get-Command {\n"
        f"  if ({_quote(outcome)} -eq 'existing') {{ throw 'Unexpected package discovery' }}\n"
        f"  if ({_quote(outcome)} -eq 'no-winget') {{ return }}\n"
        f"  [PSCustomObject]@{{Source={_quote(winget)}}}\n"
        "}\n"
        f"function Test-BootstrapInteractive {{ return ${str(outcome != 'nonterminal').lower()} }}\n"
        "try {\n"
        "  $selected = Find-SystemBootstrapPython\n"
        "  @{selected=$selected; probes=$script:ProbeCount; error=''; "
        "pathChanged=($beforePath -cne $env:PATH)} | ConvertTo-Json -Compress\n"
        "} catch {\n"
        "  @{selected=''; probes=$script:ProbeCount; error=$_.Exception.Message; "
        "pathChanged=($beforePath -cne $env:PATH)} | ConvertTo-Json -Compress\n"
        "}\n"
    )
    result = _run(script, host_name=host_name)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    if outcome in ("existing", "installed"):
        assert Path(report["selected"]) == python
        assert not report["error"]
    else:
        expected = {
            "failed": "WinGet exit 7",
            "unqualified": "no usable signed Python 3.11+",
            "nonterminal": "real interactive Windows terminal",
            "no-winget": "requires WinGet",
        }[outcome]
        assert expected in report["error"]
        assert not report["selected"]
        assert not report["pathChanged"]
    assert report["probes"] == (2 if outcome in ("installed", "unqualified") else 1)
    if outcome in ("existing", "nonterminal", "no-winget"):
        assert not trace.exists()
    else:
        assert trace.read_text(encoding="utf-8") == (
            "install|--id|Python.Python.3.11|--exact|--source|winget|--scope|user|"
            "--accept-source-agreements|--accept-package-agreements"
        )


@pytest.mark.parametrize("host_name", ["pwsh", "powershell"])
def test_system_python_refuses_unsigned_candidate(tmp_path, host_name):
    unsigned = tmp_path / "unsigned-python.exe"
    executable = bytearray(Path(getattr(sys, "_base_executable", sys.executable)).read_bytes())
    pe = struct.unpack_from("<I", executable, 0x3C)[0]
    assert executable[pe:pe + 4] == b"PE\x00\x00"
    optional = pe + 24
    magic = struct.unpack_from("<H", executable, optional)[0]
    assert magic in (0x10B, 0x20B)
    directories = optional + (96 if magic == 0x10B else 112)
    certificate = directories + 4 * 8
    # Plain text is an indeterminate PE, not a native unsigned-executable control.
    executable[certificate:certificate + 8] = b"\x00" * 8
    unsigned.write_bytes(executable)
    script = (
        "$ErrorActionPreference = 'Stop'\n" + _functions() + "\n"
        "$script:Statuses = [Collections.Generic.List[string]]::new()\n"
        "Initialize-BootstrapSignatureVerification\n"
        "$script:VerifySignature = Get-Command Get-AuthenticodeSignature -CommandType Cmdlet -ErrorAction Stop\n"
        "function Get-RegisteredBootstrapPython { return @() }\n"
        "function Get-Command {\n"
        f"  [PSCustomObject]@{{Source={_quote(unsigned)}}}\n"
        "}\n"
        "function Get-AuthenticodeSignature {\n"
        "  param($LiteralPath, $ErrorAction)\n"
        "  $value = & $script:VerifySignature -LiteralPath $LiteralPath -ErrorAction Stop\n"
        "  $script:Statuses.Add([string]$value.Status)\n"
        "  return $value\n"
        "}\n"
        "$selected = Find-PhysicalBootstrapPython -RequireSignature\n"
        "@{selected=$selected; statuses=$script:Statuses.ToArray()} | ConvertTo-Json -Compress\n"
    )
    result = _run(script, host_name=host_name)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["selected"] is None
    assert report["statuses"] == ["NotSigned"]


@pytest.mark.parametrize("host_name", ["pwsh", "powershell"])
def test_signature_capability_uses_own_host_with_empty_inherited_module_path(tmp_path, host_name):
    script = (
        "$ErrorActionPreference = 'Stop'\n" + _functions() + "\n"
        "Initialize-BootstrapSignatureVerification\n"
        "$command = Get-Command Get-AuthenticodeSignature -CommandType Cmdlet -ErrorAction Stop\n"
        "@{name=$command.Name; kind=[string]$command.CommandType} | ConvertTo-Json -Compress\n"
    )
    result = _run(
        script, host_name=host_name,
        environment={**os.environ, "PSModulePath": str(tmp_path / "empty-modules")},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"name": "Get-AuthenticodeSignature", "kind": "Cmdlet"}


def test_missing_registered_candidate_is_absence_not_verifier_failure(tmp_path):
    missing = tmp_path / "missing-python.exe"
    script = (
        "$ErrorActionPreference = 'Stop'\n" + _functions() + "\n"
        "function Get-RegisteredBootstrapPython { return @() }\n"
        "function Get-Command {\n"
        f"  [PSCustomObject]@{{Source={_quote(missing)}}}\n"
        "}\n"
        "function Get-AuthenticodeSignature { throw 'Missing candidate was executed' }\n"
        "$selected = Find-PhysicalBootstrapPython -RequireSignature\n"
        "@{selected=$selected} | ConvertTo-Json -Compress\n"
    )
    result = _run(script)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["selected"] is None


def test_system_python_can_qualify_registered_interpreter_outside_path():
    python = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    script = (
        "$ErrorActionPreference = 'Stop'\n" + _functions() + "\n"
        "function Get-Command { return }\n"
        "function Test-Path { return $true }\n"
        "function Get-ChildItem { [PSCustomObject]@{PSPath='Registry::Fixture'} }\n"
        f"function Get-ItemProperty {{ [PSCustomObject]@{{ExecutablePath={_quote(python)}}} }}\n"
        "function Get-AuthenticodeSignature { [PSCustomObject]@{Status='Valid'} }\n"
        "$selected = Find-PhysicalBootstrapPython -RequireSignature\n"
        "@{selected=$selected} | ConvertTo-Json -Compress\n"
    )
    result = _run(script)
    assert result.returncode == 0, result.stdout + result.stderr
    assert Path(json.loads(result.stdout)["selected"]) == python


@pytest.mark.parametrize(
    "exception", ["InvalidOperationException", "OperationCanceledException", "UnknownError"],
)
def test_signature_probe_error_never_becomes_package_acquisition(exception):
    python = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    verification = (
        "[PSCustomObject]@{Status='UnknownError'}"
        if exception == "UnknownError" else
        f"throw [System.{exception}]::new('Signature verification stopped')"
    )
    script = (
        "$ErrorActionPreference = 'Stop'\n" + _functions() + "\n"
        "$script:PackageQueried = $false\n"
        "function Get-RegisteredBootstrapPython { return @() }\n"
        "function Get-Command {\n"
        "  if ($args[0] -eq 'winget') {\n"
        "    $script:PackageQueried = $true\n"
        "    throw 'Unexpected package acquisition'\n"
        "  }\n"
        f"  [PSCustomObject]@{{Source={_quote(python)}}}\n"
        "}\n"
        "function Get-AuthenticodeSignature {\n"
        f"  {verification}\n"
        "}\n"
        "try { Find-SystemBootstrapPython; throw 'Unexpected interpreter selection' }\n"
        "catch {\n"
        "  @{error=$_.Exception.Message; packageQueried=$script:PackageQueried} | "
        "ConvertTo-Json -Compress\n"
        "}\n"
    )
    result = _run(script)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    expected = "verification could not complete" if exception == "UnknownError" else "Signature verification stopped"
    assert expected in report["error"]
    assert not report["packageQueried"]


@pytest.mark.parametrize("enabled", [False, True])
def test_system_python_option_preserves_forwarded_setup_argv(enabled):
    source = BOOTSTRAP.read_text(encoding="utf-8")
    parsing = source.split("$installerArgs = @()", 1)[1].split("$RecoveryStaging = $null", 1)[0]
    arguments = ["setup", "--apply", "--repository", "https://github.com/example/sample.git"]
    selected = (["--system-python"] if enabled else []) + arguments
    script = (
        "& {\n" + parsing + "\n"
        "@{enabled=$SystemPythonRequested; args=@($commandArgs)} | ConvertTo-Json -Compress\n"
        "} " + " ".join(map(_quote, selected))
    )
    result = _run(script)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["enabled"] is enabled
    assert report["args"] == arguments


@pytest.mark.parametrize("extra", ["--system-python", "--recover-for-repair", "--repair-update"])
def test_system_python_rejects_repair_or_duplicate_before_acquisition(tmp_path, extra):
    root = tmp_path / "manager-root"
    result = _run(
        "$ErrorActionPreference='Stop'\n"
        f"& {_quote(BOOTSTRAP)} --system-python {_quote(extra)}\n",
        environment={**os.environ, "WORKTREE_MANAGER_ROOT": str(root)},
    )
    assert result.returncode != 0
    assert "cannot be repeated or combined with bootstrap repair" in result.stdout + result.stderr
    assert not root.exists()
