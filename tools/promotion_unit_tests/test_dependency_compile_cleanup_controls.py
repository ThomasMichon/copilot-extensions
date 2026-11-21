"""Export original compile and cleanup controls without changing witnesses."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_SELECTED = {
    "test_installer_dependency_compile.py": (
        "test_compile_projection_is_admitted_without_install_flags",
        "test_compile_projection_preserves_installer_refusals",
        "test_unsupported_compile_shape_refuses_before_probe",
        "test_effective_compile_output_policy_cannot_be_supplied_by_ambient_config",
        "test_compile_include_paths_are_canonicalized_before_text_remapping",
        "test_compile_root_anchor_include_target_is_never_lexically_remapped_again",
    ),
    "test_installer_dependency_cleanup.py": (
        "test_active_failure_survives_exit_stack_cleanup",
        "test_cleanup_only_failure_is_distinct_and_chained",
        "test_nonzero_child_result_survives_cleanup",
        "test_check_true_preserves_exact_child_exception",
        "test_successful_child_cannot_hide_cleanup_failure",
        "test_cli_distinguishes_cleanup_from_dependency_policy",
        "test_normal_cleanup_preserves_success",
    ),
}
_ORIGINALS = {}
for _filename, _names in _SELECTED.items():
    _path = Path(__file__).resolve().parents[1] / _filename
    _spec = importlib.util.spec_from_file_location("promotion_original_controls_" + _path.stem, _path)
    assert _spec is not None and _spec.loader is not None
    _original = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = _original
    _spec.loader.exec_module(_original)
    _ORIGINALS[_filename] = _original
    for _name in _names:
        assert _name not in globals(), f"duplicate original compile/cleanup control: {_name}"
        globals()[_name] = getattr(_original, _name)

cleanup_failure = _ORIGINALS["test_installer_dependency_cleanup.py"].cleanup_failure
