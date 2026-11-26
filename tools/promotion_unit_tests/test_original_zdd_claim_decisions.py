"""Export whole original explicit-data claim-decision contracts."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
_source = str(_root / "libs" / "zdd" / "src")
if _source not in sys.path:
    sys.path.insert(0, _source)
_path = _root / "libs" / "zdd" / "tests" / "test_claims.py"
_spec = importlib.util.spec_from_file_location("promotion_original_zdd_claim_decisions", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_generation_id_is_stable_and_distinct",
    "test_none_record_is_recoverable",
    "test_never_claimed_record_is_recoverable",
    "test_live_owner_is_not_recoverable",
    "test_dead_owner_is_recoverable",
    "test_acquire_free_record",
    "test_idempotent_reacquire_by_same_generation",
    "test_conflict_when_live_generation_holds_claim",
    "test_recover_when_owning_generation_is_dead",
    "test_force_overrides_a_live_conflict",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
