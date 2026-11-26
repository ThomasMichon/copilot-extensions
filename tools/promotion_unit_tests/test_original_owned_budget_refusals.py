"""Export the whole original invalid-budget refusal contract."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
_source = str(_root / "libs" / "agent-procutil" / "src")
if _source not in sys.path:
    sys.path.insert(0, _source)
_path = _root / "libs" / "agent-procutil" / "tests" / "test_owned_budget.py"
_spec = importlib.util.spec_from_file_location("promotion_original_owned_budget_refusals", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = ("test_invalid_budget_refuses_before_launch",)
globals().update({name: getattr(_original, name) for name in _SELECTED})
