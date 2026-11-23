"""Export original pure offline-declaration and static-boundary tests."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_reproducer_lab_offline.py"
_spec = importlib.util.spec_from_file_location("promotion_original_reproducer_offline_boundary", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_existing_v1_defaults_remain_internal_and_none_is_explicit",
    "test_unknown_network_modes_never_default_to_a_supported_mode",
    "test_static_none_selection_preserves_all_unresolved_gates",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
