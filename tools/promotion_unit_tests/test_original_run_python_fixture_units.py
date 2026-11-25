"""Original Python-wrapper cases with fixture-only resolver and build inputs."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
if str(_root / "tools") not in sys.path:
    sys.path.insert(0, str(_root / "tools"))
_path = _root / "tools" / "test_run_python_wrappers.py"
_spec = importlib.util.spec_from_file_location("promotion_original_run_python_fixtures", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_parse_splits_leading_requirements",
    "test_environment_key_binds_resolver_settings",
    "test_local_requirements_are_never_cached",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
