"""Export original early readiness refusals with their unchanged fixtures."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_promotion_artifact_readiness.py"
_spec = importlib.util.spec_from_file_location("promotion_original_artifact_readiness", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

hosted = _original.hosted
no_ambient_git_identity = _original.no_ambient_git_identity
_SELECTED = (
    "test_source_mismatch_refuses_before_any_release_request",
    "test_cli_preparation_failure_is_nonzero_and_final_publication_never_repairs_readiness",
    "test_publish_cli_requires_a_nonempty_premerge_identity",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original readiness control: {_name}"
    globals()[_name] = getattr(_original, _name)
