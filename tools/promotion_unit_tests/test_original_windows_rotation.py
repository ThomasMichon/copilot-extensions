"""Export original deterministic rotation controls without changing the schedule."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_select_windows_rotation.py"
_spec = importlib.util.spec_from_file_location("promotion_original_windows_rotation", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_picks_the_expected_count",
    "test_picks_are_known_and_tier_a_has_no_duplicates",
    "test_deterministic_for_the_same_date",
    "test_every_tier_a_plugin_is_reachable_across_the_rotation_cycle",
    "test_every_tier_b_plugin_is_reachable_across_the_rotation_cycle",
    "test_cli_json_format",
    "test_cli_github_output_format",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original rotation control: {_name}"
    globals()[_name] = getattr(_original, _name)
