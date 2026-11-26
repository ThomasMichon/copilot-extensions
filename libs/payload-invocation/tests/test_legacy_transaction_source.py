"""Expose the native persistent-descriptor controls without application imports."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[3] / "tools/test_legacy_transaction_interop.py"
_spec = importlib.util.spec_from_file_location("_legacy_transaction_source", _path)
assert _spec is not None and _spec.loader is not None
_source = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_source)
test_python_backend_admits_the_held_persistent_inode = (
    _source.test_python_backend_admits_the_held_persistent_inode
)
test_python_backend_refuses_a_replaced_or_linked_name = (
    _source.test_python_backend_refuses_a_replaced_or_linked_name
)
