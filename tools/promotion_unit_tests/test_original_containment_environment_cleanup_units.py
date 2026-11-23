"""Original fixture-owned cleanup and module-path environment controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
_path = _root / "tools" / "test_plugin_test_containment.py"
_spec = importlib.util.spec_from_file_location("promotion_original_containment_environment_cleanup", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_sandbox_cleanup_supports_both_stdlib_signatures",
    "test_isolated_environment_overrides_native_module_path_identity",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
