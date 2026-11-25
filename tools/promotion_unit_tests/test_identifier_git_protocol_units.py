"""Export the original in-process identifier Git blob protocol controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_check_no_internal_identifiers.py"
_spec = importlib.util.spec_from_file_location("promotion_original_identifier_git_protocol", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_git_ref_loader_batches_and_deduplicates_blobs",
    "test_git_ref_loader_bounds_batch_bytes",
    "test_git_ref_loader_refuses_incomplete_or_malformed_batches",
    "test_git_ref_loader_propagates_git_failure",
)
for _name in _SELECTED:
    globals()[_name] = getattr(_original, _name)
