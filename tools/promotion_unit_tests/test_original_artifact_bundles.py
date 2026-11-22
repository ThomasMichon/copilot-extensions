"""Export original synthetic bundle controls without changing their witnesses."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_promotion_python_artifacts.py"
_spec = importlib.util.spec_from_file_location("promotion_original_artifact_bundles", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

no_ambient_git_identity = _original.no_ambient_git_identity
_SELECTED = (
    "test_bundle_and_transfer_roundtrip",
    "test_bundle_wheel_read_failures_are_structured",
    "test_build_cli_creates_missing_output_directory",
    "test_build_cli_reports_invalid_output_directory",
    "test_identical_artifact_bundles_ignore_clock_mtimes_permissions_and_wheel_order",
    "test_trusted_validator_rejects_archive_corruption_even_with_valid_outer_digest",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original bundle control: {_name}"
    globals()[_name] = getattr(_original, _name)
