"""Run real Codespaces prepared removal without importing its service test graph."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

_path = (
    Path(__file__).resolve().parents[3]
    / "plugins/agent-codespaces/tests/native_uninstall/test_external_uninstall_interpreter.py"
)
_spec = importlib.util.spec_from_file_location("_codespaces_prepared_uninstall_source", _path)
assert _spec is not None and _spec.loader is not None
_source = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _source
_spec.loader.exec_module(_source)
pytestmark = [
    pytest.mark.portfolio_tier("T2"), pytest.mark.effect("process"),
    pytest.mark.effect("filesystem"),
]
prepared_uninstall_python = _source.prepared_uninstall_python
test_codespaces_prepared_uninstall_preserves_state_and_never_uses_ambient_python = (
    _source.test_codespaces_prepared_uninstall_preserves_state_and_never_uses_ambient_python
)
