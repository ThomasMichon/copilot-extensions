"""Original fixture-owned cache metadata and early-refusal controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_plugin_test_cache.py"
_spec = importlib.util.spec_from_file_location("promotion_original_cache_metadata", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)
graph = _original.graph
released_graph = _original.released_graph

_SELECTED = (
    "test_missing_transitive_source_refuses_instead_of_reusing_stamp",
    "test_script_only_target_has_a_stable_runner_only_cache_key",
    "test_released_constraints_follow_selected_library_extras",
    "test_released_composition_refuses_source_activation_selectors",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
