"""Export original collection-error contracts for exact unit-node selection."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_coverage_guided_selection.py"
_spec = importlib.util.spec_from_file_location("promotion_original_coverage_errors", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

test_unreaped_parallel_collection_retains_automatic_sandbox = (
    _original.test_unreaped_parallel_collection_retains_automatic_sandbox
)
test_baseline_error_preserves_collection_output_despite_coverage_warning = (
    _original.test_baseline_error_preserves_collection_output_despite_coverage_warning
)
TestBaselineCollectionErrorContract = _original.TestBaselineCollectionErrorContract
