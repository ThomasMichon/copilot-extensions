"""Export original pure vault cache-policy inspection controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_installer_engine_stop.py"
_spec = importlib.util.spec_from_file_location("promotion_original_vault_cache_inspection_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_vault_cache_policy_keeps_exact_runtime_and_configuration_admission",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
