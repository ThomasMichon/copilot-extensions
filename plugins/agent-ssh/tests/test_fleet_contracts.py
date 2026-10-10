"""Collect the canonical pure contract suite in the consuming plugin's CI lane."""

import runpy
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard

_PLUGIN = Path(__file__).resolve().parents[1]
_CANDIDATES = (
    _PLUGIN / "libs" / "fleet-contracts" / "tests",
    _PLUGIN.parents[1] / "libs" / "fleet-contracts" / "tests",
)
_DIRECTORY = next((path for path in _CANDIDATES if path.is_dir()), None)
if _DIRECTORY is None:
    raise RuntimeError("the canonical fleet contract test suite is missing")
for _filename in ("test_records.py", "test_responses.py"):
    _tests = {
        name: value for name, value in runpy.run_path(str(_DIRECTORY / _filename)).items()
        if name.startswith("test_")
    }
    if globals().keys() & _tests.keys():
        raise RuntimeError("canonical fleet contract test names must be unique")
    globals().update(_tests)
