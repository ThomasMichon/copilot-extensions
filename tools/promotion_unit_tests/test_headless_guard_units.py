"""Expose the original guard unit bodies and their isolated repository fixture."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_check_headless_launch.py"
_spec = importlib.util.spec_from_file_location("promotion_original_headless_guard", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

repo = _original.repo
for _name, _function in vars(_original).items():
    if _name.startswith("test_"):
        assert _name not in globals(), f"duplicate original guard test: {_name}"
        globals()[_name] = _function
