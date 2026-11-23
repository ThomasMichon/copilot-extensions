"""Export original pure durable-state mount tests, not guest operations."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_reproducer_lab_operation.py"
_spec = importlib.util.spec_from_file_location("promotion_original_reproducer_state_mount", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_state_mount_is_only_local_mount_evidence",
    "test_supported_nonvolatile_filesystems",
    "test_state_device_must_match_open_directory",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
