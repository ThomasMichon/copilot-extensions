#!/usr/bin/env python3
"""Guard: every compiled gh-aw lock file was produced by the pinned CLI version.

`gh-aw` (the `gh aw` CLI extension used to compile `.md` workflow sources
into checked-in `.lock.yml` files) is an actively-developed external tool
with no dependency-manifest entry this repo's own tooling tracks -- a
contributor's local `gh aw compile` silently uses whatever version happens
to be installed on their machine, with no error and no diff-visible warning
if that drifts from what the rest of the team compiled with. A compiler
version bump can change generated job structure, permissions, or security
scanning behavior with no source-level diff to review.

This guard closes that gap the same way `check-gh-aw-action-pins.py` closes
the analogous action-SHA gap: read the pinned version from
`.github/gh-aw-version.txt` (the single source of truth for which `gh aw`
release this repo compiles against) and fail loudly if any committed
`*.lock.yml` under `.github/workflows/` was compiled by a different version
-- each lock file records its own `compiler_version` in its leading
`gh-aw-metadata` JSON comment, so no separate registry is needed.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
VERSION_FILE = REPO_ROOT / ".github" / "gh-aw-version.txt"

METADATA_PATTERN = re.compile(r"^#\s*gh-aw-metadata:\s*(.+?)\s*$")


def read_pinned_version() -> str:
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def find_version_mismatches() -> list[str]:
    violations: list[str] = []
    if not WORKFLOWS_DIR.is_dir():
        return violations
    pinned = read_pinned_version()
    for lock_file in sorted(WORKFLOWS_DIR.glob("*.lock.yml")):
        text = lock_file.read_text(encoding="utf-8")
        first_line = text.splitlines()[0] if text else ""
        match = METADATA_PATTERN.match(first_line)
        if not match:
            violations.append(
                f"{lock_file.relative_to(REPO_ROOT)}: no gh-aw-metadata "
                "comment found on the first line -- cannot verify the "
                "compiler version that produced this file."
            )
            continue
        try:
            metadata = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            violations.append(
                f"{lock_file.relative_to(REPO_ROOT)}: gh-aw-metadata "
                f"comment is not valid JSON ({exc})."
            )
            continue
        if not isinstance(metadata, dict):
            violations.append(
                f"{lock_file.relative_to(REPO_ROOT)}: gh-aw-metadata "
                f"comment decoded to a non-object JSON value "
                f"({type(metadata).__name__}) -- cannot verify the "
                "compiler version that produced this file."
            )
            continue
        actual = metadata.get("compiler_version")
        if actual != pinned:
            violations.append(
                f"{lock_file.relative_to(REPO_ROOT)}: compiled with "
                f"gh-aw {actual!r}, but the repo is pinned to {pinned!r} "
                f"(.github/gh-aw-version.txt) -- recompile with the "
                "pinned version, or update the pin deliberately if "
                "upgrading."
            )
    return violations


def main() -> int:
    violations = find_version_mismatches()
    if violations:
        print("gh-aw compiler version guard FAILED:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation}", file=sys.stderr)
        return 1
    print(
        "gh-aw compiler version guard: all lock files match the pinned "
        f"version ({read_pinned_version()!r})."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
