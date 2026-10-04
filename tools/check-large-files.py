#!/usr/bin/env python3
"""Block check-in of oversized files, with a generous allowance for images.

Direct follow-up to the ``main-history-rewrite`` effort: ``main``'s history
had accumulated ~300MB across 160+ oversized ``.github/coverage-baselines/``
blobs (up to ~14MB each) before that design moved to GitHub Release assets
(see PRs #5078/#5085/#5097/#5098). That data dump never had a tooling guard
against it landing in git in the first place -- this check is that guard,
so the same mistake (or a different large generated artifact: a raw diff, a
source map, a vendored data dump) can't quietly recur.

**Two caps, chosen from a full sweep of the tracked corpus at the time this
guard was added** (see the ``main-history-rewrite`` effort's Journal for the
underlying numbers): the largest legitimate non-image file in the tree was
~440KB and the largest legitimate image was the README's own ~1.7MB GIF.
Both caps below sit comfortably above those with real headroom for organic
growth, while still catching a runaway blob (the old coverage-baseline
JSONs ranged 1MB-14MB) outright:

* **Images** (``IMAGE_EXTENSIONS``) -- a visual reference (a screenshot, a
  design preview, a demo GIF) is expected to be checked in; these get a
  generous :data:`IMAGE_CAP_BYTES`.
* **Everything else** gets the much tighter :data:`DEFAULT_CAP_BYTES` --
  source, docs, and test-fixture JSON are all comfortably under it today,
  so this is a real backstop against new bloat, not a description of the
  status quo.

**Always-blocked extensions** (``ALWAYS_BLOCKED_EXTENSIONS``) -- a source
map or a raw diff/patch file is a generated-or-derived artifact that never
belongs hand-committed, regardless of size (none currently exist in the
tree, so this has zero pre-existing debt to grandfather).

Like ``check-module-size.py`` and ``check-effort-vision-structure.py``, this
only checks files this diff actually adds or modifies (staged files for
pre-commit; the push/PR range for pre-push) -- a pre-existing large file you
didn't touch never blocks an unrelated change. ``--all`` additionally offers
a full-tree sweep for the unconditional CI guard job, so organic drift on
trunk is still caught even outside any single diff.

Usage::

    check-large-files.py FILE [FILE ...]   # check exactly these paths (pre-commit, staged)
    check-large-files.py                   # diff HEAD vs --base (default origin/dev)
    check-large-files.py --base <ref>       # diff vs an explicit base
    check-large-files.py --all              # full-tree sweep (CI guards-full-sweep)

Exit code 0 = nothing over cap (or nothing to check), 1 = a checked file
violates a cap or is an always-blocked extension.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: Generous cap for images -- comfortably above the largest legitimate image
#: currently tracked (~1.7MB), with real headroom for a new visual reference.
IMAGE_CAP_BYTES = 3 * 1024 * 1024  # 3 MiB

#: Tight cap for everything else -- comfortably above the largest legitimate
#: non-image file currently tracked (~440KB), but well below the old
#: coverage-baseline blobs (1MB-14MB) this guard exists to prevent recurring.
DEFAULT_CAP_BYTES = 1 * 1024 * 1024  # 1 MiB

IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp", ".avif",
}

#: Generated/derived artifacts that never belong hand-committed, regardless
#: of size: a source map and a raw diff/patch are both byproducts of a build
#: or a one-off local workflow, not source a reviewer should see in a PR.
ALWAYS_BLOCKED_EXTENSIONS = {".map", ".diff", ".patch"}


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(REPO), *args],
        capture_output=True, text=True, check=False,
    )


def _rev_parse(ref: str) -> str | None:
    r = _git("rev-parse", "--verify", "--quiet", ref)
    return r.stdout.strip() or None


def _merge_base(base: str, head: str) -> str | None:
    r = _git("merge-base", base, head)
    return r.stdout.strip() or None


def _changed_files(base_ref: str, head_ref: str = "HEAD") -> list[str]:
    """Files this branch's own commits add or modify, relative to its
    merge-base with ``base_ref`` -- unaffected by how far ``base_ref``'s own
    branch has since moved (triple-dot diff), so a PR is never blamed for a
    file it never touched.
    """
    head = _rev_parse(head_ref)
    if head is None:
        print(f"check-large-files: cannot resolve HEAD ({head_ref}); skipping.")
        return []
    base = _rev_parse(base_ref)
    if base is None:
        print(
            f"check-large-files: base '{base_ref}' unavailable; "
            "skipping (fetch it to enable the guard).",
        )
        return []
    mbase = _merge_base(base, head) or base
    r = _git("diff", "--name-only", "--diff-filter=ACM", f"{mbase}..{head}")
    return [line.strip() for line in r.stdout.splitlines() if line.strip()]


def _tracked_files() -> list[str]:
    r = _git("ls-files")
    return [line for line in r.stdout.splitlines() if line]


def _ext(path: str) -> str:
    return Path(path).suffix.lower()


def check_file(path: str) -> str | None:
    """Return a violation message for ``path``, or None if it passes (or was
    skipped because it no longer exists -- e.g. a deleted file in a diff)."""
    full = REPO / path
    if not full.is_file():
        return None
    ext = _ext(path)
    if ext in ALWAYS_BLOCKED_EXTENSIONS:
        return (
            f"{path}: '{ext}' files are never checked in (generated/derived "
            "artifacts -- a source map or raw diff/patch belongs in a build "
            "output or a one-off local workflow, not git history)"
        )
    size = full.stat().st_size
    cap = IMAGE_CAP_BYTES if ext in IMAGE_EXTENSIONS else DEFAULT_CAP_BYTES
    if size > cap:
        kind = "image" if ext in IMAGE_EXTENSIONS else "non-image"
        return (
            f"{path}: {size:,} bytes exceeds the {cap:,}-byte cap for {kind} "
            "files. A large generated artifact (a coverage/data dump, a "
            "bundled build output) doesn't belong in git history -- store it "
            "as a GitHub Release asset or CI artifact instead (see the "
            "main-history-rewrite effort for why this guard exists)."
        )
    return None


def check(paths: list[str]) -> list[str]:
    violations: list[str] = []
    for path in sorted(set(paths)):
        msg = check_file(path)
        if msg:
            violations.append(msg)
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths", nargs="*", metavar="FILE",
        help="Check exactly these paths (pre-commit, staged files).",
    )
    parser.add_argument(
        "--base", default="origin/dev", metavar="REF",
        help="Diff base when no explicit paths are given (default: origin/dev).",
    )
    parser.add_argument(
        "--head", default="HEAD", metavar="REF",
        help="Diff head when no explicit paths are given (default: HEAD).",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Full-tree sweep of every tracked file, ignoring --base/--head/FILE.",
    )
    args = parser.parse_args()

    if args.all:
        paths = _tracked_files()
        scope_desc = "every tracked file"
    elif args.paths:
        paths = args.paths
        scope_desc = f"{len(args.paths)} staged file(s)"
    else:
        paths = _changed_files(args.base, args.head)
        scope_desc = f"this diff's {len(paths)} changed file(s)"

    violations = check(paths)
    if violations:
        print("[FAIL] check-large-files:")
        for v in violations:
            print(f"  - {v}")
        return 1

    print(f"[OK] check-large-files: {scope_desc} within cap.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
