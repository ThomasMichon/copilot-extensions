"""Test-only path wiring: make the real ``agent_index`` package importable.

This is deliberately a **test-time-only** cross-reference, never a packaged
runtime dependency -- ``fleet-index-adapter`` consumes agent-index purely as
an HTTP backend at runtime (see ``adapter.py``). Mirrors the precedent noted
in ``libs/fleet-contracts/README.md`` (its tests are "also collected by the
agent-ssh consumer's thin test projection, so its required test lane
exercises both the wire contract and the real static-driver CLI"): proving a
contract against a sibling's real implementation belongs in tests, not in
the shipped dependency graph.

A missing agent-index checkout (e.g. a partial sparse checkout) degrades
gracefully: the live-integration tests skip via ``pytest.importorskip`` /
import failure rather than failing the whole suite, since the stand-in-
backend tests above already cover the adapter's own logic independently.
"""

from __future__ import annotations

import sys
from pathlib import Path

_AGENT_INDEX_SRC = Path(__file__).resolve().parents[3] / "plugins" / "agent-index" / "src"
if _AGENT_INDEX_SRC.is_dir() and str(_AGENT_INDEX_SRC) not in sys.path:
    sys.path.insert(0, str(_AGENT_INDEX_SRC))
