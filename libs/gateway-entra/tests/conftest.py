"""Test-time sys.path wiring, matching the canonical-lib pattern
(e.g. ``libs/fleet-vault-adapter/tests/conftest.py``): the suite can run
directly via ``python -m pytest libs/gateway-entra/tests`` without an install,
so the package source and the sibling Fleet SDK source go on ``sys.path``.
The provider tests still need ``joserfc`` (the ``dev`` extra).
"""

from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parents[1]
_REPO_ROOT = _LIB.parents[1]

sys.path.insert(0, str(_LIB / "src"))
sys.path.insert(0, str(_REPO_ROOT / "fleet-manager" / "src"))
