#!/usr/bin/env python3
"""Lint touched effort and vision README.md files for the canonical structure.

Ask 6 of the `copilot-extensions-public-work-migration` effort: this repo's
pre-commit/pre-push hooks did not enforce the `efforts:planning-efforts` and
`visions:envisioning` section-set contracts documented in `efforts/README.md`
§ Local conventions and `visions/README.md` § Local conventions. This guard
gives that documented shape a hard PASS/FAIL instead of a field belief.

Scope: any `README.md` under `efforts/` or `visions/` that carries the
schema's own frontmatter-style metadata bullet (`- **Slug:**` for an effort,
`- **Subject:**` for a vision) is treated as a real effort/vision document and
checked for its required section headings. Index pages (`efforts/README.md`,
`visions/README.md`) and the `TEMPLATE.md` files carry no such bullet and are
skipped automatically -- no hardcoded path exclusion list to maintain.

Like `check-version-bump.py`, this only checks files that actually changed in
the diff being validated (staged files for pre-commit; the push/PR range for
pre-push) -- a pre-existing malformed doc in a file you didn't touch never
blocks an unrelated change. Only editing a matching README, or newly adding
one, can trigger a finding.

Usage::

    check-effort-vision-structure.py FILE [FILE ...]   # check exactly these paths (pre-commit, staged)
    check-effort-vision-structure.py                   # diff HEAD vs --base (default origin/main)
    check-effort-vision-structure.py --base <ref>       # diff vs an explicit base
    check-effort-vision-structure.py --all              # check every effort/vision README in the repo

Exit code 0 = conformant (or nothing to check), 1 = a checked doc is malformed.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EFFORT_REQUIRED_HEADINGS = [
    "## Guiding Intent",
    "## Context",
    "## Request",
    "## Plan",
    "## Validation Plan",
    "## Journal",
]

VISION_REQUIRED_HEADINGS = [
    "## Purpose & Intent",
    "## Concepts & Components",
    "## Non-Goals / Boundaries",
    "## See Also",
]


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True, text=True, check=False,
    )


def _rev_parse(ref: str) -> str | None:
    r = _git("rev-parse", "--verify", "--quiet", ref)
    return r.stdout.strip() or None


def _merge_base(base: str, head: str) -> str | None:
    r = _git("merge-base", base, head)
    return r.stdout.strip() or None


def _changed_readmes(base_ref: str, head_ref: str) -> list[Path]:
    head = _rev_parse(head_ref)
    if head is None:
        print(f"check-effort-vision-structure: cannot resolve HEAD ({head_ref}); skipping.")
        return []
    base = _rev_parse(base_ref)
    if base is None:
        print(
            f"check-effort-vision-structure: base '{base_ref}' unavailable; "
            "skipping (fetch it to enable the guard).",
        )
        return []
    mbase = _merge_base(base, head) or base
    r = _git("diff", "--name-only", "--diff-filter=ACM", f"{mbase}..{head}")
    paths = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line or not line.endswith("README.md"):
            continue
        parts = line.split("/")
        if parts and parts[0] in ("efforts", "visions"):
            paths.append(ROOT / line)
    return paths


def _is_effort_doc(text: str) -> bool:
    return re.search(r"^- \*\*Slug:\*\*", text, re.MULTILINE) is not None


def _is_vision_doc(text: str) -> bool:
    return re.search(r"^- \*\*Subject:\*\*", text, re.MULTILINE) is not None


def _missing_headings(text: str, required: list[str]) -> list[str]:
    headings = set(re.findall(r"^(## .+?)\s*$", text, re.MULTILINE))
    return [h for h in required if h not in headings]


def check_file(path: Path) -> list[str]:
    """Return error strings for path; empty means it passed (or was skipped)."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    rel = path.resolve().relative_to(ROOT).as_posix()
    errors: list[str] = []

    if _is_effort_doc(text):
        missing = _missing_headings(text, EFFORT_REQUIRED_HEADINGS)
        if missing:
            errors.append(
                f"{rel}: effort doc is missing required section(s): {', '.join(missing)}"
            )
    elif _is_vision_doc(text):
        missing = _missing_headings(text, VISION_REQUIRED_HEADINGS)
        if missing:
            errors.append(
                f"{rel}: vision doc is missing required section(s): {', '.join(missing)}"
            )
    # Neither marker present: an index page or another non-schema README --
    # not our concern, skip silently.

    return errors


def _discover_all() -> list[Path]:
    found: list[Path] = []
    for base in ("efforts", "visions"):
        found.extend((ROOT / base).rglob("README.md"))
    return sorted(set(found))


def _resolve_explicit(argv: list[str]) -> list[Path]:
    candidates: list[Path] = []
    for arg in argv:
        p = Path(arg)
        if not p.is_absolute():
            p = ROOT / p
        if p.name != "README.md":
            continue
        try:
            rel = p.resolve().relative_to(ROOT)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] in ("efforts", "visions"):
            candidates.append(p.resolve())
    return candidates


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help="explicit README.md paths to check (pre-commit)")
    ap.add_argument("--base", default="origin/main", help="base ref to diff against (default: origin/main)")
    ap.add_argument("--head", default="HEAD", help="head ref (default: HEAD)")
    ap.add_argument("--all", action="store_true", help="check every effort/vision README in the repo")
    args = ap.parse_args(argv)

    if args.all:
        candidates = _discover_all()
    elif args.paths:
        candidates = _resolve_explicit(args.paths)
    else:
        candidates = _changed_readmes(args.base, args.head)

    all_errors: list[str] = []
    for path in candidates:
        all_errors.extend(check_file(path))

    if all_errors:
        print("check-effort-vision-structure: malformed effort/vision doc(s):", file=sys.stderr)
        for err in all_errors:
            print(f"  {err}", file=sys.stderr)
        print(
            "\n  Every effort README needs: " + ", ".join(EFFORT_REQUIRED_HEADINGS),
            file=sys.stderr,
        )
        print(
            "  Every vision README needs: " + ", ".join(VISION_REQUIRED_HEADINGS),
            file=sys.stderr,
        )
        print("  See efforts/TEMPLATE.md and visions/TEMPLATE.md.", file=sys.stderr)
        return 1

    if candidates:
        print(f"check-effort-vision-structure: {len(candidates)} doc(s) checked, all conformant.")
    else:
        print("check-effort-vision-structure: no effort/vision README changed; nothing to check.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
