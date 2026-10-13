"""Select bounded installation-context CI coverage from changed paths."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Iterable


def needs_installation_context_smoke(paths: Iterable[str]) -> bool:
    for path in paths:
        if path in {
            ".github/workflows/ci.yml",
            "tools/sync-installation-context.py",
            "tools/installation_context_ci_scope.py",
            "tools/test_installation_context_ci_scope.py",
        } or path.startswith("libs/installation-context/"):
            return True
        if path.startswith("plugins/") and "/scripts/installation-context/" in path:
            return True
        if path.startswith(("plugins/", "worktree-manager/src/")):
            name = path.rsplit("/", 1)[-1]
            if name.startswith("_installation_context") and name.endswith(".py"):
                return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--event", default="")
    args = parser.parse_args(argv)
    if args.event == "workflow_dispatch" or not args.base:
        print("true")
        return 0
    available = subprocess.run(
        ["git", "rev-parse", "--verify", f"{args.base}^{{commit}}"],
        capture_output=True, text=True, check=False,
    )
    if available.returncode:
        print(
            "installation-context scope: base revision unavailable; selecting smoke",
            file=sys.stderr,
        )
        print(available.stderr, file=sys.stderr, end="")
        print("true")
        return 0
    changed = subprocess.run(
        ["git", "diff", "--name-only", f"{args.base}...{args.head}"],
        capture_output=True, text=True, check=False,
    )
    if changed.returncode:
        print(changed.stderr, file=sys.stderr, end="")
        return 1
    print("true" if needs_installation_context_smoke(changed.stdout.splitlines()) else "false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
