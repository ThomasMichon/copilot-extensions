"""Export original temporary-tree vendored-library synchronization units."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_check_vendored_libs_sync.py"
_spec = importlib.util.spec_from_file_location("promotion_original_vendored_libs_sync_guard", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)
fake_repo = _original.fake_repo

for _name, _function in vars(_original).items():
    if _name.startswith("test_") and _name != "test_verify_fails_closed_on_symlinked_consumer_pyproject":
        assert _name not in globals(), f"duplicate original vendored-library test: {_name}"
        globals()[_name] = _function
