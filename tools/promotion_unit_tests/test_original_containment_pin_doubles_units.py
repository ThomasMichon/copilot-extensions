"""Original pin bookkeeping controls retaining synthetic records and processes."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
_path = _root / "tools" / "test_plugin_test_containment.py"
_spec = importlib.util.spec_from_file_location("promotion_original_containment_pin_doubles", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_native_instance_pins_retain_only_exact_owned_births_until_cleanup",
    "test_native_instance_handle_budget_refuses_without_releasing_prior_custody",
    "test_native_instance_inventory_is_bounded_before_decoding_records",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
