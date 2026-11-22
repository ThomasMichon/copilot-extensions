"""Export original pure candidate-retry controls without replacing witnesses."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_promotion_candidate_retry.py"
_spec = importlib.util.spec_from_file_location("promotion_original_candidate_retry", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

no_ambient_git_identity = _original.no_ambient_git_identity
_SELECTED = (
    "test_immutable_clock_refuses_invalid_or_noncanonical_inputs",
    "test_fixed_clock_and_git_identity_do_not_mutate_parent_environment",
    "test_retry_compares_the_full_commit_identity_without_fetching_or_normalizing",
    "test_cli_emits_the_trusted_commit_directly_not_via_human_log_parsing",
    "test_cli_noop_never_emits_a_candidate_identity",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original candidate-retry control: {_name}"
    globals()[_name] = getattr(_original, _name)
