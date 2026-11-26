"""Windows venv construction retries an untrusted minor link with an exact uv patch."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_managed_venv_construction import _environment, _powershell, _quote, _uv
from test_native_helper_python_binding import _materialized_engine


@pytest.mark.parametrize("host", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("installed", [True, False])
def test_powershell_constructor_retries_untrusted_minor_link_with_exact_patch(
    tmp_path: Path, host: str, installed: bool,
) -> None:
    engine = _materialized_engine(tmp_path)
    uv, trace, slot = _uv(tmp_path)

    def entry(version: str, implementation: str = "cpython", variant: str = "default") -> dict[str, object]:
        major, minor, patch = (int(part) for part in version.split("."))
        return {"version": version, "implementation": implementation, "variant": variant,
                "version_parts": {"major": major, "minor": minor, "patch": patch}, "path": None}

    listing = [entry("3.11.99"), entry("3.11.18"), entry("3.11.7"), entry("3.11.20", "pypy"),
               entry("3.11.19", variant="freethreaded"), entry("3.12.30")]
    installed_patches = "3.11.7,3.11.18,3.11.19,3.11.20,3.12.30" if installed else "3.12.30"
    result = _powershell(host, tmp_path, f"""
. {_quote(engine / 'installer-engine.ps1')}
$env:OS='Windows_NT'
function Get-InstallerManagedSignedBasePython {{ return $null }}
function Write-Warn {{ param($Message) }}
$accepted=New-SignedVenv -VenvDir {_quote(slot)} -VenvPython {_quote(slot / 'Scripts/python.exe')} `
    -PythonVersion '3.11' -UvCommand {_quote(uv)} -WithoutPip
if (-not $accepted) {{ exit 1 }}
""", {**_environment(trace, slot, "untrusted"), "FIXTURE_UV_LIST": json.dumps(listing),
      "FIXTURE_INSTALLED_PATCHES": installed_patches})
    calls = [json.loads(line) for line in trace.read_text().splitlines()]
    requested = [call[call.index("--python") + 1] for call in calls if call[0] == "venv"]
    assert all("--managed-python" in call for call in calls if call[0] == "venv")
    assert not any(call[:2] == ["python", "dir"] for call in calls)
    assert ["python", "list", "3.11", "--managed-python", "--all-versions", "--output-format", "json"] in calls
    if installed:
        assert result.returncode == 0, result.stdout + result.stderr
        assert requested == ["3.11", "3.11.18"]
    else:
        assert result.returncode == 1, result.stdout + result.stderr
        assert requested == ["3.11"]
