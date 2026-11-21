"""Export original tracking-write units, retaining the native checkout smoke."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_check_no_sibling_tracking_writes.py"
_spec = importlib.util.spec_from_file_location("promotion_original_tracking_write_guard", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

for _name, _function in vars(_original).items():
    if _name.startswith("test_") and _name != "test_real_repo_checkout_is_clean":
        assert _name not in globals(), f"duplicate original tracking-write test: {_name}"
        globals()[_name] = _function
