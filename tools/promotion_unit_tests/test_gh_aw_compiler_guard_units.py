"""Export the original compiler-version tests and temporary-workflows fixture."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_check_gh_aw_compiler_version.py"
_spec = importlib.util.spec_from_file_location("promotion_original_gh_aw_compiler_guard", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

workflows_dir = _original.workflows_dir
for _name, _function in vars(_original).items():
    if _name.startswith("test_"):
        assert _name not in globals(), f"duplicate original compiler-version test: {_name}"
        globals()[_name] = _function
