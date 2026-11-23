"""Export original pure state-mount validation, not guest operations."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_reproducer_lab_operation.py"
_spec = importlib.util.spec_from_file_location("promotion_original_reproducer_state_mount_refusal", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_unavailable_or_volatile_state_mount_refuses",
    "test_ancestor_overmount_refuses_even_on_same_device",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
