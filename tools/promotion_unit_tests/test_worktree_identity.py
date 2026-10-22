"""Expose the original metadata-codec unit bodies without copying them."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "libs/worktree-identity"


def _load_source(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


_load_source("worktree_identity", LIBRARY / "src/worktree_identity/__init__.py")
_original = _load_source(
    "promotion_original_worktree_identity", LIBRARY / "tests/test_worktree_identity.py"
)
for _name, _function in vars(_original).items():
    if _name.startswith("test_"):
        assert _name not in globals(), f"duplicate original identity test: {_name}"
        globals()[_name] = _function
