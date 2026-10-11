"""Test-time sys.path wiring, matching the established canonical-lib pattern
(e.g. ``libs/ssh-manager/tests/conftest.py``, ``libs/fleet-index-adapter/
tests/conftest.py``): CI's "Canonical shared-library tests" step runs each
lib's suite directly via ``python -m pytest -q libs/<lib>/tests`` with no
install step, so every sibling import this suite needs must be put on
``sys.path`` here rather than assumed from an editable install.
"""

from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parents[1]
_REPO_ROOT = _LIB.parents[1]

sys.path.insert(0, str(_LIB / "src"))
sys.path.insert(0, str(_REPO_ROOT / "libs" / "fleet-contracts" / "src"))
