"""Expose Dispatch focus-reader controls without coordinator dependencies."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import ModuleType

_tests = Path(__file__).resolve().parents[3] / "plugins/agent-dispatch/tests"
_package = ModuleType("_dispatch_focus_source")
_package.__path__ = [str(_tests)]
sys.modules[_package.__name__] = _package
_source = importlib.import_module(_package.__name__ + ".test_focus_guidance")
_writer = importlib.import_module(_package.__name__ + ".test_write_session_guidance")


def test_managed_focus_composes_through_existing_session_writer(tmp_path: Path) -> None:
    _writer.test_wrapper_end_to_end_writes_bounded_session_file(tmp_path, ".sh")
    target = (
        tmp_path / "home/.copilot/session-state/session-1/instructions/agent-dispatch"
        / "session-guidance.instructions.md"
    )
    assert _source.KERNEL in target.read_text(encoding="utf-8")

test_focus_reader_uses_one_prepared_bootstrap_without_ambient_python = (
    _source.test_focus_reader_uses_one_prepared_bootstrap_without_ambient_python
)
test_enabled_opt_in_emits_exact_bounded_owned_kernel = (
    _source.test_enabled_opt_in_emits_exact_bounded_owned_kernel
)
test_aggregate_mode_is_compact_and_keeps_focus_invariants = (
    _source.test_aggregate_mode_is_compact_and_keeps_focus_invariants
)
test_owner_marker_is_derived_from_adjacent_manifest = (
    _source.test_owner_marker_is_derived_from_adjacent_manifest
)
test_absent_or_disabled_or_non_boolean_opt_in_emits_empty = (
    _source.test_absent_or_disabled_or_non_boolean_opt_in_emits_empty
)
test_missing_malformed_or_invalid_manifest_fails_open = (
    _source.test_missing_malformed_or_invalid_manifest_fails_open
)
test_contaminated_git_environment_is_isolated_and_restored = (
    _source.test_contaminated_git_environment_is_isolated_and_restored
)
test_config_parent_symlink_or_reparse_fails_open = (
    _source.test_config_parent_symlink_or_reparse_fails_open
)
test_config_file_symlink_or_reparse_fails_open = (
    _source.test_config_file_symlink_or_reparse_fails_open
)
test_payload_raw_byte_limit_parity = _source.test_payload_raw_byte_limit_parity
test_payload_requires_strict_utf8_and_rejects_nul = (
    _source.test_payload_requires_strict_utf8_and_rejects_nul
)
test_non_exact_config_shape_fails_open = _source.test_non_exact_config_shape_fails_open
test_payload_cwd_is_authoritative_when_process_cwd_differs = (
    _source.test_payload_cwd_is_authoritative_when_process_cwd_differs
)
