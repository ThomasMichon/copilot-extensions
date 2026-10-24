"""Read-only Windows registry consumer checks; never add or delete a value."""

from __future__ import annotations

import re

import pytest

from agent_machines import resources as R
from agent_machines.manifest import load_package

from ._helpers import base_package, write_package


@pytest.mark.parametrize(
    ("name", "value_type", "reg_type"),
    [("InstallDate", "DWord", "REG_DWORD"), ("InstallTime", "QWord", "REG_QWORD")],
)
def test_registry_native_numeric_convergence(tmp_path, name, value_type, reg_type):
    winreg = pytest.importorskip("winreg", reason="Windows registry required")
    if R.shutil.which("reg") is None:
        pytest.skip("reg.exe required")
    path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            value, actual_type = winreg.QueryValueEx(key, name)
    except FileNotFoundError:
        pytest.skip(f"Windows {name} value unavailable")
    assert actual_type == getattr(winreg, reg_type)
    calls = []

    def query_only(argv):
        assert argv[:2] == ["reg", "query"], "native check must never mutate the registry"
        calls.append(argv)
        out = R.default_runner(argv)
        assert out.returncode == 0
        observed, observed_type = R._parse_reg_query(out.stdout, name)
        assert observed_type == reg_type
        assert re.fullmatch(r"0[xX][0-9a-fA-F]+", observed)
        return out

    manifest = write_package(
        tmp_path,
        "pkg.yaml",
        base_package(
            name="acme/registry",
            gate=["box-1"],
            resources=[
                {
                    "type": "registry", "path": f"HKLM\\{path}", "name": name,
                    "value_type": value_type, "value": value,
                }
            ],
        ),
    )
    package = load_package(manifest, source_repo="acme")
    ctx = R.ResourceContext(tmp_path, {"acme": tmp_path}, "windows", query_only)
    for dry_run in (True, False):
        result, = R.apply_resources([package], "box-1", "windows", ctx, dry_run=dry_run)
        assert result.ok and not result.changed and result.action == "none"
        assert result.commands == []
    assert len(calls) == 2
