"""Export original feed units, retaining Git-storage and checkout witnesses."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_check_feed_neutrality.py"
_spec = importlib.util.spec_from_file_location("promotion_original_feed_neutrality_guard", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

for _name, _function in vars(_original).items():
    if _name.startswith("test_") and _name not in {
        "test_candidate_scan_preserves_patterns_and_diagnostics",
        "test_verify_scans_real_repo_tree_with_zero_findings",
    }:
        assert _name not in globals(), f"duplicate original feed-neutrality test: {_name}"
        globals()[_name] = _function
