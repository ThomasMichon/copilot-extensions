"""Select the fixture-only Docker identity contract on relevant path changes."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Iterable


def needs_docker_identity_smoke(paths: Iterable[str]) -> bool:
    return any(
        path.startswith("libs/docker-resource-producer/") or path in {
            ".github/workflows/ci.yml", "tools/docker_identity_ci_scope.py",
            "tools/test_docker_identity_ci_scope.py", "tools/accumulate_bumps.py",
            "tools/test_accumulate_bumps.py",
        }
        for path in paths
    )


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}  # headless-guard: allow dependency-free CI selector
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, timeout=10,
        env={**{key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")},
             "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"}, **kwargs,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--event", default="")
    args = parser.parse_args(argv)
    if args.event == "workflow_dispatch" or not args.base:
        print("true")
        return 0
    try:
        available = _git("rev-parse", "--verify", f"{args.base}^{{commit}}")
        if available.returncode:
            print("docker-identity scope: base revision unavailable; selecting smoke", file=sys.stderr)
            print(available.stderr, file=sys.stderr, end="")
            print("true")
            return 0
        changed = _git("diff", "--name-only", f"{args.base}...{args.head}")
    except (OSError, subprocess.SubprocessError) as error:
        print(f"docker-identity scope: Git probe failed: {error}", file=sys.stderr)
        return 1
    if changed.returncode:
        print(changed.stderr, file=sys.stderr, end="")
        return 1
    print("true" if needs_docker_identity_smoke(changed.stdout.splitlines()) else "false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
