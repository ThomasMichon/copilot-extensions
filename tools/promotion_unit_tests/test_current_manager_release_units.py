"""Export in-process Manager release controls without native or artifact actors."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_bootstrap_manager_release.py"
_spec = importlib.util.spec_from_file_location("promotion_original_current_manager_release", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

published_source = _original.published_source
_SELECTED = (
    "test_source_before_wheel_channel_preserves_legacy_fallback",
    "test_missing_release_inputs_are_typed_before_source_preparation",
    "test_ambiguous_release_is_failure_not_unavailability",
    "test_snapshot_entry_does_not_export_or_hide_verification_failure",
    "test_scoped_github_reader_refuses_ambient_account_fallback",
    "test_scoped_binary_reader_does_not_decode_or_inspect_the_asset_body",
    "test_release_discovery_and_attestation_share_scoped_client",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
