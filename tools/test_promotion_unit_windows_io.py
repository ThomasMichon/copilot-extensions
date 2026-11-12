"""Fresh Windows framework bootstrap retains actual unit-side DLL-load refusal."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows pytest I/O adapter")


def test_fresh_windows_adapter_bootstrap_still_blocks_a_unit_dll_load(tmp_path):
    code = """
import ctypes, json, runpy, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root / "tools"))
runner = runpy.run_path(str(root / "tools/run-promotion-unit-tests.py"))
assert "colorama" not in sys.modules
runner["prepare_framework_io"]()
assert "colorama" in sys.modules
from promotion_unit_contract import UnitEvidenceError
from promotion_unit_effects import UnitRun
run = UnitRun(None, root, {}, Path(sys.argv[2]))
sys.addaudithook(run.audit)
run.active = True
try:
    try:
        ctypes.WinDLL("kernel32", use_last_error=True)
    except UnitEvidenceError:
        pass
    else:
        raise AssertionError("unit-side DLL load was not refused")
finally:
    run.active = False
assert run.effects == ["ctypes.dlopen"]
print(json.dumps({"framework_ready": True, "unit_native_load_refused": True}))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(ROOT), str(tmp_path)],
        capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(result.stdout) == {
        "framework_ready": True, "unit_native_load_refused": True,
    }


def test_native_unit_runner_suppresses_only_pytest_convenience_links(tmp_path):
    code = """
import os, runpy, sys
from pathlib import Path
import _pytest.pathlib, _pytest.tmpdir, pytest
root, temporary = map(Path, sys.argv[1:])
sys.path.insert(0, str(root / "tools"))
runner = runpy.run_path(str(root / "tools/run-promotion-unit-tests.py"))
test = temporary / "test_temporary.py"
test.write_text("def test_temporary(tmp_path):\\n    assert tmp_path.is_dir()\\n")
original = _pytest.pathlib._force_symlink, getattr(_pytest.tmpdir, "_force_symlink", None), os.symlink
base = temporary / "pytest"
assert pytest.main([
    "-q", "-c", os.devnull, "--noconftest", "-p", "no:cacheprovider",
    "--basetemp", str(base), str(test),
], plugins=[runner["FrameworkTemporaryPaths"]()]) == 0
assert not list(base.glob("*current"))
assert original == (
    _pytest.pathlib._force_symlink, getattr(_pytest.tmpdir, "_force_symlink", None), os.symlink,
)
"""
    environment = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    environment.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(ROOT), str(tmp_path)],
        capture_output=True, text=True, timeout=15, env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr + result.stdout
