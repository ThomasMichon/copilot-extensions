"""Isolated absolute-path entry point for provider-owned Picker commands."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    package_parent = str(Path(__file__).resolve().parent.parent)
    if package_parent not in sys.path:
        sys.path.insert(0, package_parent)
    from agent_containers.__main__ import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
