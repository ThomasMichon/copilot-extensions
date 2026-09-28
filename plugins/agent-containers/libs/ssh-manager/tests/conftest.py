from __future__ import annotations

import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB / "src"))
# ssh_manager imports agent_procutil at runtime (a real dependency, declared
# in libs/ssh-manager/pyproject.toml's own [tool.uv.sources]) -- when this
# suite runs standalone (not combined with agent-procutil's own tests in the
# same pytest invocation, as CI's canonical-lib test step does), that
# sibling canonical lib's src/ must be on sys.path too, or the import fails
# outright.
sys.path.insert(0, str(LIB.parent / "agent-procutil" / "src"))
