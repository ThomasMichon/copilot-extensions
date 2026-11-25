"""Original lifecycle outcomes and user-bus selection with existing doubles."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
_path = _root / "tools" / "test_plugin_test_containment.py"
_spec = importlib.util.spec_from_file_location("promotion_original_containment_lifecycle_doubles", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_native_application_retains_runner_lease_limits_and_cleanup",
    "test_native_application_selects_only_the_existing_owned_user_bus",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
