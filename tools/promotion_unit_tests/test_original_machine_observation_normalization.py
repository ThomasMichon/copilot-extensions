"""Export whole original registry-only observation normalization contracts."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
for _library in ("machine-transport", "remote-login-shell"):
    _source = str(_root / "libs" / _library / "src")
    if _source not in sys.path:
        sys.path.insert(0, _source)
_path = _root / "libs" / "machine-transport" / "tests" / "test_local_execution.py"
_spec = importlib.util.spec_from_file_location("promotion_original_machine_observation_normalization", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)
pytestmark = _original.pytestmark

_SELECTED = (
    "test_literal_observation_is_an_opt_in_normalization_context",
    "test_normalization_rejects_invalid_observation_context",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
