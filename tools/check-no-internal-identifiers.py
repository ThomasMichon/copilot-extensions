#!/usr/bin/env python3
"""Local guard: fail if any private/internal identifier appears in the tree.

This repo is public, so it must never contain internal org/account/project
identifiers (employer org names, internal repo names, personal aliases, …).
A denylist that *named* those strings would itself leak them, so the list is
**never stored in this repo**. It is sourced, privately, from:

  1. env ``COPILOT_EXTENSIONS_FORBIDDEN_IDS`` (comma-separated), and
  2. ``~/.agent-codespaces/forbidden-identifiers.txt`` (one per line; blank
     lines and ``#`` comments ignored).

With neither configured (a fresh clone / CI) there is nothing to enforce and
the check is a no-op (exit 0) -- so it is safe to ship in the public repo. On
your own machine, populate either source and wire this up as a git ``pre-push``
hook; it then blocks a push that would leak any of your identifiers.

Run manually:  python tools/check-no-internal-identifiers.py
Exit code 0 = clean (or nothing configured), 1 = a forbidden identifier was
found (suitable for a pre-push hook).

The same two private sources drive the agent-codespaces scaffold guard
(``plugins/agent-codespaces/tests/test_config_init.py``).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOME_LIST = Path.home() / ".agent-codespaces" / "forbidden-identifiers.txt"

# Files this guard must not flag for merely *implementing* the mechanism.
SELF = {
    "tools/check-no-internal-identifiers.py",
    "plugins/agent-codespaces/tests/test_config_init.py",
}

# Allowlist: (identifier -> path prefixes) where a denylisted substring is a
# legitimate *product/generic* term, not the internal identifier. The sole case
# today is the Microsoft **OneDrive** product -- agent-logger's filesystem sync
# target (``OneDriveTarget`` / ``resolve_onedrive_root`` / the ``onedrive``
# target name / the ``OneDrive*`` env vars / ``~/OneDrive``) legitimately names
# the consumer OneDrive folder, which the bare ``onedrive`` denylist substring
# cannot distinguish from the internal ``onedrive`` ADO org. The org form is
# scrubbed everywhere (``onedrive.visualstudio.com`` etc.), so within these
# paths ``onedrive`` is always the product. Prefixes are matched case-
# insensitively against the repo-relative path.
ALLOW: dict[str, tuple[str, ...]] = {
    "onedrive": ("plugins/agent-logger/", "readme.md"),
}


def _allowed(ident: str, rel: str) -> bool:
    """True when *ident* is an allowlisted product/generic term in *rel*."""
    prefixes = ALLOW.get(ident.lower())
    if not prefixes:
        return False
    low = rel.lower()
    return any(low.startswith(p) for p in prefixes)


def _load_identifiers() -> list[str]:
    ids: list[str] = []
    env = os.environ.get("COPILOT_EXTENSIONS_FORBIDDEN_IDS", "")
    ids += [s for s in (part.strip() for part in env.split(",")) if s]
    try:
        for raw in HOME_LIST.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                ids.append(line)
    except OSError:
        pass
    # De-dupe, drop empties, lowercase for case-insensitive matching.
    seen: dict[str, None] = {}
    for i in ids:
        low = i.lower()
        if low:
            seen.setdefault(low, None)
    return list(seen)


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    )
    return [line for line in out.stdout.splitlines() if line]


def main() -> int:
    identifiers = _load_identifiers()
    if not identifiers:
        print(
            "no forbidden identifiers configured "
            "(set COPILOT_EXTENSIONS_FORBIDDEN_IDS or write "
            "~/.agent-codespaces/forbidden-identifiers.txt) -- skipping.",
        )
        return 0

    violations: list[str] = []
    for rel in _tracked_files():
        if rel in SELF:
            continue
        path = REPO / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # binary or unreadable -- skip
        lower = text.lower()
        if not any(ident in lower for ident in identifiers
                   if not _allowed(ident, rel)):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            ll = line.lower()
            for ident in identifiers:
                if ident in ll and not _allowed(ident, rel):
                    violations.append(f"{rel}:{lineno}: forbidden identifier "
                                      f"'{ident}'")

    if violations:
        print("Internal-identifier guard FAILED -- remove these before pushing:")
        for v in violations:
            print(f"  {v}")
        print(f"\n{len(violations)} occurrence(s) across the tree.")
        return 1

    print(f"Internal-identifier guard OK ({len(identifiers)} identifier(s) checked).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
