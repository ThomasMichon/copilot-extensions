"""Original core-profile refusals that precede native wheel retention."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
if str(_root / "tools") not in sys.path:
    sys.path.insert(0, str(_root / "tools"))
_path = _root / "tools" / "test_core_runtime_profile.py"
_spec = importlib.util.spec_from_file_location("promotion_original_core_profile_refusals", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

inputs = _original.inputs
_SELECTED = (
    "test_only_the_registered_core_manifest_schema_is_accepted",
    "test_expired_deadline_and_explicit_limits_refuse",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
