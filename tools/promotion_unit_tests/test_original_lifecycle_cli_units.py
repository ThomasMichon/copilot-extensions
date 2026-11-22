"""Export original promotion lifecycle CLI decisions."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_promotion_lifecycle.py"
_spec = importlib.util.spec_from_file_location("promotion_original_lifecycle_cli_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_cli_status_blocks_dispatch_without_claiming_a_completed_promotion",
    "test_cli_existing_pr_opening_is_idempotent_but_never_makes_dispatch_idle",
    "test_cli_transport_failure_is_visible_and_writes_no_success_output",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
