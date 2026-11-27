"""Expose native Machines manifest controls without machine-operation dependencies."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import ModuleType

_tests = Path(__file__).resolve().parents[3] / "plugins/agent-machines/tests"
_package = ModuleType("_machines_manifest_source")
_package.__path__ = [str(_tests)]
sys.modules[_package.__name__] = _package
_source = importlib.import_module(_package.__name__ + ".test_bootstrap_manifest_native")
packaged_command = _source.packaged_command
bootstrap = _source.bootstrap
test_native_manifest_current_is_quiet_without_python = (
    _source.test_native_manifest_current_is_quiet_without_python
)
test_native_manifest_stale_retains_publication_and_launch_owner = (
    _source.test_native_manifest_stale_retains_publication_and_launch_owner
)
test_native_namespaced_manifest_refuses_invalid_without_fallback = (
    _source.test_native_namespaced_manifest_refuses_invalid_without_fallback
)
test_native_first_use_still_precedes_manifest_reader = (
    _source.test_native_first_use_still_precedes_manifest_reader
)
test_native_legacy_invalid_manifest_never_publishes = (
    _source.test_native_legacy_invalid_manifest_never_publishes
)
test_native_governance_refusal_precedes_manifest_reader = (
    _source.test_native_governance_refusal_precedes_manifest_reader
)
test_native_stale_manifest_cannot_bypass_publication = (
    _source.test_native_stale_manifest_cannot_bypass_publication
)
