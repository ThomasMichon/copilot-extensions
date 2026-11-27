"""Expose native SSH metadata and retained Linux handoff controls."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import ModuleType

_tests = Path(__file__).resolve().parents[3] / "plugins/agent-ssh/tests"
_package = ModuleType("_ssh_metadata_source")
_package.__path__ = [str(_tests)]
sys.modules[_package.__name__] = _package
_source = importlib.import_module(_package.__name__ + ".test_bootstrap_native_metadata")
_driver = importlib.import_module(_package.__name__ + ".test_bootstrap_driver")
_routing = importlib.import_module(_package.__name__ + ".test_bootstrap_check_reconcile_always_on")
pytestmark = _source.pytestmark
packaged_command = _source.packaged_command
bootstrap_metadata = _source.bootstrap_metadata
test_metadata_current_needs_no_python = _source.test_metadata_current_needs_no_python
test_metadata_preserves_existing_action = _source.test_metadata_preserves_existing_action
test_metadata_retains_existing_holds = _source.test_metadata_retains_existing_holds
test_metadata_invalid_field_never_submits = _source.test_metadata_invalid_field_never_submits
test_metadata_invalid_document_never_submits = _source.test_metadata_invalid_document_never_submits
test_metadata_version_linefeed_keeps_identity = _source.test_metadata_version_linefeed_keeps_identity
test_metadata_source_linefeed_keeps_identity = _source.test_metadata_source_linefeed_keeps_identity
test_metadata_uses_one_document_when_manifest_replaced = (
    _source.test_metadata_uses_one_document_when_manifest_replaced
)
test_metadata_missing_selected_source_never_falls_back = (
    _source.test_metadata_missing_selected_source_never_falls_back
)
test_native_bootstrap_validates_launch_handoff_without_installing = (
    _driver.test_native_bootstrap_validates_launch_handoff_without_installing
)
test_sh_reconciles_without_any_opt_in_config = _routing.test_sh_reconciles_without_any_opt_in_config
test_sh_reconciles_even_with_stale_opt_in_key_present = (
    _routing.test_sh_reconciles_even_with_stale_opt_in_key_present
)
