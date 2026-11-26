"""Export the whole original data-only outer-frame refusal contract."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
_source = str(_root / "libs" / "agent-procutil" / "src")
if _source not in sys.path:
    sys.path.insert(0, _source)
_path = _root / "libs" / "agent-procutil" / "tests" / "test_diagnostics.py"
_spec = importlib.util.spec_from_file_location("promotion_original_procutil_diagnostic_frames", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)
pytestmark = _original.pytestmark

_SELECTED = (
    "test_untrusted_outer_frames_do_not_authorize_nested_diagnostics",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
