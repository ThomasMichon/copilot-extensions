"""Run original native compatibility fixtures without installing unrelated services."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import ModuleType

import pytest

_root = Path(__file__).resolve().parents[3]
if not (_root / "plugins/agent-codespaces/tests").is_dir():
    pytest.skip("This integration selection requires source consumers", allow_module_level=True)


def _load(plugin: str, module: str) -> ModuleType:
    name = "_bootstrap_compatibility_" + plugin.replace("-", "_")
    if name not in sys.modules:
        package = ModuleType(name)
        package.__path__ = [str(_root / "plugins" / plugin / "tests")]
        sys.modules[name] = package
    return importlib.import_module(name + "." + module)


_codespaces = _load("agent-codespaces", "test_bootstrap_python")
_retained = _load("agent-dispatch", "test_retained_bootstrap")
_logger = _load("agent-logger", "test_install_signed_python_probe")
_yaml = _load("agent-index", "test_bootstrap_yaml")
_previous = sys.modules.get("test_install_engine_adoption")
sys.modules["test_install_engine_adoption"] = _load("agent-index", "test_install_engine_adoption")
try:
    _index = _load("agent-index", "test_runtime_gate")
finally:
    if _previous is None:
        sys.modules.pop("test_install_engine_adoption", None)
    else:
        sys.modules["test_install_engine_adoption"] = _previous

host = _codespaces.host
test_discovery_uses_path_order_without_executing_candidates = (
    _codespaces.test_discovery_uses_path_order_without_executing_candidates
)
test_setup_owns_path_hygiene_not_a_runtime_allowlist = (
    _codespaces.test_setup_owns_path_hygiene_not_a_runtime_allowlist
)
test_alias_and_function_do_not_shadow_path_application = (
    _codespaces.test_alias_and_function_do_not_shadow_path_application
)
test_missing_python_is_an_explicit_prerequisite_outcome = (
    _codespaces.test_missing_python_is_an_explicit_prerequisite_outcome
)
test_prepared_python_bypasses_ambient_discovery = _codespaces.test_prepared_python_bypasses_ambient_discovery
test_invalid_prepared_python_is_not_an_ambient_fallback = (
    _codespaces.test_invalid_prepared_python_is_not_an_ambient_fallback
)
test_discovery_errors_are_not_reported_as_missing_python = (
    _codespaces.test_discovery_errors_are_not_reported_as_missing_python
)
test_discovery_does_not_compile_attest_or_launch = _codespaces.test_discovery_does_not_compile_attest_or_launch
test_declared_bootstrap_never_falls_back_to_discovery = (
    _codespaces.test_declared_bootstrap_never_falls_back_to_discovery
)
test_runtime_gate_does_not_fall_back_after_prerequisite_failure = (
    _codespaces.test_runtime_gate_does_not_fall_back_after_prerequisite_failure
)
test_binstub_publication_retains_managed_bootstrap = _retained.test_binstub_publication_retains_managed_bootstrap
test_adopted_configuration_reader_ignores_path_python = (
    _index.test_adopted_configuration_reader_ignores_path_python
)
test_missing_newest_candidate_does_not_abort_probe = _logger.test_missing_newest_candidate_does_not_abort_probe
test_no_candidate_installed_returns_null_not_throw = _logger.test_no_candidate_installed_returns_null_not_throw
test_fallback_preserves_block_scalars_and_following_state_flags = (
    _yaml.test_fallback_preserves_block_scalars_and_following_state_flags
)
test_fallback_keeps_more_indented_block_content_literal = (
    _yaml.test_fallback_keeps_more_indented_block_content_literal
)
test_fallback_rejects_partially_dedented_block_content = _yaml.test_fallback_rejects_partially_dedented_block_content
test_fallback_ends_sequence_mapping_block_before_sibling = (
    _yaml.test_fallback_ends_sequence_mapping_block_before_sibling
)
test_fallback_preserves_colons_in_sequence_block_text = _yaml.test_fallback_preserves_colons_in_sequence_block_text
test_fallback_preserves_folded_blank_line_boundaries = _yaml.test_fallback_preserves_folded_blank_line_boundaries
test_fallback_clip_does_not_add_a_break_at_eof = _yaml.test_fallback_clip_does_not_add_a_break_at_eof
test_fallback_supports_explicit_block_indentation_in_either_header_order = (
    _yaml.test_fallback_supports_explicit_block_indentation_in_either_header_order
)
test_fallback_dedented_comment_ends_block_without_becoming_content = (
    _yaml.test_fallback_dedented_comment_ends_block_without_becoming_content
)
test_fallback_preserves_whitespace_content_and_folded_boundaries = (
    _yaml.test_fallback_preserves_whitespace_content_and_folded_boundaries
)
