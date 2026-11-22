"""Export original ZIP32/ZIP64 directory-count boundary controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_installer_wheel_inputs.py"
_spec = importlib.util.spec_from_file_location("promotion_original_zip_directory_boundary_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)
limits = _original.limits

_SELECTED = (
    "test_stdlib_directory_count_boundaries_allow_zip32_and_zip64",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
