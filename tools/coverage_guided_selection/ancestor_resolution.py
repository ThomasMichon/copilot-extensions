"""Phase 2: nearest-ancestor baseline resolution + attribution remap/invalidate.

Realizes this effort's own Phase 2 (see `efforts/active/coverage-guided-ci`'s
Plan): given an arbitrary fork-point commit (typically a PR branch's own
merge-base against `dev`, which may sit behind `main`'s current baseline
generation, or behind several), resolve the **newest** coverage baseline
checked into `main` (see `correlation.py`) whose own `measured_commit` is an
ancestor of that fork point, per the vision's "baseline reachable from any
fork point" Feature -- then carry that baseline's line-level attribution
forward through every intervening commit's own diff, file by file:

- A file whose diff between the baseline's `measured_commit` and the fork
  point is **entirely pure insertions/deletions** (no hunk both removes and
  adds lines -- i.e. no hunk actually replaces/edits existing content) has
  its covered line numbers translated through the diff's own line-number
  shift, with one deliberate asymmetry: a line preceded by a **deletion**
  is safely remapped (a test that already reached it in the old code can't
  be retroactively un-reached by removing unrelated code elsewhere), but a
  line preceded by an **insertion** is treated the same as deleted-outright
  attribution -- dropped, not remapped -- because newly inserted code can
  introduce new control flow (an early `return`, a new guard clause) that
  causes a previously-reaching test to no longer reach it, and hunk
  lengths alone can't prove an insertion was execution-neutral. A line
  that was itself deleted always drops out (correct: it no longer exists
  to be covered).
- A file with **any** hunk that both removes and adds lines genuinely
  changed content, so old line numbers inside it can't be trusted to still
  mean the same thing -- that file's attribution is **invalidated**
  (dropped from the resulting baseline's ``coverage`` map entirely), which
  `selection.select_tests` already treats as ``no_baseline_entry`` --
  forcing the smoke/coverage-debt fallback for that file specifically,
  never a silently stale selection.

This module only reads git history (``git log``, ``git show``, ``git diff``,
``git merge-base``) from an already-fetched local clone -- it performs no
network I/O of its own and never writes anything.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

#: Ambient Git repository-selection variables that must never leak into a
#: subprocess here -- if the calling environment has e.g. `GIT_DIR` or
#: `GIT_WORK_TREE` set (from a wrapping script, another git operation in
#: progress, or a test harness), it silently overrides our own explicit
#: `cwd`, so history/diff/merge-base could come from an entirely different
#: repository than the one we were asked about. Mirrors
#: `agent_worktrees.git_ops._REPOSITORY_CONTEXT_ENV` (this module
#: deliberately doesn't import that plugin-internal module -- this package
#: is dependency-free by design, deployable in any plugin's own ephemeral
#: venv -- so the list is kept in sync by hand, not by import).
_REPOSITORY_CONTEXT_ENV = frozenset({
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CEILING_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_CONFIG",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_PARAMETERS",
    "GIT_DIR",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_GRAFT_FILE",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_INTERNAL_SUPER_PREFIX",
    "GIT_NAMESPACE",
    "GIT_NO_REPLACE_OBJECTS",
    "GIT_OBJECT_DIRECTORY",
    "GIT_PREFIX",
    "GIT_QUARANTINE_PATH",
    "GIT_REPLACE_REF_BASE",
    "GIT_SHALLOW_FILE",
    "GIT_WORK_TREE",
})


def scrubbed_git_env() -> dict[str, str]:
    """Ambient environment with every repository-selection variable removed.

    A caller supplies its target repository explicitly via `cwd=`/`-C` --
    any of these inherited variables would override that silently.
    """
    env = os.environ.copy()
    for name in list(env):
        upper = name.upper()
        if (
            upper in _REPOSITORY_CONTEXT_ENV
            or upper.startswith("GIT_CONFIG_KEY_")
            or upper.startswith("GIT_CONFIG_VALUE_")
        ):
            env.pop(name, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


class AncestorResolutionError(RuntimeError):
    """Raised when git plumbing needed for resolution fails unexpectedly."""


def _git(args: list[str], *, cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False,
        env=scrubbed_git_env(),
    )
    if proc.returncode != 0:
        raise AncestorResolutionError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): "
            f"{proc.stderr.strip()}"
        )
    return proc.stdout


def is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    """True if `ancestor` is an ancestor of (or equal to) `descendant`.

    `git merge-base --is-ancestor` exits 0 for yes, 1 for no, and anything
    else (128, a missing/unreachable commit) is a genuine plumbing failure
    this surfaces loudly rather than silently treating as "no".
    """
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo_root, capture_output=True, text=True, check=False,
        env=scrubbed_git_env(),
    )
    if proc.returncode not in (0, 1):
        raise AncestorResolutionError(
            "git merge-base --is-ancestor failed unexpectedly "
            f"(exit {proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.returncode == 0


@dataclass(frozen=True)
class ResolvedBaseline:
    baseline: dict
    #: the `main`-branch commit the baseline JSON was actually read from --
    #: distinct from `baseline["measured_commit"]`, which is the `dev`
    #: commit coverage was collected against.
    baseline_commit: str


def resolve_nearest_baseline(
    repo_root: Path,
    plugin: str,
    fork_commit: str,
    *,
    main_ref: str = "main",
) -> ResolvedBaseline | None:
    """Walk `main_ref`'s own history of the plugin's baseline file for the
    newest generation whose embedded `measured_commit` is an ancestor of
    `fork_commit`.

    Returns `None` if no generation qualifies -- the baseline file was
    never checked in on this branch, or every generation was measured on a
    commit that isn't actually an ancestor of `fork_commit` (a fork point
    older than this plugin's very first enrolled baseline). Either case is
    the "no attribution available" signal the vision's own smoke-fallback
    Concept exists to catch -- this function raises for a genuine git
    plumbing failure, never for "nothing found".
    """
    from . import correlation

    path = correlation.baseline_path_on_main(plugin)
    # --diff-filter=d excludes revisions where this commit's own change to
    # `path` was a deletion -- the file genuinely doesn't exist at such a
    # revision, which is a normal, expected case (not a plumbing failure)
    # that would otherwise make `git show <rev>:<path>` fail for a reason
    # indistinguishable from a real corruption/plumbing error below.
    log_output = _git(
        ["log", "--format=%H", "--diff-filter=d", main_ref, "--", path],
        cwd=repo_root,
    )
    revisions = [line.strip() for line in log_output.splitlines() if line.strip()]
    for rev in revisions:
        # Every logged revision is guaranteed (by --diff-filter=d above) to
        # have `path` present, so a `git show` failure here is a genuine
        # plumbing problem (object corruption, a GC'd/unreachable commit)
        # and must propagate as AncestorResolutionError, never be silently
        # swallowed into "this generation doesn't qualify, keep looking."
        content = _git(["show", f"{rev}:{path}"], cwd=repo_root)
        try:
            candidate = json.loads(content)
        except json.JSONDecodeError:
            continue
        # A malformed-but-valid-JSON document (not an object at all, or a
        # measured_commit that isn't a plain non-empty string) must be
        # skipped exactly like a JSON-decode failure -- continuing to an
        # older generation -- never crash here. `.get()` on a non-dict
        # candidate raises AttributeError; a non-string measured_commit
        # (a number, a list, ...) reaches `is_ancestor`'s own
        # `subprocess.run` call below and raises TypeError there instead,
        # either of which would otherwise propagate past this function's
        # own documented "raises only for a genuine plumbing failure"
        # contract and past `decide()`'s own "never raises for an
        # untrusted baseline" contract in turn.
        if not isinstance(candidate, dict):
            continue
        measured_commit = candidate.get("measured_commit")
        if not isinstance(measured_commit, str) or not measured_commit:
            continue
        if is_ancestor(repo_root, measured_commit, fork_commit):
            return ResolvedBaseline(baseline=candidate, baseline_commit=rev)
    return None


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

#: (old_start, old_len, new_start, new_len) per hunk.
Hunk = tuple[int, int, int, int]


def _parse_hunks(diff_text: str) -> list[Hunk]:
    hunks: list[Hunk] = []
    for line in diff_text.splitlines():
        m = _HUNK_RE.match(line)
        if not m:
            continue
        old_start = int(m.group(1))
        old_len = int(m.group(2)) if m.group(2) is not None else 1
        new_start = int(m.group(3))
        new_len = int(m.group(4)) if m.group(4) is not None else 1
        hunks.append((old_start, old_len, new_start, new_len))
    return hunks


@dataclass(frozen=True)
class FileRemapResult:
    #: "unchanged" (file untouched between the two commits -- old line
    #: numbers are still valid as-is), "remapped" (every hunk was a pure
    #: insertion/deletion -- `hunks` translates old line numbers), or
    #: "invalid" (at least one hunk replaced/edited existing content).
    status: str
    hunks: tuple[Hunk, ...] = field(default_factory=tuple)


def compute_file_remap(
    repo_root: Path, file_path: str, old_commit: str, new_commit: str,
) -> FileRemapResult:
    """Classify a single file's diff between two commits.

    Uses `--unified=0` so every hunk boundary is exact (no shared context
    lines padding a hunk's old/new ranges), which is what makes "a hunk with
    both a nonzero old_len and a nonzero new_len genuinely replaced content"
    a reliable signal -- with default context lines, an insertion right next
    to an unrelated unchanged line could otherwise appear to "replace" that
    context line.

    `--no-ext-diff --no-textconv` disable `GIT_EXTERNAL_DIFF` and any
    configured `diff.*.textconv` driver: either could otherwise transform
    or replace this machine-readable output in a way `_parse_hunks` can't
    recognize as unified-diff syntax, silently reporting "unchanged"/
    "remapped" instead of the real edit -- stale attribution carried
    forward as if it were still accurate.
    """
    diff_text = _git(
        ["diff", "--unified=0", "--no-color", "--no-ext-diff", "--no-textconv",
         old_commit, new_commit, "--", file_path],
        cwd=repo_root,
    )
    if not diff_text.strip():
        return FileRemapResult(status="unchanged")

    hunks = _parse_hunks(diff_text)
    if not hunks:
        # A nonempty diff with no parsed `@@` hunks at all -- e.g. a binary
        # file ("Binary files ... differ", no textual hunks) or any other
        # unified-diff shape this parser doesn't recognize. Treating this
        # the same as "unchanged" would silently carry every old
        # attribution forward across a real, unparsed change -- invalidate
        # instead, exactly as if a hunk had replaced content.
        return FileRemapResult(status="invalid")
    for old_start, old_len, new_start, new_len in hunks:
        if old_len > 0 and new_len > 0:
            return FileRemapResult(status="invalid")
    return FileRemapResult(status="remapped", hunks=tuple(hunks))


def remap_line(old_line: int, hunks: tuple[Hunk, ...]) -> int | None:
    """Translate `old_line` through a file's own pure insert/delete hunks.

    Returns `None` ("no longer safely attributable") when:

    - `old_line` fell inside a deleted range (the line no longer exists at
      the new commit -- correctly drops out of attribution rather than
      erroring), or
    - **any insertion hunk precedes `old_line`.** A pure line-coordinate
      shift proves the *position* of unrelated content moved, but it
      proves nothing about *execution*: newly inserted code can introduce
      new control flow -- an early `return`/`raise`/`continue`/`break`, a
      new guard clause -- that causes a test which genuinely reached
      `old_line` at the baseline commit to no longer reach it at its
      cleanly-translated new position, even though the line number itself
      maps perfectly. Hunk lengths alone cannot prove an insertion was
      execution-neutral, so any preceding insertion conservatively
      invalidates that line's own attribution rather than silently
      carrying forward evidence that might now be stale -- the vision's
      own "never quieter than the evidence supports" Behavior, applied to
      a risk line-arithmetic alone can't rule out.

    A preceding pure **deletion** is, by contrast, safe to translate
    through without invalidating: whatever test attributed `old_line` at
    the baseline commit, by definition, already executed all the way down
    to it in the OLD code -- removing *other*, unrelated code elsewhere in
    the file cannot retroactively make that same test stop reaching a line
    it already demonstrably reached. Only a newly inserted control-flow
    statement can introduce a *new* skip; a deletion can only ever widen
    what's reachable, never narrow what a test already proved it reaches
    (data-flow-only effects, e.g. a deleted precondition that changes a
    later branch's outcome, are an inherent limitation of line-based
    attribution shared by every diff-scoped test-selection tool, not
    something this function claims to rule out).
    """
    offset = 0
    for old_start, old_len, new_start, new_len in hunks:
        if old_len == 0:
            # Pure insertion: `new_len` new lines appear right after old
            # line `old_start` (git reports the insertion point as the old
            # line immediately preceding it, with a 0-length old range).
            if old_line >= old_start + 1:
                return None  # a preceding insertion -- not provably safe
            # Insertion strictly after old_line: no effect on it at all.
        else:
            if old_line < old_start:
                continue
            if old_line >= old_start + old_len:
                # Pure deletion: old_len lines removed, new_len (0) added.
                offset += new_len - old_len
            else:
                return None  # old_line itself was deleted
    return old_line + offset


def remap_or_invalidate_baseline(
    repo_root: Path, resolved: ResolvedBaseline, fork_commit: str,
) -> dict:
    """Carry a resolved baseline's attribution forward to `fork_commit`.

    Returns a new baseline dict (the input is never mutated) whose
    ``coverage`` map reflects `fork_commit`'s own line numbers: unchanged
    files pass through as-is, cleanly-shiftable files have their covered
    line numbers translated, and files with real content changes are
    dropped entirely -- which `selection.select_tests` already treats as
    ``no_baseline_entry``, forcing that file's own smoke/coverage-debt
    fallback rather than a silently stale selection.

    The original `baseline["measured_commit"]` is preserved verbatim
    (provenance: what coverage.py actually measured); the new
    `"remapped_to_commit"` field records the fork point this remap carried
    attribution forward to, and `"remap_invalidated_files"` lists every
    file this remap had to drop, for a caller that wants to report why a
    selection fell back for a given file without re-deriving it.
    """
    baseline = resolved.baseline
    old_commit = baseline["measured_commit"]
    new_coverage: dict[str, dict[str, list[str]]] = {}
    invalidated: list[str] = []

    for file, per_line in baseline.get("coverage", {}).items():
        result = compute_file_remap(repo_root, file, old_commit, fork_commit)
        if result.status == "unchanged":
            new_coverage[file] = per_line
            continue
        if result.status == "invalid":
            invalidated.append(file)
            continue
        remapped: dict[str, list[str]] = {}
        for line_str, tests in per_line.items():
            new_line = remap_line(int(line_str), result.hunks)
            if new_line is None:
                continue
            bucket = remapped.setdefault(str(new_line), [])
            for t in tests:
                if t not in bucket:
                    bucket.append(t)
        if remapped:
            new_coverage[file] = remapped
        else:
            # Every covered line in this file ended up with no safely
            # attributable new line -- each was either deleted outright or
            # (per `remap_line`'s own asymmetric policy) preceded by an
            # insertion. Either way there's nothing left to select from,
            # so this is equivalent to invalidation; `invalidated` doesn't
            # distinguish the reason per file (it's a flat filename list),
            # only `remap_line`/`compute_file_remap`'s own return values
            # carry that detail for a caller that needs it.
            invalidated.append(file)

    out = dict(baseline)
    out["coverage"] = new_coverage
    out["remapped_to_commit"] = fork_commit
    out["remap_invalidated_files"] = sorted(invalidated)
    return out
