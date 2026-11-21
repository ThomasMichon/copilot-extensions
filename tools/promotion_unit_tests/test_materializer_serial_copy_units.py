"""Export the original copy-worker matrix; the packet selects its serial case."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_materialize_main.py"
_spec = importlib.util.spec_from_file_location("promotion_original_materializer_serial_copy_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_plugin_copy_workers_preserve_tree_logs_and_standalone_dependency_order",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original serial-copy control: {_name}"
    globals()[_name] = getattr(_original, _name)
