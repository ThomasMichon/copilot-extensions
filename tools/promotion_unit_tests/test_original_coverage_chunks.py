"""Export the complete original chunk-planning and result-merging classes."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_coverage_guided_selection.py"
_spec = importlib.util.spec_from_file_location("promotion_original_coverage_chunks", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

TestPlanChunks = _original.TestPlanChunks
TestMergeChunkResults = _original.TestMergeChunkResults
