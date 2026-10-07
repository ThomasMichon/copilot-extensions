"""Resolve which ref finalize's content-on-upstream check should validate.

Split out of ``finalize.py`` (module-size guard) -- see ``resolve_finalize_ref``.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import git_ops, output

#: The identity of the probe commit :func:`squash_landed` builds (never a ref's).
_PROBE_IDENTITY = ("-c", "user.name=agent-worktrees", "-c", "user.email=agent-worktrees@localhost")


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
    tree = git_ops.git("rev-parse", f"{branch}^{{tree}}", cwd=cwd, check=False)
    if base.returncode != 0 or tree.returncode != 0:
        return None
    base_sha, tree_sha = base.stdout.strip(), tree.stdout.strip()
    base_tree = git_ops.git("rev-parse", f"{base_sha}^{{tree}}", cwd=cwd, check=False)
    if base_tree.returncode != 0 or not base_sha or not tree_sha:
        return None
    if base_tree.stdout.strip() == tree_sha:
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


def creation_point(ref: str, cwd: str) -> str:
    """The commit local branch *ref* was created at, when it was created from a
    remote-tracking branch (its reflog's oldest entry, ``branch: Created from
    origin/main``) that is proven to have held that commit: everything reachable
    from it was already published when the branch was made, so it is never this
    branch's own work -- even after that remote rewrites its history.

    The reflog message alone doesn't prove where the commit came from (a local
    branch can be named ``origin/x``), so no local branch may share the source's
    name, and the remote must be shown to have held that commit: the source's own
    remote-tracking reflog, or the remote's ``HEAD`` reflog (which records a
    clone), names it, or one of the remote's tracking refs contains it now. ""
    when unknown or ambiguous: not a local branch, an expired or edited reflog,
    any other source, or no such proof."""
    full = git_ops.git("rev-parse", "--symbolic-full-name", ref, cwd=cwd, check=False)
    name = full.stdout.strip()
    if full.returncode != 0 or not name.startswith("refs/heads/"):
        return ""
    log = git_ops.git("reflog", "show", "--format=%H%x09%gs", name, "--", cwd=cwd, check=False)
    entries = [ln for ln in log.stdout.splitlines() if ln.strip()]
    if log.returncode != 0 or not entries:
        return ""
    sha, _, subject = entries[-1].partition("\t")
    sha = sha.strip()
    prefix = "branch: Created from "
    if not subject.startswith(prefix) or not sha:
        return ""
    source = subject[len(prefix):].strip().removeprefix("refs/remotes/")
    remotes = git_ops.git("remote", cwd=cwd, check=False).stdout.split()
    if "/" not in source or source.partition("/")[0] not in remotes:
        return ""
    if git_ops.git("rev-parse", "--verify", "-q", f"refs/heads/{source}", cwd=cwd,
                   check=False).returncode == 0:
        return ""  # a local branch with that name: the message can't tell which it was
    remote = source.partition("/")[0]
    for logged in (f"refs/remotes/{source}", f"refs/remotes/{remote}/HEAD"):  # HEAD logs a clone
        tracked = git_ops.git("reflog", "show", "--format=%H", logged, "--", cwd=cwd, check=False)
        if tracked.returncode == 0 and sha in {ln.strip() for ln in tracked.stdout.splitlines()}:
            return sha
    held_now = git_ops.git("for-each-ref", "--count=1", "--contains", sha, "--format=%(refname)",
                           f"refs/remotes/{remote}/", cwd=cwd, check=False)
    return sha if held_now.returncode == 0 and held_now.stdout.strip() else ""


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

    diff_r = git_ops.git(
        "diff", "--name-only", merge_base_r.stdout.strip(), branch,
        cwd=cwd, check=False,
    )
    changed_files = [f for f in diff_r.stdout.splitlines() if f.strip()]
    if not changed_files:
        return True

    for file in changed_files:
        b_blob = git_ops.git(
            "rev-parse", f"{branch}:{file}", cwd=cwd, check=False
        )
        m_blob = git_ops.git(
            "rev-parse", f"{upstream}:{file}", cwd=cwd, check=False
        )
        if b_blob.stdout.strip() != m_blob.stdout.strip():
            return False

    return True


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


