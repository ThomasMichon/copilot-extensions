"""Export original installer parser and temporary-file guards without installation."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_check_install_contract.py"
_spec = importlib.util.spec_from_file_location("promotion_original_installer_guard_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_engine_usage_bypass_comments_do_not_count",
    "test_engine_usage_requires_a_real_function_call",
    "test_captured_uv_call_remains_an_actual_install",
    "test_canonical_policy_invocation_counts_but_documentation_does_not",
    "test_policy_calls_require_provider_and_argument_separator",
    "test_policy_calls_accept_complete_native_and_array_argv",
    "test_engine_usage_accepts_trailing_comment_same_as_shared_parser",
    "test_runtime_scope_accepts_reconciled_runtime_and_payload_only",
    "test_runtime_installer_must_not_declare_none",
    "test_runtime_installer_must_declare_a_scope",
    "test_declared_scope_must_be_valid",
    "test_unreadable_manifest_is_reported",
    "test_exec_redirect_that_swallows_stderr_is_rejected",
    "test_grouped_exec_redirect_is_accepted",
    "test_ignored_completion_marker_failures_are_violations",
    "test_checked_completion_marker_publication_passes",
    "test_attached_completion_must_propagate_failure",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
