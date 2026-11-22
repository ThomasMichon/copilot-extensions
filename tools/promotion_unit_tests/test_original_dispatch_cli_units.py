"""Export original promotion dispatch CLI decisions."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_promotion_dispatch.py"
_spec = importlib.util.spec_from_file_location("promotion_original_dispatch_cli_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_cli_exposes_only_a_validated_source_and_active_pr_still_prevents_recovery_dispatch",
    "test_cli_rechecks_queued_automatic_source_without_writing_success_on_retirement",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
