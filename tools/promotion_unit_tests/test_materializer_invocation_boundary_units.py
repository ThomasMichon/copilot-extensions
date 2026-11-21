"""Export original fixture-only invocation boundary controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_materialize_main.py"
_spec = importlib.util.spec_from_file_location("promotion_original_materializer_invocation_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_invocation_promotion_refuses_foreign_manifest_before_writes",
    "test_invocation_promotion_refuses_escaping_output",
    "test_materialize_calls_invocation_owner",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original invocation boundary control: {_name}"
    globals()[_name] = getattr(_original, _name)
