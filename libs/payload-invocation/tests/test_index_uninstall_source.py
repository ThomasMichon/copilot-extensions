"""Export external-helper admission without installing Index's application."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
from typing import Any, cast

import pytest

_source = (
    Path(__file__).resolve().parents[3]
    / "plugins/agent-index/tests/test_managed_uninstall_bootstrap.py"
)
_spec = importlib.util.spec_from_file_location("_index_uninstall_source", _source)
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
sys.path.insert(0, str(_source.parent))
try:
    _spec.loader.exec_module(_module)
finally:
    sys.path.pop(0)
_original = cast(Any, _module)
prepared_helper = _original.prepared_helper
test_uninstall_external_helper_requires_prepared_managed_python = (
    _original.test_uninstall_external_helper_requires_prepared_managed_python
)
_removal_spec = importlib.util.spec_from_file_location(
    "_index_native_uninstall_source", _source.with_name("test_native_runtime_uninstall.py"),
)
assert _removal_spec and _removal_spec.loader
_removal_module = importlib.util.module_from_spec(_removal_spec)
sys.modules[_removal_spec.name] = _removal_module
_removal_spec.loader.exec_module(_removal_module)
_removal = cast(Any, _removal_module)
test_real_native_removal_includes_nested_engine_and_preserves_transaction = (
    _removal.test_real_native_removal_includes_nested_engine_and_preserves_transaction
)
test_real_native_preview_and_absent_root_do_not_mutate = (
    _removal.test_real_native_preview_and_absent_root_do_not_mutate
)
test_real_native_preview_defaults_root_before_helper_admission = (
    _removal.test_real_native_preview_defaults_root_before_helper_admission
)
_retained_source = (
    _source.parents[3]
    / "plugins/agent-codespaces/tests/native_uninstall/test_windows_uninstall_candidates.py"
)
_retained_spec = importlib.util.spec_from_file_location("_index_retained_uninstall", _retained_source)
assert _retained_spec and _retained_spec.loader
_retained_module = importlib.util.module_from_spec(_retained_spec)
sys.modules[_retained_spec.name] = _retained_module
_retained_spec.loader.exec_module(_retained_module)
_retained = cast(Any, _retained_module)
_native_windows = pytest.mark.skipif(os.name != "nt", reason="Native Windows uninstall controls")
test_retained_windows_candidates = _native_windows(
    _retained.test_real_candidate_failures_do_not_abort_or_accept_unsupported_python,
)
test_retained_windows_metadata = _native_windows(
    _retained.test_supported_selection_runs_real_finite_metadata_cleanup_and_preserves_state,
)
test_retained_windows_invoker = _native_windows(
    _retained.test_actual_finite_admission_invoker_recovers_from_process_start_failure,
)
test_retained_windows_base_prefix = _native_windows(
    _retained.test_real_external_venv_with_internal_base_prefix_is_not_selected,
)
