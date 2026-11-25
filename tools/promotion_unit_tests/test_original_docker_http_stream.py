"""Original in-memory HTTP framing contracts, without a daemon transport."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
_library = _root / "libs" / "docker-resource-producer"
if str(_library / "src") not in sys.path:
    sys.path.insert(0, str(_library / "src"))
_path = _library / "tests" / "test_http_stream.py"
_spec = importlib.util.spec_from_file_location("promotion_original_docker_http_stream", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_complete_chunked_body_and_trailers",
    "test_partial_ambiguous_or_unsupported_chunks_refuse",
    "test_response_framing_refuses",
    "test_header_and_line_budgets_are_bounded",
    "test_empty_deletion_response_needs_no_length_header",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
