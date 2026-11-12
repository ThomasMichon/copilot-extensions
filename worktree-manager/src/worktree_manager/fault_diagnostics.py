"""Optional fault output independent of a terminal's screen rendering."""

from __future__ import annotations

import faulthandler
import os
from pathlib import Path
import sys

_stream = None


def enable() -> None:
    global _stream
    if faulthandler.is_enabled():
        return
    directory = os.environ.get("WORKTREE_MANAGER_FAULT_DIR")
    output = None
    if directory:
        try:
            root = Path(directory)
            if root.is_absolute():
                output = (root / f"manager-{os.getpid()}.fault.log").open("xb", buffering=0)
                faulthandler.enable(file=output)
                # faulthandler retains only the descriptor, not the file object.
                _stream = output
                return
        except (AttributeError, OSError, ValueError):
            if output is not None:
                output.close()
    if sys.stderr is not None:
        try:
            faulthandler.enable()
        except (AttributeError, OSError, ValueError):
            pass
