"""Export original artifact-admission integrity and provenance decisions."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_promotion_dispatch.py"
_spec = importlib.util.spec_from_file_location("promotion_original_dispatch_artifact_integrity_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_source_admission_integrity_cannot_be_invented_by_artifact_metadata",
    "test_untrusted_or_legacy_artifact_uploads_cannot_claim_current_source_ownership",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
