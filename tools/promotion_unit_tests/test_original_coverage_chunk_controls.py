"""Export original synthetic chunk controls and the exact CLI-summary class."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_coverage_guided_selection.py"
_spec = importlib.util.spec_from_file_location("promotion_original_coverage_chunk_controls", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

test_chunk_refuses_incomplete_evidence_and_keeps_diagnostics = (
    _original.test_chunk_refuses_incomplete_evidence_and_keeps_diagnostics
)
test_collector_keeps_all_paths_and_isolates_parallel_state = (
    _original.test_collector_keeps_all_paths_and_isolates_parallel_state
)
test_invalid_worker_count_refuses_before_launch = (
    _original.test_invalid_worker_count_refuses_before_launch
)
test_workers_share_one_unchanged_containment_budget = (
    _original.test_workers_share_one_unchanged_containment_budget
)
TestCoverageGuidedSelectionCli = _original.TestCoverageGuidedSelectionCli
