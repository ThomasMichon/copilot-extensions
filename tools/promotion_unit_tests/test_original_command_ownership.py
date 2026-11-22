"""Export original source-derived command ownership witnesses without execution."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_installer_engine_commands.py"
_spec = importlib.util.spec_from_file_location("promotion_original_command_ownership", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_OUTSIDE = ("test_name_allowlist_order_and_no_independent_runtime_imports",)
_SELECTED = tuple(
    _name for _name in vars(_original)
    if _name.startswith("test_") and _name not in _OUTSIDE
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original command ownership control: {_name}"
    globals()[_name] = getattr(_original, _name)
