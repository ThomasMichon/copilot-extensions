"""Export original synthetic origin-probe tests, not installed or guest calls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_reproducer_lab_origins.py"
_spec = importlib.util.spec_from_file_location("promotion_original_reproducer_origin_probe", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)
paths = _original.paths

_SELECTED = (
    "test_observation_attributes_main_package_only",
    "test_wrong_interpreter_refuses",
    "test_wrong_installed_source_refuses",
    "test_preimported_main_package_refuses",
    "test_explicit_noneditable_metadata_is_supported",
    "test_file_growth_is_read_with_a_finite_limit",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
