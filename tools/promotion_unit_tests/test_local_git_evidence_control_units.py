"""Export the original mocked local-only Git evidence controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_check_agent_bridge_contracts.py"
_spec = importlib.util.spec_from_file_location("promotion_original_local_git_controls", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_provenance_batch_refuses_delimiters_before_any_object_read",
    "test_provenance_commit_validation_is_local_only",
    "test_local_git_probe_is_bounded_headless_and_fail_closed",
    "test_repeated_local_evidence_is_read_once_per_validation",
    "test_local_only_guard_refuses_unsupported_git_before_object_reads",
)
for _name in _SELECTED:
    globals()[_name] = getattr(_original, _name)
