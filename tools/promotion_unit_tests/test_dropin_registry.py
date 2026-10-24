"""Expose the original drop-in registry unit bodies without copying them."""

from __future__ import annotations

import importlib.util
import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "libs/dropin-registry"


def _load_source(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


_load_source("dropin_registry", LIBRARY / "src/dropin_registry/__init__.py")
_original = _load_source(
    "promotion_original_dropin_registry", LIBRARY / "tests/test_dropin_registry.py"
)
for _name, _function in vars(_original).items():
    if _name.startswith("test_") and _name != "test_fifo_is_rejected_without_calling_classifier":
        assert _name not in globals(), f"duplicate original registry test: {_name}"
        globals()[_name] = _function


def test_fifo_is_rejected_without_calling_classifier(tmp_path, monkeypatch):
    """Keep the original refusal assertions; native FIFO creation stays outside units."""
    entry = tmp_path / "blocking.json"
    original_lstat = Path.lstat

    def lstat(path, *args, **kwargs):
        observed = original_lstat(path, *args, **kwargs)
        if path == entry:
            return os.stat_result((stat.S_IFIFO | 0o600, *observed[1:]))
        return observed

    monkeypatch.setattr(_original.os, "mkfifo", lambda path: path.touch(), raising=False)
    monkeypatch.setattr(Path, "lstat", lstat)
    _original.test_fifo_is_rejected_without_calling_classifier(tmp_path)
