"""Native admitted-slot reuse avoids construction without bypassing health."""

from __future__ import annotations

import json
import os
import shutil
import sys

import pytest

from .test_installer_engine_adoption import (
    PLUGIN,
    PWSH,
    _function,
    _prelude,
    _run,
    launch_runtime,  # noqa: F401 - shared real-venv fixture
)

pytestmark = pytest.mark.timeout(60)


@pytest.mark.parametrize("ext", [
    pytest.param("sh", marks=pytest.mark.skipif(os.name == "nt", reason="native POSIX")),
    pytest.param("ps1", marks=pytest.mark.skipif(PWSH is None, reason="pwsh unavailable")),
])
@pytest.mark.parametrize("state", [
    "healthy", "unhealthy", "different-payload", "malformed-marker",
])
def test_completed_runtime_reuse_keeps_health_and_admission(tmp_path, request, ext, state):
    slot, python, package, env = request.getfixturevalue("launch_runtime")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import argparse\nargparse.ArgumentParser().parse_args()\n"
        if state != "unhealthy" else "raise SystemExit(23)\n",
        encoding="utf-8",
    )
    payload_hash = "a" * 64
    marker = slot / ".install-complete.json"
    marker.write_text(json.dumps({
        "version": "1.2.3", "completed_at": "2026-10-01T00:00:00Z",
        "pid": 0, "payload_hash": payload_hash,
    }), encoding="utf-8")
    if state == "malformed-marker":
        marker.write_text("{", encoding="utf-8")
    root = slot.parents[1]
    selector = root / "current-version"
    selector.write_text("1.2.3", encoding="utf-8")
    protected = [marker, selector, python, slot / "pyvenv.cfg", package / "__main__.py"]
    before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in protected]
    requested_hash = "b" * 64 if state == "different-payload" else payload_hash

    if ext == "sh":
        script = (
            _prelude(ext)
            + "".join(_function(ext, name) for name in (
                "_codespaces_runtime_healthy", "_versioned_slot_clean",
                "deploy_venv", "deploy_package",
            ))
            + f"""
SCRIPT_DIR='{PLUGIN / "scripts"}'
INSTALL_DIR='{root}'
SRC_VERSION=1.2.3
VERSIONED_RUNTIME=1
LINK_DIR="$INSTALL_DIR/.venv"
VENV_DIR='{slot}'
VENV_PYTHON='{python}'
_bootstrap_python() {{ printf '%s\\n' '{sys._base_executable}'; }}
_payload_hash() {{ printf '%s' '{requested_hash}'; }}
_assert_uv() {{ echo 'UV_ACQUISITION' >&2; return 1; }}
invoke_uv_venv_resilient() {{ echo 'UNEXPECTED_VENV_CONSTRUCTION' >&2; return 1; }}
_install_package_into() {{ echo 'UNEXPECTED_PACKAGE_INSTALL' >&2; return 1; }}
_stamp_build_info() {{ echo 'UNEXPECTED_BUILD_STAMP' >&2; return 1; }}
deploy_venv
deploy_package
[[ "$_RUNTIME_CONSTRUCTION_REUSE" == 1 ]]
_runtime_construction_finish
"""
        )
    else:
        for name in ("versioned_runtime.py", "versioned_runtime_leases.py",
                     "versioned_runtime_construction.py"):
            shutil.copyfile(PLUGIN / "scripts" / name, tmp_path / name)
        script = (
            _prelude(ext)
            + "".join(_function(ext, name) for name in (
                "Test-CodespacesRuntime", "Invoke-VersionedSlotClean",
                "Deploy-Venv", "Deploy-Package",
            ))
            + f"""
$InstallDir='{root}'
$SrcVersion='1.2.3'
$VersionedRuntime=$true
$VenvDir='{slot}'
$VenvPython='{python}'
$LinkDir=Join-Path $InstallDir '.venv'
$env:OS='Test'
function Get-BootstrapPython {{ '{sys._base_executable}' }}
function Get-PayloadHash {{ '{requested_hash}' }}
function Assert-Uv {{ [Console]::Error.WriteLine('UV_ACQUISITION') }}
function Invoke-UvVenvResilient {{ throw 'UNEXPECTED_VENV_CONSTRUCTION' }}
function Install-PackageInto {{ throw 'UNEXPECTED_PACKAGE_INSTALL' }}
function Stamp-BuildInfo {{ throw 'UNEXPECTED_BUILD_STAMP' }}
try {{
    if (-not (Deploy-Venv)) {{ exit 1 }}
    if (-not (Deploy-Package)) {{ exit 1 }}
    if (-not $script:RuntimeConstructionReuse) {{ throw 'reuse was not admitted' }}
}} finally {{
    if (Get-Command Stop-RuntimeConstruction -ErrorAction SilentlyContinue) {{
        Stop-RuntimeConstruction
    }}
}}
"""
        )
    result = _run(ext, script, tmp_path, env)
    output = result.stdout + result.stderr
    assert (result.returncode == 0) is (state == "healthy"), output
    if state == "unhealthy":
        assert "module launch health gate" in output
    elif state == "different-payload":
        assert "content-conflict" in output
    elif state == "malformed-marker":
        assert "health-repair-required" in output
    assert "UNEXPECTED_VENV_CONSTRUCTION" not in output
    assert "UNEXPECTED_PACKAGE_INSTALL" not in output
    assert "UNEXPECTED_BUILD_STAMP" not in output
    assert output.count("UV_ACQUISITION") == (
        1 if ext == "ps1" and state != "unhealthy" else 0
    ), output
    assert [(path.read_bytes(), path.stat().st_mtime_ns) for path in protected] == before
