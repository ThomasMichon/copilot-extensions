"""Export original pure attachment-inventory tests, not Docker reader calls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_reproducer_lab_network.py"
_spec = importlib.util.spec_from_file_location("promotion_original_reproducer_network_inventory", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_exact_physical_inventory_is_not_egress_or_custody_proof",
    "test_single_subject_inventory_without_peers",
    "test_unsafe_network_inventory",
    "test_missing_network_fields_refuse",
    "test_every_peer_attachment_is_checked",
    "test_selected_ids_are_full_distinct_and_bounded",
    "test_malformed_inventory_is_a_bounded_failure",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
