"""Original pre-uv checker cases using fixture-only manifests and fragments."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
if str(_root / "tools") not in sys.path:
    sys.path.insert(0, str(_root / "tools"))
_path = _root / "tools" / "test_check_pre_uv_tier.py"
_spec = importlib.util.spec_from_file_location("promotion_original_pre_uv_fixtures", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_fragments_loaded_by_a_listed_entrypoint_are_checked",
    "test_missing_fragment_fails_the_check",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
