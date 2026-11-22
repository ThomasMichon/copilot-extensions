"""Export original optional pytest temporary-link controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_pytest_windows_paths.py"
_spec = importlib.util.spec_from_file_location("promotion_original_pytest_temp_link_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_windows_suppresses_only_current_link_helper_and_restores_it",
    "test_other_platforms_keep_pytest_current_links",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
