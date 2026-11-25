"""Export original noncontained private build-tool root refusals."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_installer_build_tools_root.py"
_spec = importlib.util.spec_from_file_location("promotion_original_build_tools_noncontained_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = ("test_private_build_tools_still_refuse_noncontained_roots",)
globals().update({name: getattr(_original, name) for name in _SELECTED})
