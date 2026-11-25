"""Export external-helper admission without installing Index's application."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any, cast

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
