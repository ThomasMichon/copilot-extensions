"""Export original bootstrap declaration fixtures without the process smoke."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_bootstrap_python_materialization.py"
_spec = importlib.util.spec_from_file_location("promotion_original_bootstrap_declaration_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_bootstrap_declaration_materializes_without_changing_canonical_source",
    "test_withdrawn_bootstrap_request_refuses_candidate_before_writes",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original bootstrap declaration control: {_name}"
    globals()[_name] = getattr(_original, _name)
