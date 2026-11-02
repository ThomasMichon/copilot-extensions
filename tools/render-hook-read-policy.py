#!/usr/bin/env python3
"""Render the native read exemption's exact normalized guard-source contract."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "agent-worktrees" / "scripts"
GUARDS = ("statelessness_guard.py", "cross_repo_guard.py", "anchor_write_guard.py", "pr_supersede_guard.py")


def render(scripts: Path = SCRIPTS) -> str:
    guards = {name: hashlib.sha256((scripts / name).read_text(encoding="utf-8-sig").encode("utf-8")).hexdigest() for name in GUARDS}
    return json.dumps({"schema": "agent-worktrees.native-read-policy", "version": 1, "guards": guards}, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = SCRIPTS / "hook-read-policy.json"
    expected = render()
    if args.check:
        if not target.is_file() or target.read_text(encoding="utf-8") != expected:
            parser.exit(1, "Native read policy differs; run python tools/render-hook-read-policy.py\n")
    else:
        target.write_text(expected, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
