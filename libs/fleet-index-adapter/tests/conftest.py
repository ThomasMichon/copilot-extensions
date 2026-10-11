"""Test-time sys.path wiring, matching the established canonical-lib pattern
(e.g. ``libs/ssh-manager/tests/conftest.py``): CI's "Canonical shared-library
tests" step runs each lib's suite directly via ``python -m pytest -q
libs/<lib>/tests`` with no install step, so every sibling import this suite
needs must be put on ``sys.path`` here rather than assumed from an editable
install.

``agent_index`` is a genuine exception to that sibling-lib pattern: it is a
full Copilot CLI plugin, not a canonical lib this package vendors or depends
on at runtime (see ``adapter.py``'s module docstring -- this adapter consumes
agent-index purely as an HTTP backend). Reaching its source here is a
test-time-only cross-reference to prove the adapter against the real,
unmodified implementation (mirrors the precedent noted in
``libs/fleet-contracts/README.md``: its tests are "also collected by the
agent-ssh consumer's thin test projection, so its required test lane
exercises both the wire contract and the real static-driver CLI"). A missing
agent-index checkout degrades gracefully: the live-integration tests below
skip via ``pytest.importorskip`` rather than failing the whole suite, since
the stand-in-backend tests already cover the adapter's own logic
independently.
"""

from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parents[1]
_REPO_ROOT = _LIB.parents[1]

sys.path.insert(0, str(_LIB / "src"))
sys.path.insert(0, str(_REPO_ROOT / "libs" / "fleet-contracts" / "src"))

_AGENT_INDEX_SRC = _REPO_ROOT / "plugins" / "agent-index" / "src"
if _AGENT_INDEX_SRC.is_dir() and str(_AGENT_INDEX_SRC) not in sys.path:
    sys.path.insert(0, str(_AGENT_INDEX_SRC))
