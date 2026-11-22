"""Export original pure CI-admission cases without Git or workflow inspection."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_ci_job_admission.py"
_spec = importlib.util.spec_from_file_location("promotion_original_ci_job_admission", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_dependency_and_unknown_paths",
    "test_chat_core_selects_only_its_own_standalone_family",
    "test_non_pr_events_keep_full_coverage",
    "test_ambiguous_paths_fail_instead_of_emitting_empty_success",
    "test_widening_never_short_circuits_validation",
    "test_union_and_verified_empty_diff",
    "test_incomplete_commit_ids_are_not_accepted",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
