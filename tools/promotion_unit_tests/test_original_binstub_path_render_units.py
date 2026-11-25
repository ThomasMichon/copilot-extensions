"""Original launcher path-rendering cases without native launcher execution."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
if str(_root / "tools") not in sys.path:
    sys.path.insert(0, str(_root / "tools"))
_path = _root / "tools" / "test_runtime_application_binstub_paths.py"
_spec = importlib.util.spec_from_file_location("promotion_original_binstub_path_render", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_global_launchers_share_one_windows_file_root_spelling",
    "test_other_device_namespaces_keep_the_explicit_root_binding",
    "test_extended_only_names_keep_the_explicit_root_binding",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
