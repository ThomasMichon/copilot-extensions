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

Real review findings (PR #4155, rounds 3-4): a line-oriented regex over the
raw text is the wrong tool for this -- it kept losing to valid YAML scalar
forms (single/double-quoted values, nested action subdirectory paths, and
finally multiline block scalars like `uses: >-` folded onto the next line).
Parsing the YAML properly and walking every step's own `uses:` VALUE (after
PyYAML has already resolved whatever scalar style it was written in) closes
this class of gap for good -- there is no other scalar form left to miss.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

GH_AW_ACTION_PREFIX = "github/gh-aw/actions/"
FULL_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _iter_steps(workflow: dict):
    """Yield every step dict from every job in a parsed workflow document."""
    jobs = workflow.get("jobs") if isinstance(workflow, dict) else None
    if not isinstance(jobs, dict):
        return
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if isinstance(step, dict):
                yield step


def find_unpinned_refs() -> list[str]:
    violations: list[str] = []
    if not WORKFLOWS_DIR.is_dir():
        return violations
    for lock_file in sorted(WORKFLOWS_DIR.glob("*.lock.yml")):
        text = lock_file.read_text(encoding="utf-8")
        try:
            workflow = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            violations.append(
                f"{lock_file.relative_to(REPO_ROOT)}: could not parse as YAML "
                f"to check action pins ({exc})."
            )
            continue
        for step in _iter_steps(workflow):
            uses = step.get("uses")
            if not isinstance(uses, str):
                continue
            uses = uses.strip()
            if not uses.startswith(GH_AW_ACTION_PREFIX):
                continue
            if "@" not in uses:
                # No ref at all -- not a form gh-aw's compiler emits, but
                # flag it rather than silently ignore an unexpected shape.
                violations.append(
                    f"{lock_file.relative_to(REPO_ROOT)}: step uses '{uses}' "
                    "has no '@<ref>' suffix -- cannot verify it is SHA-pinned."
                )
                continue
            _, ref = uses.rsplit("@", 1)
            if not FULL_SHA_PATTERN.match(ref):
                violations.append(
                    f"{lock_file.relative_to(REPO_ROOT)}: github/gh-aw action "
                    f"pinned by mutable ref '{ref}' (uses: {uses}), not a "
                    "40-character commit SHA -- recompile with "
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
