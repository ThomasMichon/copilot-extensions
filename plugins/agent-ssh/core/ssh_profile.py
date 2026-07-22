"""Compatibility wrapper for the packaged agent_ssh.ssh_profile core.

The runtime implementation lives under ``src/agent_ssh`` so the plugin installs
as a normal Python package. This wrapper keeps the documented source-tree path
usable for direct script invocation from a checkout.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_ssh.ssh_profile import *  # noqa: F403
from agent_ssh.ssh_profile import main


if __name__ == "__main__":
    raise SystemExit(main())
