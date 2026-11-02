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


def test_runtime_admission_primitives_do_not_import_installer_or_publisher(tmp_path):
    root = Path(__file__).resolve().parent
    wheel = _record()["wheels"][0]
    archive = tmp_path / wheel["filename"]
    archive.write_bytes(_encoded(wheel))
    script = """
import importlib.abc, os, pathlib, sys
sys.path.insert(0, sys.argv[1])
blocked = {
    "promotion_python_artifacts", "publish_python_artifacts",
    "worktree_manager.self_install",
}
class DenyUnrelatedOwners(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in blocked:
            raise AssertionError("runtime admission imported unrelated owner: " + fullname)
sys.meta_path.insert(0, DenyUnrelatedOwners())
os.environ["WORKTREE_MANAGER_ROOT"] = sys.argv[3]
import runtime_source_authentication as authentication
import runtime_profile_wheels as wheels
from worktree_manager import runtime_source_config, source_config
assert authentication.default_root is source_config.default_root
assert source_config.config_path() == pathlib.Path(sys.argv[3]) / "config.toml"
assert runtime_source_config.configured_source().channel == "marketplace"
archive = pathlib.Path(sys.argv[2])
with archive.open("rb") as stream:
    metadata, size = wheels.inspect_wheel(stream, {"filename": archive.name})
assert metadata["Version"] == "1.0" and size > 0
assert not blocked.intersection(sys.modules)
print("narrow-runtime-owners-ok")
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(root), str(archive),
         str(tmp_path / "manager")],
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "narrow-runtime-owners-ok"
