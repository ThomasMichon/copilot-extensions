"""Original release-scope checker cases using fixture-only policy and pages."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
if str(_root / "tools") not in sys.path:
    sys.path.insert(0, str(_root / "tools"))
_path = _root / "tools" / "test_check_promotion_release_scope.py"
_spec = importlib.util.spec_from_file_location("promotion_original_release_scope_fixtures", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_reports_unclassified_entries_unreleased_sources_and_links",
    "test_a_classified_tree_passes",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
