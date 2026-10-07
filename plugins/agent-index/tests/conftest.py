from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

# Re-exported so any test file can request these fixtures by name without a
# per-file import (pytest discovers fixtures registered here automatically).
# See _indexer_assignment_fixtures.py for the mechanics each one stands in for.
from _indexer_assignment_fixtures import (  # noqa: E402,F401
    machine_local_indexer_overlay,
    paired_knowledge_repo_indexer,
)
