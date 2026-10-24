"""Clean caller-interpreter contracts; native children stay outside unit-only CI."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from promotion_unit_tests.test_runtime_profile_wheels import _encoded, _record


def test_builder_parser_and_first_party_inspector_need_no_caller_packaging(tmp_path):
    root = Path(__file__).resolve().parent
    wheel = _record()["wheels"][0]
    archive = tmp_path / wheel["filename"]
    archive.write_bytes(_encoded(wheel, wheel_header=(
        "Wheel-Version: 1.0\nGenerator: locked\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    )))
    script = """
import importlib.abc, pathlib, sys
sys.path.insert(0, sys.argv[1])
class DenyPackaging(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "packaging" or fullname.startswith("packaging."):
            raise ModuleNotFoundError("caller packaging is deliberately unavailable")
sys.meta_path.insert(0, DenyPackaging())
from build_python_artifacts import parse_wheel_filename
from promotion_python_artifacts import validate_wheel
wheel = pathlib.Path(sys.argv[2])
info = parse_wheel_filename(wheel)
if info["version"] != "1.0":
    raise RuntimeError("builder token identity changed")
with wheel.open("rb") as stream:
    validate_wheel(stream, {"filename": wheel.name, "generator": "locked"})
if any(name == "packaging" or name.startswith("packaging.") for name in sys.modules):
    raise RuntimeError("first-party caller acquired an undeclared dependency")
print("clean-caller-contract-ok")
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", script, str(root), str(archive)],
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "clean-caller-contract-ok"
