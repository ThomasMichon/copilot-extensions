"""Export the whole original pre-discovery marketplace-identity refusal."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
_source = str(_root / "libs" / "repository-policy" / "src")
if _source not in sys.path:
    sys.path.insert(0, _source)
_path = _root / "libs" / "repository-policy" / "tests" / "test_repository_policy.py"
_spec = importlib.util.spec_from_file_location("promotion_original_repository_policy_identity", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = ("test_invalid_marketplace_identity_refuses",)
globals().update({name: getattr(_original, name) for name in _SELECTED})
