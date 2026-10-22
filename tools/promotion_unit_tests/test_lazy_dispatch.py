"""Run the original shared dispatcher unit bodies without duplicating them."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "libs/lazy-cli-dispatch"


def _load_source(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


_load_source("lazy_cli_dispatch", LIBRARY / "src/lazy_cli_dispatch/__init__.py")
_original = _load_source(
    "promotion_original_lazy_dispatch_tests", LIBRARY / "tests/test_dispatch.py"
)

TestDispatchLazy = _original.TestDispatchLazy
TestSelfOverride = _original.TestSelfOverride
TestCoreHelper = _original.TestCoreHelper
