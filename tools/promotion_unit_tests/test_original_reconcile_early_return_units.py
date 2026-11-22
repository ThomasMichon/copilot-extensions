"""Export original contained and unscoped reconciliation early returns."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_installer_instance_reconcile.py"
_spec = importlib.util.spec_from_file_location("promotion_original_reconcile_early_return_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_implicit_contained_reconciliation_never_invokes_a_shell",
    "test_missing_runtime_scope_never_invokes_a_shell",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
