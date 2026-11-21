"""Export the original exact bundled-artifact exception witnesses."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_installer_dependency_policy.py"
_spec = importlib.util.spec_from_file_location("promotion_original_dependency_bundled_artifact", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)
bundled_policy = _original.bundled_policy

_SELECTED = ("test_bundled_dbus_next_exception_is_exact",)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original bundled-artifact control: {_name}"
    globals()[_name] = getattr(_original, _name)
