"""Make ``endpoint_rendezvous`` importable for the canonical lib's tests.

``core-delegation`` imports ``endpoint_rendezvous`` for discovery but does not
declare it as a distribution dependency -- a consuming package vendors it
alongside this module instead (see the README). For the canonical lib's own
tests, put the sibling ``libs/endpoint-rendezvous/src`` on the path so the
import resolves without an install step.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SIBLING_SRC = (
    Path(__file__).resolve().parent.parent.parent / "endpoint-rendezvous" / "src"
)
if _SIBLING_SRC.is_dir() and str(_SIBLING_SRC) not in sys.path:
    sys.path.insert(0, str(_SIBLING_SRC))
