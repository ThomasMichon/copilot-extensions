"""Expose the original in-process PR target work-count controls."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKTREES = ROOT / "plugins/agent-worktrees"
_path = sys.path.copy()
try:
    sys.path[:0] = [
        str(WORKTREES / "src"),
        str(ROOT / "libs/agent-procutil/src"),
    ]
    _spec = importlib.util.spec_from_file_location(
        "promotion_original_pr_target_probes",
        WORKTREES / "tests/test_pr_target_probes.py",
    )
    assert _spec and _spec.loader
    _original = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = _original
    _spec.loader.exec_module(_original)
finally:
    sys.path[:] = _path

for _name, _function in vars(_original).items():
    if _name.startswith("test_"):
        assert _name not in globals(), f"duplicate original identity test: {_name}"
        globals()[_name] = _function
