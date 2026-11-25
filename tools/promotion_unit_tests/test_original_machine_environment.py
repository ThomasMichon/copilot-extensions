"""Original complete logical-environment tuple table, without OS probing."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
for _library in ("machine-transport", "remote-login-shell"):
    _source = str(_root / "libs" / _library / "src")
    if _source not in sys.path:
        sys.path.insert(0, _source)
_path = _root / "libs" / "machine-transport" / "tests" / "test_machine_environment.py"
_spec = importlib.util.spec_from_file_location("promotion_original_machine_environment", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = ("test_logical_and_tuple_forms",)
globals().update({name: getattr(_original, name) for name in _SELECTED})
