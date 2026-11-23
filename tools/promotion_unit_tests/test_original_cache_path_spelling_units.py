"""Original pure cache-path spelling and non-Windows identity controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_plugin_test_cache.py"
_spec = importlib.util.spec_from_file_location("promotion_original_cache_path_spelling", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_private_fixture_preserves_unc_and_native_root_spelling",
    "test_windows_cache_spelling_preserves_drive_unc_and_existing_prefix",
    "test_non_windows_cache_spelling_is_unchanged",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
