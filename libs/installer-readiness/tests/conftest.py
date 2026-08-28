from __future__ import annotations

import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
INSTALLATION_CONTEXT = LIB.parent / "installation-context"
for path in (LIB / "src", INSTALLATION_CONTEXT):
    sys.path.insert(0, str(path))
