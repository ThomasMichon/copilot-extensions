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

**Publication modes check new blob objects, not every changed path.** A blob
already reachable from the supplied base or published tips is not introduced
again by a rename, copy, merge or rewritten commit. Unpublished local branches
and merely present loose objects are not publication baselines. ``--published``
adds an actual already-published tip, including the old PR tip on a rebase.

New objects remain reachable through their introducing commits even if later
deleted or shrunk in the same push, so those introductions are still checked.
Object IDs are enumerated without names; NUL-delimited commit diffs recover
their paths without Git's quoting/locale ambiguities. Each new blob is checked
once, using an offending path if it has multiple names in the incoming history.
``--all`` remains an explicit full-tree audit, not an introduction-scoped mode.

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
    check-large-files.py --base <ref> [--head <ref>] [--published <old-published-tip>]
    check-large-files.py --all [--head <ref>] # full-tree sweep at one revision (CI guards-full-sweep;
                                               # default --head: HEAD)

Exit code 0 = nothing over cap (or nothing to check), 1 = a checked file
violates a cap or is an always-blocked extension.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_HEADLESS = getattr(subprocess, "CREATE_NO_WINDOW", 0)

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


class GitEnumerationError(RuntimeError):
    """Unavailable publication history or failed/malformed Git metadata."""


#: Environment variables that redirect git's own notion of "which repo/
#: index/objects am I operating on" -- an inherited value from a DIFFERENT
#: checkout's environment (e.g. a parent process that already set GIT_DIR
#: for its own repo) would silently override this script's own explicit
#: ``git -C REPO``, making ``--all`` inspect a different repository
#: entirely and still report a clean pass. Modeled on
#: ``plugins/agent-worktrees/src/agent_worktrees/git_ops.py``'s own
#: ``_REPOSITORY_CONTEXT_ENV``. Deliberately EXCLUDES ``GIT_INDEX_FILE``:
#: git's own ``pre-commit`` hook protocol sets it to the real index being
#: committed (relevant for e.g. a partial/``git commit -p`` commit), and
#: stripping it would make this check silently look at the WRONG index.
_REPOSITORY_CONTEXT_ENV = frozenset({
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CEILING_DIRECTORIES", "GIT_COMMON_DIR",
    "GIT_CONFIG", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_DIR",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_GRAFT_FILE", "GIT_IMPLICIT_WORK_TREE",
    "GIT_INTERNAL_SUPER_PREFIX", "GIT_NAMESPACE", "GIT_NO_REPLACE_OBJECTS",
    "GIT_OBJECT_DIRECTORY", "GIT_PREFIX", "GIT_QUARANTINE_PATH", "GIT_REPLACE_REF_BASE",
    "GIT_SHALLOW_FILE", "GIT_WORK_TREE",
})


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    for name in list(env):
        upper = name.upper()
        if (
            upper in _REPOSITORY_CONTEXT_ENV
            or upper.startswith("GIT_CONFIG_KEY_")
            or upper.startswith("GIT_CONFIG_VALUE_")
        ):
            env.pop(name, None)
    return env


def _git_bytes(*args: str, input: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(REPO), *args],
        input=input, capture_output=True, check=False, env=_git_env(), creationflags=_HEADLESS,
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


def _git_or_raise(*args: str) -> str:
    """Like ``_git``, but raises :class:`GitEnumerationError` (naming the
    command and git's own stderr) instead of silently returning whatever
    partial/empty output a failed invocation produced.
    """
    r = _git_bytes(*args)
    if r.returncode != 0:
        raise GitEnumerationError(
            f"'git {' '.join(args)}' failed: {_decode(r.stderr).strip()}"
        )
    return _decode(r.stdout)


def _rev_parse(ref: str) -> str | None:
    r = _git_bytes("rev-parse", "--verify", "--quiet", "--end-of-options", ref)
    if r.returncode:
        return None
    out = _decode(r.stdout).strip()
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", out) is None:
        raise GitEnumerationError("git returned a malformed publication object id")
    return out


def _publication_commits(refs: list[str]) -> dict[str, str | None]:
    requested = list(dict.fromkeys(refs))
    if any(not ref or any(character.isspace() or character == "\0" for character in ref) for ref in requested):
        raise GitEnumerationError("publication references contain invalid batch input")
    pinned = [ref for ref in requested if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", ref)]
    expressions = [(ref, ref) for ref in pinned] + [(ref + "^{commit}", ref) for ref in requested]
    response = _git_bytes(
        "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize) %(rest)",
        input="".join(expression + " " + ref + "\n" for expression, ref in expressions).encode("utf-8"),
    )
    if response.returncode:
        raise GitEnumerationError("Git could not resolve publication commit references")
    lines = _decode(response.stdout).splitlines()
    if len(lines) != len(expressions):
        raise GitEnumerationError("publication commit batch response is incomplete")
    metadata: dict[str, tuple[str, str] | None] = {}
    for (expression, ref), line in zip(expressions, lines):
        if line == expression + " missing":
            metadata[expression] = None
            continue
        fields = line.split(maxsplit=3)
        if (
            len(fields) != 4 or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", fields[0]) is None
            or fields[1] not in ("commit", "tag", "tree", "blob")
            or not fields[2].isdigit() or fields[3] != ref
            or expression == ref and fields[0] != ref
            or expression != ref and fields[1] != "commit"
        ):
            raise GitEnumerationError("publication commit batch response is malformed")
        metadata[expression] = (fields[0], fields[1])
    resolved = {}
    for ref in requested:
        peeled = metadata[ref + "^{commit}"]
        if ref in pinned and peeled is not None:
            original = metadata[ref]
            # A tag's object identity differs legitimately from its commit.
            if (
                original is None or original[1] not in ("commit", "tag")
                or original[1] == "commit" and original[0] != peeled[0]
            ):
                raise GitEnumerationError("publication object identity disagrees with commit peeling")
        resolved[ref] = peeled[0] if peeled is not None else None
    return resolved


def _ext(path: str) -> str:
    return Path(path).suffix.lower()


def _object_metadata(shas: list[str]) -> dict[str, tuple[str, int]]:
    requested = list(dict.fromkeys(shas))
    if not requested:
        return {}
    if any(re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", sha) is None for sha in requested):
        raise GitEnumerationError("git reported an invalid blob object id")
    result = _git_bytes(
        "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)",
        input=("\n".join(requested) + "\n").encode("ascii"),
    )
    if result.returncode:
        raise GitEnumerationError(
            f"'git cat-file --batch-check' failed: {_decode(result.stderr).strip()}"
        )
    lines = _decode(result.stdout).splitlines()
    if len(lines) != len(requested):
        raise GitEnumerationError("git blob-size batch response is incomplete")
    metadata = {}
    for sha, line in zip(requested, lines):
        fields = line.split()
        if (
            len(fields) != 3 or fields[0] != sha or fields[1] not in {"blob", "tree", "commit", "tag"}
            or not fields[2].isascii() or not fields[2].isdigit()
        ):
            raise GitEnumerationError("git blob-size batch response is missing or malformed")
        try:
            metadata[sha] = (fields[1], int(fields[2]))
        except ValueError as exc:
            raise GitEnumerationError("git blob-size batch response contains an invalid size") from exc
    return metadata


def _blob_sizes(shas: list[str]) -> dict[str, int]:
    metadata = _object_metadata(shas)
    if any(kind != "blob" for kind, _size in metadata.values()):
        raise GitEnumerationError("git reported a non-blob for a staged file")
    return {sha: size for sha, (_kind, size) in metadata.items()}


#: Blob-ish modes diff-tree's --raw output ever reports as a FILE entry.
#: 160000 (a submodule gitlink, pointing at a commit in another repo, not a
#: blob in this one) and 040000 (a tree -- shouldn't appear with -r, listed
#: defensively) are deliberately excluded: `git cat-file -s` on a gitlink's
#: "sha" fails because it names a commit, not a blob, which is a legitimate
#: skip, never a guard failure, and must not be conflated with one.
_REGULAR_FILE_MODES = {"100644", "100755", "120000"}


def _blob_candidates(paths: dict[str, set[str]], sizes: dict[str, int]) -> list[tuple[str, int]]:
    result = []
    for sha, size in sizes.items():
        names = sorted(paths[sha])
        selected = next((name for name in names if violation_for(name, size)), names[0])
        result.append((selected, size))
    return result


def _new_blob_paths(commits: list[str], sizes: dict[str, int]) -> list[tuple[str, int]]:
    paths: dict[str, set[str]] = {}
    if commits:
        if any(re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit) is None for commit in commits):
            raise GitEnumerationError("git reported an invalid introducing commit id")
        result = _git_bytes(
            "diff-tree", "--stdin", "--root", "-m", "--no-commit-id", "--raw", "--no-abbrev",
            "--no-renames", "-r", "--diff-filter=d", "-z",
            input=("\n".join(commits) + "\n").encode("ascii"),
        )
        if result.returncode:
            raise GitEnumerationError(
                f"'git diff-tree --stdin' failed: {_decode(result.stderr).strip()}"
            )
        raw = _decode(result.stdout)
        parts = raw.split("\0")
        for index in range(0, len(parts) - 1, 2):
            meta, path = parts[index], parts[index + 1]
            fields = meta.lstrip(":\n").split()
            if len(fields) != 5:
                raise GitEnumerationError("git reported malformed new-blob path metadata")
            mode, sha = fields[1], fields[3]
            if mode in _REGULAR_FILE_MODES and sha in sizes:
                paths.setdefault(sha, set()).add(path)
    if set(paths) != set(sizes):
        raise GitEnumerationError("git did not report an introducing path for every new blob")
    return _blob_candidates(paths, sizes)


def _new_blobs_in_range(
    base_ref: str, head_ref: str, published_refs: list[str] | None = None,
) -> list[tuple[str, int]]:
    """New blob objects relative to complete published histories, not merge-base paths."""
    baselines = ([base_ref] if base_ref not in ("0" * 40, "0" * 64) else []) + (published_refs or [])
    resolved = _publication_commits([head_ref, *baselines])
    head = resolved[head_ref]
    if head is None:
        raise GitEnumerationError("the selected publication head is unavailable")
    excluded = []
    if base_ref not in ("0" * 40, "0" * 64):
        base = resolved[base_ref]
        if base is None:
            raise GitEnumerationError("the publication base is unavailable; fetch it first")
        excluded.append(base)
    for ref in published_refs or []:
        published = resolved[ref]
        if published is None:
            raise GitEnumerationError("an explicitly published baseline is unavailable; fetch it first")
        excluded.append(published)
    excluded = list(dict.fromkeys(excluded))
    walk = ("rev-list", "--objects", "--no-object-names")
    if excluded:
        with ThreadPoolExecutor(max_workers=2) as workers:
            incoming_read = workers.submit(_git_or_raise, *walk, head, "--not", *excluded)
            known_read = workers.submit(_git_or_raise, *walk, *excluded)
            incoming, known = incoming_read.result().splitlines(), set(known_read.result().splitlines())
    else:
        incoming, known = _git_or_raise(*walk, head).splitlines(), set()
    # Negative commit traversal can still report known blobs after a rebase or
    # deletion/reuse. Novelty is the difference of complete reachability sets.
    objects = [sha for sha in incoming if sha not in known]
    metadata = _object_metadata(objects)
    sizes = {sha: size for sha, (kind, size) in metadata.items() if kind == "blob"}
    if not sizes:
        return []
    commits = [sha for sha, (kind, _size) in metadata.items() if kind == "commit"]
    return _new_blob_paths(commits, sizes)


def _blobs_in_tree(rev: str) -> list[tuple[str, int]]:
    """Every ``(path, size)`` tracked in the tree at ``rev`` -- the path
    inventory and the size both come from the SAME snapshot (``ls-tree``),
    unlike pairing ``ls-files`` (always the current index) with a separate
    per-path lookup at an arbitrary ``rev``, which can drift out of sync
    with each other. Raises :class:`GitEnumerationError` if ``rev`` doesn't
    resolve -- ``ls-tree`` against an unknown ref prints nothing and exits
    nonzero, which must surface as a genuine failure, never a silent
    "nothing to check" pass.
    """
    r = _git_or_raise("ls-tree", "-r", "-l", "-z", rev)
    out: list[tuple[str, int]] = []
    for record in r.split("\0"):
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
    """New stage-0 blob objects, excluding complete HEAD reachability.

    Paths resolve through literal NUL-delimited ls-files entries, not Git's
    colon revision or magic pathspec grammars. Working-tree growth/shrinkage
    cannot change the checked index object. Deletions, gitlinks and unresolved
    merge stages are not new stage-0 blobs; failures reading a selected object
    remain errors. Multiple paths to one new object are classified once.
    """
    if not paths:
        return []
    ls_files = subprocess.run(
        ["git", "--literal-pathspecs", "-C", str(REPO), "ls-files", "--stage", "-z", "--", *paths],
        capture_output=True, check=False, env=_git_env(), creationflags=_HEADLESS,
    )
    if ls_files.returncode != 0:
        raise GitEnumerationError(
            f"'git ls-files --stage' failed: {_decode(ls_files.stderr).strip()}"
        )
    shas: dict[str, str] = {}
    for record in _decode(ls_files.stdout).split("\0"):
        if not record:
            continue
        # "<mode> <sha> <stage>\t<path>"
        meta, _, path = record.partition("\t")
        fields = meta.split()
        if len(fields) < 3:
            continue
        mode, sha, stage = fields[0], fields[1], fields[2]
        if mode not in _REGULAR_FILE_MODES or stage != "0":
            continue  # e.g. a submodule gitlink, or an unmerged conflict stage -- deliberate skip
        shas[path] = sha
    # Iterate the entries ls-files itself already resolved and normalized
    # (not the caller's original path strings): a caller-supplied path like
    # "./src/big.json" round-trips through ls-files as "src/big.json", so
    # matching back against the ORIGINAL spelling would silently find
    # nothing and skip a real, staged, oversized file.
    head = _rev_parse("HEAD")
    known = set(_git_or_raise("rev-list", "--objects", "--no-object-names", head).splitlines()) if head else set()
    shas = {path: sha for path, sha in shas.items() if sha not in known}
    sizes = _blob_sizes(list(shas.values()))
    names: dict[str, set[str]] = {}
    for path, sha in shas.items():
        names.setdefault(sha, set()).add(path)
    return _blob_candidates(names, sizes)


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


def main(argv: list[str] | None = None) -> int:
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
        "--published", action="append", default=[], metavar="REF",
        help="Additional already-published tip whose reachable objects are not new (repeatable).",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Full-tree sweep at --head, ignoring --base/FILE.",
    )
    args = parser.parse_args(argv)

    try:
        if args.all:
            blobs = _blobs_in_tree(args.head)
            scope_desc = f"every tracked file at {args.head}"
        elif args.paths:
            blobs = _staged_blobs(args.paths)
            scope_desc = f"{len(blobs)} new staged blob(s)"
        else:
            blobs = _new_blobs_in_range(args.base, args.head, args.published)
            scope_desc = f"this diff's {len(blobs)} newly-introduced blob(s)"
    except GitEnumerationError as exc:
        # A git plumbing command needed to enumerate blobs failed outright
        # (e.g. an unresolvable --head, a corrupt range) -- a hard failure,
        # never a silent "nothing to check" pass: an unexamined commit or
        # path could easily be the one carrying the oversized/disallowed
        # blob this guard exists to catch.
        print(f"[FAIL] check-large-files: {exc}")
        return 1

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
