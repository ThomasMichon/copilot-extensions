"""Retain the original Index constructor call-boundary and refusal regressions."""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
import sys
from typing import Any

_root = Path(__file__).resolve().parents[3]


def _load(relative: str, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _root / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(_root / "plugins/agent-index/tests"))
try:
    importlib.import_module("test_installer_publication_and_health")
    _server = _load(
        "plugins/agent-index/tests/test_server_venv_provisioning.py", "_index_server_source",
    )
    _publication = _load(
        "plugins/agent-index/tests/test_installer_publication_provisioning.py",
        "_index_publication_source",
    )
    _selection = _load("tools/test_installer_bootstrap_selection.py", "_index_selection_source")
finally:
    sys.path.pop(0)

test_index_venv_propagation = _selection.test_index_venv_propagation
test_server_venv_prefers_signed_python_copies_mode_before_uv_fallback = (
    _server.test_server_venv_prefers_signed_python_copies_mode_before_uv_fallback
)
test_rejected_discoverable_uv_refuses_unseasoned_cli_server_and_engine = (
    _publication.test_rejected_discoverable_uv_refuses_unseasoned_cli_server_and_engine
)
