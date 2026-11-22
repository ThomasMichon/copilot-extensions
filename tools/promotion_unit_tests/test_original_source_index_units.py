"""Export original fixture-only source-owned artifact-index decisions."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_promotion_source_identity.py"
_spec = importlib.util.spec_from_file_location("promotion_original_source_index_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_source_owned_index_accepts_an_equivalent_composition_in_another_container",
    "test_source_owned_index_still_rejects_changed_source_or_composition",
    "test_legacy_index_retains_its_original_parent_binding",
    "test_source_owned_index_refuses_a_second_container_identity",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
