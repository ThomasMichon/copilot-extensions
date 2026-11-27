"""Retain activated-runtime shadow controls at the Containers metadata boundary."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

_path = Path(__file__).resolve().parents[3] / "tools/test_uninstall_path_candidates.py"
_spec = importlib.util.spec_from_file_location("_containers_uninstall_metadata_source", _path)
assert _spec is not None and _spec.loader is not None
_source = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _source
_spec.loader.exec_module(_source)
pytestmark = [
    pytest.mark.portfolio_tier("T2"), pytest.mark.effect("process"),
    pytest.mark.effect("filesystem"),
]
interpreters = _source.interpreters
test_activated_runtime_does_not_shadow_native_uninstall_metadata = (
    _source.test_activated_runtime_does_not_shadow_native_uninstall_metadata
)
test_windows_legacy_uninstall_enumerates_all_external_candidates = (
    _source.test_windows_legacy_uninstall_enumerates_all_external_candidates
)
