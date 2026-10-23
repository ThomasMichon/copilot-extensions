"""Resolve which ref finalize's content-on-upstream check should validate.

Split out of ``finalize.py`` (module-size guard) -- see ``resolve_finalize_ref``.
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from subprocess import CompletedProcess
from typing import Iterator

from . import git_ops, git_tree_diff, output

#: The identity of the probe commit :func:`squash_landed` builds (never a ref's).
_PROBE_IDENTITY = ("-c", "user.name=agent-worktrees", "-c", "user.email=agent-worktrees@localhost")
_report_history: ContextVar[
    dict[tuple[tuple[str, ...], str], CompletedProcess[str]] | None
] = ContextVar("finalize_report_history", default=None)


@contextmanager
def report_history_snapshot() -> Iterator[None]:
    """Share history reads within one read-only report, never a pruning decision."""
    token = _report_history.set({})
    try:
        yield
    finally:
        _report_history.reset(token)


def _history_read(*args: str, cwd: str) -> CompletedProcess[str]:
    snapshot = _report_history.get()
    key = args, cwd
    if snapshot is not None and key in snapshot:
        return snapshot[key]
    result = git_ops.git(*args, cwd=cwd, check=False)
    if snapshot is not None:
        snapshot[key] = result
    return result


@dataclass
class Landing:
    """Why a ref's content is (or isn't) on one upstream: each check's verdict,
    ``None`` when it couldn't be read or wasn't needed."""

    ref: str
    upstream: str
    ancestor: bool | None = None
    cherry: bool | None = None
    squash: bool | None = None
    blobs: bool | None = None

    @property
    def landed(self) -> bool:
        return any((self.ancestor, self.cherry, self.squash, self.blobs))

    @property
    def label(self) -> str:
        """The upstream as people write it (``origin/dev``); checks use the full ref."""
        return self.upstream.removeprefix("refs/remotes/")

    def to_dict(self) -> dict:
        return {"ref": self.ref, "upstream": self.label, "landed": self.landed,
                "ancestor": self.ancestor, "cherry": self.cherry,
                "squash": self.squash, "blobs": self.blobs}

    def describe(self) -> str:
        word = {True: "yes", False: "no", None: "-"}
        checks = ", ".join(f"{name} {word[getattr(self, name)]}"
                           for name in ("ancestor", "cherry", "squash", "blobs"))
        return f"{self.ref} on {self.label}: {checks}"


def squash_landed(branch: str, upstream: str, cwd: str) -> bool | None:
    """Whether *branch*'s whole change since its merge-base with *upstream*, as one
    patch, is already a commit on *upstream* -- a squash merge of a multi-commit
    branch, which ``git cherry`` on the branch's own commits can't see. ``None``
    when it can't be read, or when the branch makes no net change (the blob
    comparison decides that). Writes one unreferenced probe commit object (no ref
    moves; ``git gc`` collects it)."""
    base = git_ops.git("merge-base", upstream, branch, cwd=cwd, check=False)
    base_sha = base.stdout.strip()
    if base.returncode != 0 or not base_sha:
        return None
    trees = git_ops.git(
        "rev-parse", "--revs-only", "--end-of-options", f"{branch}^{{tree}}", f"{base_sha}^{{tree}}",
        cwd=cwd, check=False,
    )
    tree_ids = trees.stdout.splitlines()
    if trees.returncode != 0 or len(tree_ids) != 2 or not all(
        re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value) for value in tree_ids
    ):
        return None
    tree_sha, base_tree_sha = tree_ids
    if base_tree_sha == tree_sha:
        return None
    probe = git_ops.git(*_PROBE_IDENTITY, "commit-tree", "--no-gpg-sign", tree_sha,
                        "-p", base_sha, "-m", "agent-worktrees landing probe",
                        cwd=cwd, check=False)
    probe_sha = probe.stdout.strip()
    if probe.returncode != 0 or not probe_sha:
        return None
    cherry = git_ops.git("cherry", upstream, probe_sha, cwd=cwd, check=False)
    lines = [ln for ln in cherry.stdout.splitlines() if ln.strip()]
    if cherry.returncode != 0 or len(lines) != 1:
        return None
    return lines[0].startswith("-")


def creation_point(ref: str, cwd: str, *, published_bases: tuple[str, ...] = ()) -> str:
    """The commit local branch *ref* was created at, when it was created from a
    remote-tracking branch (its reflog's oldest entry, ``branch: Created from
    origin/main``) that is proven to have held that commit: everything reachable
    from it was already published when the branch was made, so it is never this
    branch's own work -- even after that remote rewrites its history. A later
    reset to a proven tip of that same source or a caller-qualified landing base
    on the same remote advances this published boundary
    (:func:`_later_resets_to_source`), including after earlier local work.

    The reflog message alone doesn't prove where the commit came from (a local
    branch can be named ``origin/x``), so no local branch may share the source's
    name, and the remote must be shown to have held that commit: the source's own
    remote-tracking reflog, or the remote's ``HEAD`` reflog (which records a
    clone), names it, or one of the remote's tracking refs contains it now. ""
    when unknown or ambiguous: not a local branch, an expired or edited reflog,
    any other source, or no such proof."""
    full = _history_read("rev-parse", "--symbolic-full-name", ref, cwd=cwd)
    name = full.stdout.strip()
    if full.returncode != 0 or not name.startswith("refs/heads/"):
        return ""
    log = _history_read("reflog", "show", "--format=%H%x09%gs", name, "--", cwd=cwd)
    entries = [ln for ln in log.stdout.splitlines() if ln.strip()]
    if log.returncode != 0 or not entries:
        return ""
    sha, _, subject = entries[-1].partition("\t")
    sha = sha.strip()
    prefix = "branch: Created from "
    if not subject.startswith(prefix) or not sha:
        return ""
    source = subject[len(prefix):].strip().removeprefix("refs/remotes/")
    remotes = _history_read("remote", cwd=cwd).stdout.split()
    if "/" not in source or source.partition("/")[0] not in remotes:
        return ""
    remote = source.partition("/")[0]
    remote_prefix = f"refs/remotes/{remote}/"
    refs_r = _history_read(
        "for-each-ref", "--format=%(refname)%09%(objectname)",
        f"refs/heads/{source}", remote_prefix, cwd=cwd,
    )
    if refs_r.returncode != 0:
        return ""
    remote_tips = set()
    for line in refs_r.stdout.splitlines():
        name, separator, object_id = line.partition("\t")
        if not separator or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", object_id):
            return ""
        if name == f"refs/heads/{source}":
            return ""  # a local branch can shadow the source in the creation message
        if name.startswith(remote_prefix):
            remote_tips.add(object_id)
    created = sha if sha in remote_tips else ""
    if not created and remote_tips:
        held_now = _history_read("for-each-ref", "--count=1", "--contains", sha, "--format=%(refname)",
                                remote_prefix, cwd=cwd)
        created = sha if held_now.returncode == 0 and held_now.stdout.strip() else ""
    if not created:
        for logged in (f"refs/remotes/{source}", f"refs/remotes/{remote}/HEAD"):  # HEAD logs a clone
            tracked = _history_read("reflog", "show", "--format=%H", logged, "--", cwd=cwd)
            if tracked.returncode == 0 and sha in {ln.strip() for ln in tracked.stdout.splitlines()}:
                created = sha
                break
    return _later_resets_to_source(entries, created, source, published_bases, cwd) if created else ""


def _later_resets_to_source(
    entries: list[str], created: str, source: str, published_bases: tuple[str, ...], cwd: str,
) -> str:
    """Find the latest reset to an independently proven source/landing-base tip.

    Earlier local work does not make a subsequent published tip unpublished.
    Commits beyond that tip remain outside this boundary, including empty or
    reverted commits; other tracked PR branches are checked separately.
    """
    if not any(
        entry.partition("\t")[2].startswith(("branch: Reset to ", "reset: moving to "))
        for entry in entries[:-1]
    ):
        return created
    prefix = f"refs/remotes/{source.partition('/')[0]}/"
    sources = {f"refs/remotes/{source}"} | {b for b in published_bases if b.startswith(prefix)}
    shadows = {ref: ref.replace("refs/remotes/", "refs/heads/", 1) for ref in sources}
    refs = _history_read(
        "for-each-ref", "--format=%(refname)", *sorted(sources | set(shadows.values())), cwd=cwd,
    )
    if refs.returncode != 0:
        return created
    names = set(refs.stdout.splitlines())
    qualified = sorted(ref for ref in sources if ref in names and shadows[ref] not in names)
    if not qualified:
        return created
    tips = _history_read("reflog", "show", "--format=%H", *qualified, "--", cwd=cwd)
    if tips.returncode != 0:
        return created
    held = {ln.strip() for ln in tips.stdout.splitlines()}
    point = created
    for entry in reversed(entries[:-1]):  # oldest first, after the creation
        sha, _, subject = entry.partition("\t")
        if subject.startswith(("branch: Reset to ", "reset: moving to ")) and sha.strip() in held:
            point = sha.strip()
    return point


def landing(branch: str, upstream: str, cwd: str, *, explain: bool = False) -> Landing:
    """Every check of :func:`is_content_on_upstream` for *branch* on *upstream*,
    stopping at the first that proves it landed unless *explain* asks for all."""
    result = Landing(branch, upstream)
    result.ancestor = git_ops.git(
        "merge-base", "--is-ancestor", branch, upstream, cwd=cwd, check=False,
    ).returncode == 0
    if result.landed and not explain:
        return result
    cherry_r = git_ops.git("cherry", upstream, branch, cwd=cwd, check=False)
    if cherry_r.returncode == 0 and cherry_r.stdout.strip():
        result.cherry = not [ln for ln in cherry_r.stdout.splitlines() if ln.startswith("+")]
    if result.landed and not explain:
        return result
    result.squash = squash_landed(branch, upstream, cwd)
    if result.landed and not explain:
        return result
    result.blobs = _blobs_on_upstream(branch, upstream, cwd)
    return result


def is_content_on_upstream(
    branch: str,
    upstream: str,
    cwd: str,
) -> bool:
    """Non-mutating check: is the branch's content already on upstream?

    Uses multiple strategies in order of reliability:
    1. Ancestor check (branch is ancestor of upstream)
    2. git cherry (patch-id comparison)
    2b. The whole branch as one patch (a squash merge; :func:`squash_landed`)
    3. Blob comparison of changed files

    No ref moves; 2b may write an unreferenced probe commit object.
    """
    return landing(branch, upstream, cwd).landed


def _blobs_on_upstream(branch: str, upstream: str, cwd: str) -> bool:
    """Strategy 3: every file *branch* changed since its merge-base with
    *upstream* has the same content on *upstream*."""
    merge_base_r = git_ops.git(
        "merge-base", branch, upstream,
        cwd=cwd, check=False,
    )
    if merge_base_r.returncode != 0:
        return False

    def run_git(*args: str):
        return git_ops.git(*args, cwd=cwd, check=False)

    try:
        changed_files = git_tree_diff.read(run_git, merge_base_r.stdout.strip(), branch)
        if not changed_files:
            return True
        if any(name in os.environ for name in (
            "GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS",
            "GIT_NOGLOB_PATHSPECS", "GIT_ICASE_PATHSPECS",
        )):
            raise git_tree_diff.TreeDiffError("inherited pathspec modes prevent literal verification")
        # Text capture normalizes CR/LF and replaces invalid UTF-8. Never infer
        # an absent entry from a path that may no longer name the original bytes.
        if any(any(char in path for char in "\r\n\ufffd") for path in changed_files):
            raise git_tree_diff.TreeDiffError("changed path is not losslessly verifiable")
        branch_objects = {
            file: after for file, (_before, after) in changed_files.items() if after.strip("0")
        }
        deleted_paths = changed_files.keys() - branch_objects.keys()
        branch_objects.update(git_tree_diff.read_objects(run_git, branch, deleted_paths))
        upstream_objects = git_tree_diff.read_objects(run_git, upstream, changed_files)
    except git_tree_diff.TreeDiffError as exc:
        output.warn(f"Cannot verify changed files on upstream: {exc}")
        return False

    return all(
        branch_objects.get(file) == upstream_objects.get(file)
        for file in changed_files
    )


def resolve_finalize_ref(
    tracked_branch: str, worktree_path: str,
) -> tuple[str, str | None, bool]:
    """Resolve which ref the "is this worktree's content on upstream?"
    check should actually validate (#7723).

    ``tracked_branch`` (``_worktree_branch``'s result) is the name recorded
    when the worktree was CREATED. It can go stale: the checkout may later
    move to a differently-named branch (e.g. a ``-journal`` suffix variant)
    or end up detached, without ever updating the tracking record. Checking
    the stale tracked name against the content that's *actually* about to be
    discarded is wrong in both directions -- it can false-block finalize on
    content that's actually already safe, or silently pass while the real
    checkout holds something different.

    Returns ``(effective_ref, current_ref, diverged)``:
      - ``effective_ref``: what finalize should actually validate against
        upstream -- the worktree's real checked-out branch, or ``"HEAD"``
        when detached.
      - ``current_ref``: the worktree's actual checked-out branch name, or
        ``None`` when detached.
      - ``diverged``: True when the checkout is not sitting on the
        originally tracked branch name -- either a differently-named
        branch, or detached HEAD (``current_ref is None``). Both are a
        signal worth surfacing, independent of whether the tracked
        branch's own content turns out to still matter.
    """
    current_ref = git_ops.current_branch(worktree_path)
    effective_ref = current_ref or "HEAD"
    diverged = current_ref != tracked_branch
    return effective_ref, current_ref, diverged


def warn_if_tracked_branch_diverged(
    worktree_id: str,
    tracked_branch: str,
    current_ref: str | None,
    upstream: str,
    worktree_path: str,
) -> bool:
    """Surface (non-blocking) orphaned work on a stale tracked branch (#7723).

    Called only when the checkout has diverged from ``tracked_branch``. The
    tracked name is still meaningful as a second, independent signal: if it
    names a ref that itself still holds content never folded into the
    actual checkout, that's real orphaned work worth a human's attention --
    flag it, but never block finalize on it (the worktree being deleted is
    the *actual checkout*, not the stale tracked name).

    Returns True when the tracked branch was flagged as possibly orphaned
    (still exists, content not confirmed on upstream) -- callers should
    preserve that ref through cleanup rather than deleting it alongside the
    checkout, so the flagged content stays reachable for a rescue.
    """
    tracked_exists = git_ops.git(
        "rev-parse", "--verify", tracked_branch, cwd=worktree_path, check=False,
    ).returncode == 0
    if not tracked_exists:
        return False
    if is_content_on_upstream(tracked_branch, upstream, cwd=worktree_path):
        return False
    checkout_desc = current_ref if current_ref is not None else "a detached HEAD"
    output.warn(
        f"Worktree {worktree_id}'s checked-out branch has diverged from its "
        f"originally tracked branch ('{tracked_branch}') -- it is now on "
        f"{checkout_desc} -- and the tracked branch still has content not on "
        f"{upstream}. This worktree's tracked branch may hold orphaned work "
        f"-- inspect 'git log {upstream}..{tracked_branch}' before it "
        f"becomes unreachable, e.g. via 'agent-worktrees claims orphans' or "
        f"a manual rescue branch/PR. Proceeding to validate the actual "
        f"checkout ({checkout_desc}) instead. The tracked branch ref is "
        f"preserved (not deleted) through cleanup."
    )
    return True
