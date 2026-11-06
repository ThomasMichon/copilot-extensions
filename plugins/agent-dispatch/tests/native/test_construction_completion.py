"""Join Dispatch's native completion caller to the real construction owner."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
SHELLS = [(name, shutil.which(name)) for name in ("powershell", "pwsh")]
DIGEST = "a" * 64
pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.skipif(os.name != "nt", reason="Windows completion consumer"),
]


def _quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _function(source: str, name: str) -> str:
    match = re.search(
        rf"^function {re.escape(name)}\s*\{{.*?^\}}[ \t]*\r?$",
        source, re.MULTILINE | re.DOTALL,
    )
    assert match is not None, name
    return match.group()


@pytest.mark.parametrize("shell,executable", SHELLS)
@pytest.mark.parametrize("case", ["packaged", "missing-bootstrap", "modified-hook"])
def test_dispatch_completion_preserves_pywin32_policy_and_child_error(
    tmp_path: Path, shell: str, executable: str | None, case: str,
):
    if executable is None:
        pytest.skip(f"{shell} unavailable")
    payload = tmp_path / "payload"
    payload.mkdir()
    for name in (
        "runtime_launcher.py", "versioned_runtime.py", "versioned_runtime_leases.py",
        "versioned_runtime_construction.py", "versioned_runtime_source.py",
        "versioned_runtime_publication.py",
    ):
        shutil.copyfile(SCRIPTS / name, payload / name)
    root = tmp_path / "runtime"
    old_slot = root / "versions" / "1.0.0"
    old_slot.mkdir(parents=True)
    (old_slot / ".install-complete.json").write_text(json.dumps({
        "version": "1.0.0", "payload_hash": "b" * 64,
        "completed_at": "2026-01-01T00:00:00Z",
    }), encoding="utf-8")
    (root / "current-version").write_text("1.0.0\n", encoding="utf-8")
    (root / "config.json").write_text('{"keep":"existing configuration"}\n', encoding="utf-8")
    (root / "deploy-manifest.json").write_text('{"version":"1.0.0"}\n', encoding="utf-8")
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    candidate = root / "versions" / "1.2.3"
    site = candidate / "Lib" / "site-packages"
    executed = tmp_path / "hook-executed"
    hook = f"from pathlib import Path\nPath({str(executed)!r}).touch()\n"
    setup = payload / "package_fixture.py"
    setup.write_text(
        "from pathlib import Path\n"
        f"site = Path({str(site)!r})\n"
        "library = site / 'win32' / 'lib'\n"
        "library.mkdir(parents=True)\n"
        "(site / 'pywin32_system32').mkdir()\n"
        "(site / 'pythonwin').mkdir()\n"
        "(site.parent.parent / 'pyvenv.cfg').write_text("
        "'include-system-site-packages = false\\n')\n"
        + (
            f"(library / 'pywin32_bootstrap.py').write_text({hook!r})\n"
            if case != "missing-bootstrap" else ""
        )
        + f"(site / 'unverified_hook.py').write_text({hook!r})\n"
        + "(site / 'pywin32.pth').write_text("
        + repr(
            "# .pth file for the PyWin32 extensions\nwin32\nwin32\\lib\npythonwin\n"
            "import pywin32_bootstrap"
            + ("; import unverified_hook\n" if case == "modified-hook" else "\n")
        ) + ")\n",
        encoding="utf-8",
    )
    source = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
    functions = "\n".join(_function(source, name) for name in (
        "Invoke-VersionedSlotClean", "Invoke-VersionedMarkComplete",
    ))
    script = payload / "probe.ps1"
    script.write_text(f"""
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$InstallDir = {_quote(root)}
$LinkDir = Join-Path $InstallDir '.venv'
$SrcVersion = '1.2.3'
$VersionedRuntime = $true
$script:RuntimeConstructionSession = $null
$script:SourceRuntimeIdentity = $null
$PluginDir = {_quote(payload)}
. {_quote(SCRIPTS / 'source-runtime.ps1')}
function Get-BootstrapPython {{ return {_quote(sys.executable)} }}
function Get-PayloadHash {{ return {_quote(DIGEST)} }}
{functions}
$code = 0
$ownerId = $null
try {{
    Invoke-VersionedSlotClean
    $ownerId = $script:RuntimeConstructionSession.Process.Id
    [Console]::Out.WriteLine("HELPER_PID=$ownerId")
    & {_quote(sys.executable)} -I {_quote(setup)}
    if ($LASTEXITCODE -ne 0) {{ throw 'Packaged input fixture failed' }}
    if (-not (Invoke-VersionedMarkComplete)) {{ throw 'Completion was not acknowledged' }}
    [Console]::Out.WriteLine('COMPLETED')
}} catch {{
    [Console]::Error.WriteLine($_.Exception.Message)
    $code = 1
}} finally {{
    try {{ Stop-RuntimeConstruction }} catch {{
        [Console]::Error.WriteLine($_.Exception.Message)
        $code = 1
    }}
    if ($script:RuntimeConstructionSession) {{ throw 'Construction owner did not settle' }}
    if ($ownerId) {{
        $remaining = Get-Process -Id $ownerId -ErrorAction SilentlyContinue
        if ($remaining) {{
            $remaining.Dispose()
            throw 'Construction owner remained live'
        }}
        [Console]::Out.WriteLine('HELPER_SETTLED')
    }}
}}
exit $code
""", encoding="utf-8")
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == (0 if case == "packaged" else 1), result.stdout + result.stderr
    pid = re.search(r"^HELPER_PID=(\d+)$", result.stdout, re.MULTILINE)
    assert pid is not None, result.stdout + result.stderr
    assert "HELPER_SETTLED" in result.stdout, result.stdout + result.stderr
    assert not executed.exists(), "installed path hook executed during static admission"
    assert all(path.read_bytes() == contents for path, contents in before.items())
    if case == "packaged":
        assert "COMPLETED" in result.stdout
        marker = json.loads((candidate / ".install-complete.json").read_text(encoding="utf-8"))
        assert marker["payload_hash"] == DIGEST
        assert (site / "pywin32.pth").is_file()
    else:
        assert "Unverified executable installed path hook" in result.stderr
        assert "pywin32.pth" in result.stderr
        assert "construction helper exit 1" in result.stderr
        assert "COMPLETED" not in result.stdout
        assert not candidate.exists()
