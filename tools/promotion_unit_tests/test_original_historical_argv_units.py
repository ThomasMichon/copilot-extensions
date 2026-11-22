"""Export original closed historical-argv inspection controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_installer_engine_stop.py"
_spec = importlib.util.spec_from_file_location("promotion_original_historical_argv_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_closed_historical_inspection_is_not_a_launch",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
