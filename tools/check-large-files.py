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

**Diff mode checks every commit in the range individually, not just the net
difference between the range's endpoints.** A naive tree-to-tree diff would
miss a commit that adds an oversized/disallowed blob and a later commit in
the *same push* that deletes or shrinks it -- the oversized blob is still
permanently in the pushed history at that point, which is exactly the
accumulation this guard exists to prevent (it is, verbatim, how the
coverage-baseline bloat this guard follows up on actually happened: each
promotion both added a new oversized baseline snapshot and left the
previous one in history). For each commit newly reachable in the range,
this diffs it against its own first parent (``git diff-tree``) and checks
every path THAT commit touches at its own blob size -- not an object-level
"is this blob content new to the whole repository" scan
(``git rev-list --objects``), which would silently miss a rename/type-change
whose content happens to be byte-identical to something already elsewhere
in history (the content isn't new, even though the path is).

Like ``check-module-size.py`` and ``check-effort-vision-structure.py``, this
only checks the range's own commits -- nothing already on ``origin/dev``
before this diff started ever blocks an unrelated change.

Every size check reads the actual git blob -- the index for explicit staged
paths, the object store for diff/full-tree modes -- never the working
tree. This makes the guard immune to a working-tree/git mismatch (a staged
oversized file whose working-tree copy is later shrunk or deleted without
re-staging would otherwise slip through; conversely an unrelated
working-tree edit could wrongly flag a safely-sized staged blob). All git
output is read NUL-delimited (``-z``) and decoded explicitly as UTF-8
(never the ambient locale encoding, which can silently mis-decode a
non-ASCII path on a non-UTF-8-locale platform) -- never the default quoted/
locale-decoded form, so a filename with non-ASCII characters, a tab, or a
newline can't be silently misread as missing and skipped.

Usage::

    check-large-files.py -- FILE [FILE ...]  # check exactly these paths against the INDEX (pre-commit, staged)
                                              # the "--" is required so a staged file literally named
                                              # "--all" (or any other flag-shaped name) is never parsed
                                              # as an option instead of a path.
    check-large-files.py                     # every blob new in HEAD vs --base (default origin/dev)
    check-large-files.py --base <ref> [--head <ref>]
    check-large-files.py --all [--head <ref>] # full-tree sweep at one revision (CI guards-full-sweep;
                                               # default --head: HEAD)

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


def _git_bytes(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(REPO), *args],
        capture_output=True, check=False,
    )


def _decode(raw: bytes) -> str:
    """Decode git output explicitly as UTF-8 (git's own internal encoding
    for paths/refs), never the ambient locale/code-page encoding a bare
    ``text=True`` subprocess call would use -- the latter can silently
    mis-decode a non-ASCII path on a non-UTF-8-locale platform (notably
    Windows), making ``cat-file`` fail to resolve the (mis-decoded) path
    and the oversized/disallowed file underneath it go uninspected.
    ``surrogateescape`` preserves a non-UTF-8 byte sequence (a POSIX
    filename is not guaranteed to be valid UTF-8) round-trippably instead
    of raising or silently substituting it away.
    """
    return raw.decode("utf-8", errors="surrogateescape")


def _git(*args: str) -> str:
    return _decode(_git_bytes(*args).stdout)


def _rev_parse(ref: str) -> str | None:
    r = _git_bytes("rev-parse", "--verify", "--quiet", ref)
    out = _decode(r.stdout).strip()
    return out or None


def _merge_base(base: str, head: str) -> str | None:
    out = _git("merge-base", base, head).strip()
    return out or None


def _ext(path: str) -> str:
    return Path(path).suffix.lower()


#: The well-known SHA of an empty git tree -- used as the "parent" for a
#: root commit (one with no parent of its own) when diffing a single
#: commit against its predecessor.
EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _commits_in_range(mbase: str, head: str) -> list[str]:
    out = _git("rev-list", f"{mbase}..{head}")
    return [line for line in out.split("\n") if line]


def _commit_touched_blobs(commit: str) -> list[tuple[str, int]]:
    """``(path, size)`` for every path ``commit`` itself adds, modifies,
    renames, or type-changes relative to its own first parent (or the
    empty tree, for a root commit) -- i.e. this one commit's own diff, at
    its own post-commit blob size. Checking per-commit rather than only the
    whole range's net tree-to-tree difference is what makes a rename (same
    blob content, same object id, merely a new path) still inspected at its
    new path: ``git rev-list --objects`` considers such a blob *not new* to
    the repository (the same content is already reachable via its old
    path/commit), so it would otherwise never surface in an object-level
    scan -- see the module docstring's new-blobs rationale, which this
    function implements per-commit rather than for the whole range as one
    object-reachability query.
    """
    parent_r = _git_bytes("rev-parse", "--verify", "--quiet", f"{commit}^")
    parent = _decode(parent_r.stdout).strip() or EMPTY_TREE_SHA
    diff = _git("diff-tree", "--no-commit-id", "--name-only", "-r", "--diff-filter=d", parent, commit)
    out: list[tuple[str, int]] = []
    for path in diff.split("\n"):
        if not path:
            continue
        r = _git_bytes("cat-file", "-s", f"{commit}:{path}")
        if r.returncode != 0:
            continue  # shouldn't happen for a path diff-tree just reported, but be defensive
        try:
            size = int(_decode(r.stdout).strip())
        except ValueError:
            continue
        out.append((path, size))
    return out


def _new_blobs_in_range(base_ref: str, head_ref: str) -> list[tuple[str, int]] | None:
    """Every ``(path, size)`` touched by any commit newly reachable in
    ``<merge-base of base_ref and head_ref>..head_ref`` -- i.e. every file
    this diff's own commits add, modify, rename, or type-change, each
    checked at ITS OWN introducing commit's blob size. This includes a file
    added then deleted or shrunk again later in the same range (see the
    module docstring for why this must not be scoped to only the range's
    net tree-to-tree difference), and a rename/type-change whose content is
    byte-identical to something already elsewhere in history (merely
    re-pathed, so its blob object itself isn't "new" to the repository --
    see ``_commit_touched_blobs``). Returns None if ``base_ref``/
    ``head_ref`` can't be resolved (caller should skip the check in that
    case -- e.g. ``base_ref`` not fetched).
    """
    head = _rev_parse(head_ref)
    if head is None:
        print(f"check-large-files: cannot resolve head ({head_ref}); skipping.")
        return None
    base = _rev_parse(base_ref)
    if base is None:
        print(
            f"check-large-files: base '{base_ref}' unavailable; "
            "skipping (fetch it to enable the guard).",
        )
        return None
    mbase = _merge_base(base, head) or base
    out: list[tuple[str, int]] = []
    for commit in _commits_in_range(mbase, head):
        out.extend(_commit_touched_blobs(commit))
    return out


def _blobs_in_tree(rev: str) -> list[tuple[str, int]]:
    """Every ``(path, size)`` tracked in the tree at ``rev`` -- the path
    inventory and the size both come from the SAME snapshot (``ls-tree``),
    unlike pairing ``ls-files`` (always the current index) with a separate
    per-path lookup at an arbitrary ``rev``, which can drift out of sync
    with each other.
    """
    r = _git_bytes("ls-tree", "-r", "-l", "-z", rev)
    out: list[tuple[str, int]] = []
    for record in _decode(r.stdout).split("\0"):
        if not record:
            continue
        # "<mode> <type> <sha> <size>\t<path>"
        meta, _, path = record.partition("\t")
        if not path:
            continue
        fields = meta.split()
        if len(fields) < 4 or fields[1] != "blob":
            continue
        try:
            size = int(fields[3])
        except ValueError:
            continue
        out.append((path, size))
    return out


def _staged_blobs(paths: list[str]) -> list[tuple[str, int]]:
    """``(path, size)`` for each of ``paths`` as recorded in the INDEX
    (stage 0) -- never the working tree. A staged oversized blob whose
    working-tree copy is later shrunk or deleted without re-staging is
    still what would actually be committed, and must still be caught; the
    reverse (an unrelated working-tree edit growing a safely-sized staged
    blob) must not false-positive.
    """
    out: list[tuple[str, int]] = []
    for path in paths:
        r = _git_bytes("cat-file", "-s", f":{path}")
        if r.returncode != 0:
            continue  # not in the index (e.g. a staged deletion) -- nothing to check
        try:
            size = int(_decode(r.stdout).strip())
        except ValueError:
            continue
        out.append((path, size))
    return out


def violation_for(path: str, size: int) -> str | None:
    ext = _ext(path)
    if ext in ALWAYS_BLOCKED_EXTENSIONS:
        return (
            f"{path}: '{ext}' files are never checked in (generated/derived "
            "artifacts -- a source map or raw diff/patch belongs in a build "
            "output or a one-off local workflow, not git history)"
        )
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


def check(blobs: list[tuple[str, int]]) -> list[str]:
    """One violation message per distinct ``(path, size)`` pair that fails.

    Deliberately NOT deduped by path alone: the same path can legitimately
    appear at more than one size within a single diff-mode range (modified
    more than once, or added oversized then shrunk again later in the same
    push -- see the module docstring) and EVERY oversized occurrence must
    be reported, not just whichever one a path-level dedup happened to keep
    first.
    """
    violations: list[str] = []
    seen: set[tuple[str, int]] = set()
    for path, size in sorted(blobs):
        if (path, size) in seen:
            continue
        seen.add((path, size))
        msg = violation_for(path, size)
        if msg:
            violations.append(msg)
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths", nargs="*", metavar="FILE",
        help=(
            "Check exactly these paths, read from the index (pre-commit, "
            "staged files). Always pass '--' before the file list so a "
            "staged file whose name happens to look like a flag (e.g. "
            "'--all') is never parsed as one."
        ),
    )
    parser.add_argument(
        "--base", default="origin/dev", metavar="REF",
        help="Diff base when no explicit paths are given (default: origin/dev).",
    )
    parser.add_argument(
        "--head", default="HEAD", metavar="REF",
        help="Diff/sweep head (default: HEAD).",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Full-tree sweep at --head, ignoring --base/FILE.",
    )
    args = parser.parse_args()

    if args.all:
        blobs = _blobs_in_tree(args.head)
        scope_desc = f"every tracked file at {args.head}"
    elif args.paths:
        blobs = _staged_blobs(args.paths)
        scope_desc = f"{len(args.paths)} staged file(s)"
    else:
        found = _new_blobs_in_range(args.base, args.head)
        if found is None:
            return 0
        blobs = found
        scope_desc = f"this diff's {len(blobs)} newly-introduced blob(s)"

    violations = check(blobs)
    if violations:
        print("[FAIL] check-large-files:")
        for v in violations:
            print(f"  - {v}")
        return 1

    print(f"[OK] check-large-files: {scope_desc} within cap.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
