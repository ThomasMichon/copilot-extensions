"""Export original injected lock-posture controls, not real guest locks."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_reproducer_lab_operation.py"
_spec = importlib.util.spec_from_file_location("promotion_original_reproducer_lock_posture", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_state_lock_posture_accepts_only_expected_plain_file",
    "test_existing_lock_posture_refuses",
    "test_missing_lock_is_only_allowed_before_acquisition",
    "test_source_adapter_reuses_shared_lock_at_fixed_key",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
