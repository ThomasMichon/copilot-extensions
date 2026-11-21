"""Export original synthetic ancestor-metadata controls and their fixtures."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_materialize_installer_engine.py"
_spec = importlib.util.spec_from_file_location("promotion_original_materializer_ancestor_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

tmp_path = _original.tmp_path
ancestor = _original.ancestor
_SELECTED = (
    "test_ordinary_descendant_needs_no_path_resolution",
    "test_ordinary_descendant_keeps_live_symlink_refusal",
    "test_reparse_metadata_retains_resolved_ancestor_walk",
    "test_relative_path_retains_resolved_ancestor_walk",
    "test_nonordinary_path_retains_resolved_ancestor_walk",
    "test_ancestor_metadata_is_fresh_on_every_call",
    "test_metadata_permission_failure_is_not_accepted",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original ancestor control: {_name}"
    globals()[_name] = getattr(_original, _name)
