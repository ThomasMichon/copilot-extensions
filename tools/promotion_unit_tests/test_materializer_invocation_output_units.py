"""Export original fixture-only invocation output controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_materialize_main.py"
_spec = importlib.util.spec_from_file_location("promotion_original_materializer_invocation_output_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_invocation_promotion_rebuilds_canonical_helpers_and_toolkit",
    "test_invocation_promotion_uses_the_version_stamped_candidate_root",
    "test_invocation_source_check_accepts_stale_or_absent_outputs",
    "test_invocation_materialized_equality_refuses_byte_drift",
    "test_invocation_source_check_refuses_invalid_python",
    "test_invocation_source_cli_keeps_check_and_source_modes_distinct",
    "test_invocation_materializer_refuses_corrupt_writer_result",
    "test_invocation_materializer_never_executes_candidate_tooling",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original invocation output control: {_name}"
    globals()[_name] = getattr(_original, _name)
