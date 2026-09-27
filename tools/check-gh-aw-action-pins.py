#!/usr/bin/env python3
"""Guard: every gh-aw-compiled lock workflow pins github/gh-aw actions by SHA.

Real review finding (PR #4155, round 4): `gh aw compile` (invoked without an
explicit `--action-tag <sha>`) emits `uses: github/gh-aw/actions/<name>@<tag>`
with a *mutable* version tag (e.g. `v0.89.21`) by default -- a tag move can
silently change the security-sensitive agent runtime with no source diff to
review. The only way to get an immutable SHA pin is to pass a raw SHA via
`--action-tag <sha>` on every single recompile; nothing in the committed
source enforces or remembers this, so a future contributor who runs a plain
`gh aw compile` after an unrelated edit would silently regress the pin with
no error and no diff-visible warning.

This guard closes that gap the same way `check-trusted-ci.py` closes the
analogous `runs-on:` gap: fail loudly, in every future CI run, if any
committed `*.lock.yml` under `.github/workflows/` ever references a
`github/gh-aw/actions/...@<ref>` action by anything other than a full
40-character commit SHA.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

# Matches `uses: github/gh-aw/actions/<name>@<ref>` (any ref shape); the
# capture group is checked separately against the 40-hex-char SHA pattern so
# the error message can show exactly what was found.
ACTION_REF_PATTERN = re.compile(
    r"uses:\s*github/gh-aw/actions/[\w-]+@(\S+)"
)
FULL_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def find_unpinned_refs() -> list[str]:
    violations: list[str] = []
    if not WORKFLOWS_DIR.is_dir():
        return violations
    for lock_file in sorted(WORKFLOWS_DIR.glob("*.lock.yml")):
        text = lock_file.read_text(encoding="utf-8")
        for line_num, line in enumerate(text.splitlines(), start=1):
            match = ACTION_REF_PATTERN.search(line)
            if not match:
                continue
            ref = match.group(1)
            if not FULL_SHA_PATTERN.match(ref):
                violations.append(
                    f"{lock_file.relative_to(REPO_ROOT)}:{line_num}: "
                    f"github/gh-aw action pinned by mutable ref '{ref}', "
                    "not a 40-character commit SHA -- recompile with "
                    "`gh aw compile --action-tag <full-sha>` "
                    "(resolve the SHA from github/gh-aw itself, not "
                    "github/gh-aw-actions -- they are different repos)."
                )
    return violations


def main() -> int:
    violations = find_unpinned_refs()
    if violations:
        print("gh-aw action pin guard FAILED:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation}", file=sys.stderr)
        return 1
    print("gh-aw action pin guard: all github/gh-aw action refs are SHA-pinned.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
