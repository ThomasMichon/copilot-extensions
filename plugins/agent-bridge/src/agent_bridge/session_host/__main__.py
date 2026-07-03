"""``python -m agent_bridge.session_host`` -> the standalone host launcher."""

from __future__ import annotations

import sys

from .launcher import main

if __name__ == "__main__":
    sys.exit(main())
