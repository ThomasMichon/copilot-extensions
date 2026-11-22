"""Export original stubbed lookup outcomes without repository or GitHub access."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_module_health_watchdog.py"
_spec = importlib.util.spec_from_file_location("promotion_original_module_health_lookups", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_existing_issue_number_raises_lookup_failed_on_a_nonzero_exit",
    "test_existing_issue_number_returns_none_on_a_clean_no_match",
    "test_existing_issue_number_returns_the_number_on_a_match",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
