"""Export the original worker-limit refusal without altering threaded tests."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_parallel_artifact_hash.py"
_spec = importlib.util.spec_from_file_location("promotion_original_parallel_artifact_hash", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = ("test_payload_hash_rejects_unbounded_worker_counts_before_read",)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original parallel-hash refusal: {_name}"
    globals()[_name] = getattr(_original, _name)
