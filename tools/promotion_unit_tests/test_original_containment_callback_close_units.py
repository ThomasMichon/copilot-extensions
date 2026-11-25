"""Original callback-routing and fake-owner close controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
_path = _root / "tools" / "test_plugin_test_containment.py"
_spec = importlib.util.spec_from_file_location("promotion_original_containment_callback_close", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_tree_size_uses_parallel_readers_only_on_windows",
    "test_windows_job_waits_for_every_member_before_closing_handle",
    "test_windows_job_reports_unproven_termination_instead_of_claiming_reaped",
    "test_expired_windows_job_requests_termination_without_verification_extension",
    "test_preliminary_accounting_failure_still_retires_owned_job",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
