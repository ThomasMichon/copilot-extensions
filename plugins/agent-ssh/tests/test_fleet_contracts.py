"""Collect the canonical pure contract suite in the consuming plugin's CI lane."""

import runpy
from pathlib import Path

_PLUGIN = Path(__file__).resolve().parents[1]
_CANDIDATES = (
    _PLUGIN / "libs" / "fleet-contracts" / "tests" / "test_records.py",
    _PLUGIN.parents[1] / "libs" / "fleet-contracts" / "tests" / "test_records.py",
)
_SOURCE = next((path for path in _CANDIDATES if path.is_file()), None)
if _SOURCE is None:
    raise RuntimeError("the canonical fleet contract test suite is missing")
globals().update({
    name: value for name, value in runpy.run_path(str(_SOURCE)).items()
    if name.startswith("test_")
})
