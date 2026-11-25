"""Original cleanup-retry cases using fixture paths and existing clock doubles."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
_path = _root / "tools" / "test_plugin_test_containment.py"
_spec = importlib.util.spec_from_file_location("promotion_original_containment_cleanup_retry", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_sandbox_cleanup_retries_only_transient_windows_errors",
    "test_sandbox_cleanup_retry_expires_without_hiding_failure",
    "test_sandbox_cleanup_never_retries_retained_or_expired_ownership",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
