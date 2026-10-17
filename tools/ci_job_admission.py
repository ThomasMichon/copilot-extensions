"""Select standalone CI jobs before runner allocation; unknown impact runs all."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from collections.abc import Iterable


@dataclass(frozen=True)
class Admission:
    fleet: bool
    manager: bool
    dispatch: bool
    reason: str

    def outputs(self) -> str:
        """Render only fixed-name, validated GitHub step outputs."""
        return "\n".join((
            "admission_validated=true",
            f"fleet_contracts={str(self.fleet).lower()}",
            f"manager_contracts={str(self.manager).lower()}",
            f"dispatch_contracts={str(self.dispatch).lower()}",
        ))


def select_jobs(paths: Iterable[str], *, event: str) -> Admission:
    """Conservatively select the first standalone admission families."""
    if event != "pull_request":
        return Admission(True, True, True, "full-event")
    paths = tuple(paths)
    for path in paths:
        if (
            not isinstance(path, str) or not path or "\\" in path
            or any(ord(char) < 32 for char in path)
            or PurePosixPath(path).is_absolute()
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or ":" in path
        ):
            raise ValueError("changed paths must be unambiguous repository-relative paths")
    fleet = manager = dispatch = False
    for path in paths:
        if path.startswith(("libs/", "tools/", ".github/")):
            return Admission(True, True, True, "shared-input")
        if path.startswith("fleet-manager/"):
            fleet = True
        elif path.startswith("worktree-manager/"):
            manager = True
        elif path.startswith("services/dispatch/"):
            dispatch = True
        elif path.startswith("plugins/"):
            # Manager imports the engine and consumes plugin-owned pivot data.
            manager = True
            if path.startswith("plugins/agent-dispatch/"):
                dispatch = True
        elif path.startswith(("docs/", "efforts/", "visions/")) and path.endswith(".md"):
            continue
        elif "/" not in path and path.endswith(".md"):
            # TESTING governs the harnesses even when no implementation changed.
            if path == "TESTING.md":
                return Admission(True, True, True, "shared-test-contract")
        elif path.startswith("services/index/"):
            continue
        else:
            return Admission(True, True, True, "unclassified-input")
    return Admission(fleet, manager, dispatch, "changed-paths")


def changed_paths(repo: Path, base: str, head: str) -> list[str]:
    """Read the pinned PR diff, including both sides of renames, without helpers."""
    if any(not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha) for sha in (base, head)):
        raise ValueError("PR admission requires explicit full base and head commit IDs")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_LAZY_FETCH="1")
    result = subprocess.run(
        ["git", "-C", str(repo), "diff", "--no-ext-diff", "--no-textconv",
         "--name-only", "--no-renames", "-z", f"{base}...{head}", "--"],
        env=env, capture_output=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"cannot establish changed-path admission: {detail}")
    if result.stdout and not result.stdout.endswith(b"\0"):
        raise ValueError("changed-path output is not NUL terminated")
    return result.stdout.decode("utf-8").split("\0")[:-1] if result.stdout else []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="")
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        paths = changed_paths(args.repository, args.base, args.head) if args.event == "pull_request" else []
        selected = select_jobs(paths, event=args.event)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"CI admission failed: {exc}", file=sys.stderr)
        return 1
    print(f"CI standalone admission: {selected.reason}", file=sys.stderr)
    print(selected.outputs())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
