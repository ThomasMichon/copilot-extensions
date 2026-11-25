"""Run Vault's native ingress controls without installing its runtime dependencies."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any, cast

_source = (
    Path(__file__).resolve().parents[3]
    / "plugins/agent-vault/tests/test_managed_service_launcher_ps1.py"
)
_spec = importlib.util.spec_from_file_location("_vault_windows_service_source", _source)
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)
_original = cast(Any, _module)
pytestmark = _original.pytestmark
windows_service = _original.windows_service
service_shell = _original.service_shell
test_windows_vault_service_uses_prepared_uv_before_python = (
    _original.test_windows_vault_service_uses_prepared_uv_before_python
)
test_windows_vault_missing_preparation_refuses_before_python = (
    _original.test_windows_vault_missing_preparation_refuses_before_python
)
test_windows_vault_publication_is_atomic_and_idempotent = (
    _original.test_windows_vault_publication_is_atomic_and_idempotent
)
