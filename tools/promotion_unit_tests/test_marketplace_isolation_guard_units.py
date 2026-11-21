"""Export original temporary-fixture marketplace isolation analysis units."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_check_marketplace_isolation.py"
_spec = importlib.util.spec_from_file_location("promotion_original_marketplace_isolation_guard", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

for _name, _function in vars(_original).items():
    if _name.startswith("test_") and _name != "test_payload_catalog_adopter_capabilities_avoid_bare_global_commands":
        assert _name not in globals(), f"duplicate original marketplace isolation test: {_name}"
        globals()[_name] = _function
