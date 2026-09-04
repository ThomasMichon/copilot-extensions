"""Git subprocess helper and worktree state classification.

All git operations go through :func:`git` which provides consistent
error handling, explicit cwd, and machine-readable output.
"""

from __future__ import annotations

import base64
import functools
import logging
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

log = logging.getLogger("agent-worktrees")

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


def repository_identity_env() -> dict[str, str]:
    """Return ambient process state without inherited Git context.

    Repository identity probes supply their checkout explicitly with ``git -C``.
    Inherited repository/config-selection variables can override or alter that
    selection, so they are removed. Unrelated process and Git settings remain.
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


# --- Path helpers -----------------------------------------------------------

def _normalize_wt_path(p: str) -> str:
    """Normalize a worktree path for comparison (case-insensitive on Windows)."""
    p = str(Path(p).resolve()).rstrip("/\\")
    if platform.system() == "Windows":
        return p.lower()
    return p


def is_cwd_inside(worktree_path: str) -> bool:
    """Return True if the current working directory is inside *worktree_path*."""
    cwd = _normalize_wt_path(os.getcwd())
    wt = _normalize_wt_path(worktree_path)
    return cwd == wt or cwd.startswith(wt + os.sep)


def resolve_to_anchor(repo_path: Path) -> Path:
    """Resolve a git worktree path back to its anchor (main checkout).

    If *repo_path* is already the anchor (has a ``.git`` directory), it is
    returned unchanged.  If it is a worktree (has a ``.git`` file),
    ``git rev-parse --git-common-dir`` is used to find the shared ``.git``
    directory whose parent is the anchor repo root.
    """
    git_path = repo_path / ".git"
    if git_path.is_dir():
        return repo_path  # already the anchor
    if git_path.is_file():
        try:
            r = subprocess.run(
                ["git", "-C", str(repo_path), "rev-parse", "--git-common-dir"],
                capture_output=True, text=True, timeout=5,
                env=repository_identity_env(),
            )
            if r.returncode == 0:
                common = Path(r.stdout.strip())
                if not common.is_absolute():
                    common = (repo_path / common).resolve()
                # common is the shared .git dir; its parent is the anchor
                anchor = common.parent
                if (anchor / ".git").is_dir():
                    return anchor
        except Exception:
            pass
    return repo_path


def _redact_args(cmd: list[str]) -> list[str]:
    """Redact any injected credential header so tokens never reach logs/errors."""
    out: list[str] = []
    for a in cmd:
        if a.startswith("http.extraheader="):
            out.append("http.extraheader=<redacted>")
        else:
            out.append(a)
    return out


class GitError(Exception):
    """A git command failed."""

    def __init__(self, cmd: list[str], returncode: int, stderr: str) -> None:
        self.cmd = _redact_args(cmd)
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"git {' '.join(self.cmd[1:])} failed (rc={returncode}): {stderr}"
        )


#: A hooks directory guaranteed to hold no hooks, used to disable a repo's
#: *client-side* guard hooks for the plugin's own trusted mechanical git ops
#: (squash re-commit / rebase / push -- see :func:`git` ``no_hooks``). ``/dev/null``
#: is the portable idiom: git looks for hook files under this path, finds none,
#: and runs no hook -- on POSIX and on Git-for-Windows alike. Server-side branch
#: protection is unaffected (it is not a client hook). #3707.
_NO_HOOKS_PATH = "/dev/null"


def git(
    *args: str,
    cwd: str | Path | None = None,
    check: bool = True,
    capture: bool = True,
    timeout: float | None = None,
    no_hooks: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a git command with consistent error handling.

    Args:
        *args: Git subcommand and arguments (without 'git' prefix).
        cwd: Working directory for the command.
        check: If True, raise GitError on non-zero exit.
        capture: If True, capture stdout and stderr.
        timeout: If set, seconds to wait before ``subprocess.run`` raises
            ``subprocess.TimeoutExpired``. Default ``None`` keeps the historical
            unbounded behavior for every caller that does not opt in (e.g.
            network ops like ``fetch``/``push``). Read-only inspection callers
            (worktree classification) pass a bound so a single stalled ``git``
            spawn cannot hang them indefinitely.
        no_hooks: If True, run with ``-c core.hooksPath=<empty>`` so a repo's
            **client-side** guard hooks (a branch-protection ``pre-commit`` /
            ``pre-push`` / ``pre-rebase``) cannot block or corrupt the tool's own
            trusted, mechanical plumbing (the squash re-commit, rebase, push).
            Only *client-side* hooks are disabled -- server-side branch
            protection (Gitea/GitHub rulesets) is untouched -- and only for
            operations that re-arrange or re-commit ALREADY-committed content, so
            content-quality checks that ran at original-commit time still hold.
            This is **not** ``--no-verify`` (disallowed for agent-authored
            commits): it scopes the disable to the plugin's internal git ops via
            a config override. See #3707.

    Returns:
        CompletedProcess with stdout/stderr as strings.
    """
    prefix = ["-c", f"core.hooksPath={_NO_HOOKS_PATH}"] if no_hooks else []
    cmd = ["git", *prefix, *args]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise GitError(cmd, result.returncode, result.stderr.strip())
    return result


class WorktreeState(str, Enum):
    """Worktree state classification.

    Every value except :attr:`CONVO` is produced by :func:`classify_worktree`
    from pure git inspection.  ``CONVO`` is a *session-derived* refinement of
    ``UNUSED`` -- a clean, commit-less worktree whose Copilot session
    nonetheless held conversation turns -- layered on by
    :func:`refine_state_with_session`.  It lives in this enum (rather than as a
    private render flag) so the tmux status bar and the picker data contract
    (``list --json --classify``) share one display vocabulary.
    """

    ACTIVE = "active"
    WIP = "wip"
    DIRTY = "dirty"
    UNUSED = "unused"
    CONVO = "convo"
    COMPLETED = "completed"
    GONE = "gone"
    ORPHAN = "orphan"
    UNKNOWN = "unknown"


def refine_state_with_session(state: WorktreeState, turns: int) -> WorktreeState:
    """Layer the session-derived ``CONVO`` refinement onto a git ``state``.

    A clean, commit-less worktree (:attr:`WorktreeState.UNUSED`) whose session
    held conversation turns is not idle -- it is rendered as a distinct
    ``CONVO`` block rather than grey ``UNUSED``.  Centralized here so every
    surface that reports a worktree's *display* state -- the tmux status bar
    (``status-segment``) and the picker data contract
    (``list --json --classify``) -- draws from one vocabulary instead of each
    re-deriving the rule.

    Pure: performs no git or session I/O; ``turns`` is supplied by the caller
    (e.g. from :class:`sessions.SessionContext`).  Only ``UNUSED`` is refined;
    any other state is returned unchanged.
    """
    if state == WorktreeState.UNUSED and turns > 0:
        return WorktreeState.CONVO
    return state


@dataclass
class WorktreeStateInfo:
    """Result of classifying a worktree's git state."""

    state: WorktreeState
    ahead: int = 0
    behind: int = 0
    dirty: int = 0
    title: str = ""
    current_branch: str | None = None
    """The worktree's actual HEAD branch (None if detached or unreadable)."""
    branch_drift: bool = False
    """True when the worktree's HEAD is on a different branch than tracked."""


@dataclass
class FastForwardResult:
    """Outcome of attempting to fast-forward a worktree to its upstream.

    ``updated`` is True only when commits were actually advanced.  ``reason``
    is a stable token describing what happened (or why nothing did):

    - ``updated``     -- fast-forwarded ``behind`` commits onto the branch.
    - ``up-to-date``  -- clean and already level with upstream (no-op).
    - ``dirty``       -- working tree has uncommitted changes; skipped.
    - ``ahead``       -- branch has local commits, none behind; skipped.
    - ``diverged``    -- branch has both local commits and is behind; skipped.
    - ``detached``    -- HEAD is detached; skipped.
    - ``no-upstream`` -- the upstream ref does not exist; skipped.
    - ``orphan``      -- no merge base with upstream; skipped.
    - ``gone``        -- the worktree path/.git is missing; skipped.
    - ``ff-failed``   -- the fast-forward merge itself failed; skipped.
    """

    updated: bool
    reason: str
    behind: int = 0
    ahead: int = 0


@dataclass
class WorktreeBaseResult:
    """Fresh-base resolution for worktree creation or local source binding."""

    start_point: str
    fetched: bool
    fetch_error: str | None
    anchor: FastForwardResult


def _rev_count(rangespec: str, *, cwd: str | Path) -> int:
    """Return ``git rev-list --count <rangespec>`` as an int (0 on failure)."""
    result = git("rev-list", "--count", rangespec, cwd=cwd, check=False)
    if result.returncode != 0:
        return 0
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 0


def can_fast_forward(info: WorktreeStateInfo) -> bool:
    """Return True when an already-classified worktree is fast-forward eligible.

    Eligible means: clean working tree, no local commits ahead of upstream,
    and strictly behind.  This is a cheap predicate over an existing
    :class:`WorktreeStateInfo` (no git calls); the freshness of ``behind``
    depends on whether the classification fetched first.
    """
    return info.dirty == 0 and info.ahead == 0 and info.behind > 0


def _get_current_branch_safe(cwd: str | Path) -> str | None:
    """Return the worktree's current branch, or None if detached/unreadable."""
    result = git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd, check=False)
    if result.returncode != 0:
        return None
    name = result.stdout.strip()
    # "HEAD" means detached -- not a named branch
    return None if name == "HEAD" else name


def current_branch(cwd: str | Path) -> str | None:
    """Return the checkout's current branch name, or None if detached/unreadable.

    Public wrapper over the internal helper so other modules can gate on the
    checked-out branch without reaching into a private symbol.
    """
    return _get_current_branch_safe(cwd)


def classify_worktree(
    worktree_path: str,
    branch: str,
    *,
    fetch: bool = False,
    remote: str = "origin",
    default_branch: str = "master",
    active_paths: set[str] | None = None,
) -> WorktreeStateInfo:
    """Classify a worktree's git state.

    Args:
        worktree_path: Filesystem path to the worktree.
        branch: The worktree's branch name (e.g., worktree/id).
        fetch: If True, fetch before classification (slower but more accurate).
        remote: Remote name.
        default_branch: Default branch name.
        active_paths: Set of normalized worktree paths with live Copilot
            sessions.  When provided, any worktree whose path is in this
            set is classified as ACTIVE regardless of git state -- it must
            never appear as COMPLETED or UNUSED.

    Returns:
        WorktreeStateInfo with the classification result.
    """
    path = Path(worktree_path)
    if not path.exists():
        return WorktreeStateInfo(state=WorktreeState.GONE)

    # A directory without a .git entry (file or dir) is a zombie -- the
    # worktree was partially removed or never fully created.
    if not (path / ".git").exists():
        return WorktreeStateInfo(state=WorktreeState.GONE)

    # Detect branch drift: if the worktree's HEAD is on a different branch
    # than what the tracking record says, use the actual HEAD for
    # classification so unmerged work on feature branches isn't hidden.
    actual_branch = _get_current_branch_safe(path)
    drift = bool(
        actual_branch
        and actual_branch != branch
    )
    effective_branch = actual_branch if drift else branch

    # If a live Copilot session owns this worktree, it is ACTIVE -- period.
    # active_paths stores paths stripped of trailing separators (but NOT
    # lowercased).  Use case-insensitive lookup on Windows to match.
    if active_paths is not None:
        norm = worktree_path.rstrip("/\\")
        _casefold = platform.system() == "Windows"
        if any(
            norm == ap if not _casefold else norm.lower() == ap.lower()
            for ap in active_paths
        ):
            return WorktreeStateInfo(
                state=WorktreeState.ACTIVE,
                current_branch=actual_branch,
                branch_drift=drift,
            )

    upstream = f"{remote}/{default_branch}"

    # Bound each classification git spawn so one stalled `git` (a Defender
    # scan, an index/ref .lock, a slow pack read) cannot hang the caller. The
    # picker loops this over every worktree, so an unbounded stall froze the
    # whole tab for minutes. On timeout report an honest UNKNOWN for *this*
    # worktree rather than fabricating a concrete state from a half-finished
    # probe (an empty `status` looks clean; a failed `merge-base` looks ORPHAN).
    # The picker's silent repoll re-resolves UNKNOWN rows on a later tick.
    try:
        return _classify_git_state(
            path, effective_branch, upstream,
            fetch=fetch, remote=remote, default_branch=default_branch,
            actual_branch=actual_branch, drift=drift,
        )
    except subprocess.TimeoutExpired:
        log.warning(
            "classify_worktree: git timed out for %s -- reporting UNKNOWN", path,
        )
        return WorktreeStateInfo(
            state=WorktreeState.UNKNOWN,
            current_branch=actual_branch, branch_drift=drift,
        )


# Per-call bound for classification git spawns. Generous: a normal status /
# rev-list / cherry is well under a second, so this only trips on a genuine
# stall (Defender scan, an index/ref .lock, a slow pack read), never on
# legitimately-slow-but-progressing git.
_CLASSIFY_GIT_TIMEOUT = 15.0


def _classify_git_state(
    path: Path,
    effective_branch: str,
    upstream: str,
    *,
    fetch: bool,
    remote: str,
    default_branch: str,
    actual_branch: str | None,
    drift: bool,
) -> WorktreeStateInfo:
    """Git-driven half of :func:`classify_worktree` (dirty/ahead/behind/merge).

    Every git call is bounded by ``_CLASSIFY_GIT_TIMEOUT`` via the ``_g`` helper;
    a stalled spawn raises ``subprocess.TimeoutExpired``, which the caller turns
    into an honest ``UNKNOWN`` state. Split out from ``classify_worktree`` so the
    timeout guard wraps one cohesive block while the pure/session short-circuits
    stay above it.
    """
    def _g(*args):
        return git(*args, cwd=path, check=False, timeout=_CLASSIFY_GIT_TIMEOUT)

    if fetch:
        _g("fetch", remote, default_branch, "--quiet")

    # Dirty check
    result = _g("status", "--porcelain")
    dirty_lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    dirty_count = len(dirty_lines)

    # Ahead / behind in one spawn.  A symmetric-difference count also lets us
    # defer merge-base: unrelated histories necessarily have commits on both
    # sides, while an all-ahead or all-behind pair already shares an ancestor.
    counts_r = _g(
        "rev-list", "--left-right", "--count",
        f"{effective_branch}...{upstream}",
    )
    counts_ok = counts_r.returncode == 0
    try:
        ahead_s, behind_s = counts_r.stdout.split()
        ahead = int(ahead_s) if counts_ok else 0
        behind = int(behind_s) if counts_ok else 0
    except (AttributeError, TypeError, ValueError):
        ahead = behind = 0

    merge_base: str | None = None

    def _merge_base() -> str | None:
        nonlocal merge_base
        if merge_base is None:
            mb = _g("merge-base", upstream, effective_branch)
            if mb.returncode != 0:
                return None
            merge_base = mb.stdout.strip()
        return merge_base

    if (not counts_ok or (ahead > 0 and behind > 0)) and _merge_base() is None:
        return WorktreeStateInfo(
            state=WorktreeState.ORPHAN, dirty=dirty_count,
            current_branch=actual_branch, branch_drift=drift,
        )

    # Last commit subject as fallback title
    title = ""
    if ahead > 0:
        title_r = _g(
            "--no-pager", "log", "-1", "--format=%s", effective_branch,
        )
        if title_r.returncode == 0:
            title = title_r.stdout.strip()
            if len(title) > 60:
                title = title[:57] + "..."

    _drift_fields = dict(current_branch=actual_branch, branch_drift=drift)

    if dirty_count > 0:
        return WorktreeStateInfo(
            state=WorktreeState.DIRTY,
            ahead=ahead, behind=behind, dirty=dirty_count, title=title,
            **_drift_fields,
        )

    if ahead == 0:
        # Check reflog for past commits (squash-merged back)
        reflog = _g(
            "--no-pager", "reflog", "show", effective_branch, "--format=%gs",
        )
        has_commits = any(
            ln.startswith("commit")
            for ln in (reflog.stdout or "").splitlines()
        )
        state = WorktreeState.COMPLETED if has_commits else WorktreeState.UNUSED
        return WorktreeStateInfo(
            state=state, ahead=0, behind=behind, title="",
            **_drift_fields,
        )

    # Branch has commits -- check if changes already in master (squash-merged).
    #
    # Use `git cherry` for patch-id comparison: it detects equivalent
    # patches even when commit SHAs differ (squash-merge) and even when
    # upstream later modified the same files.  Lines starting with "-"
    # are already on upstream; "+" means unmerged.
    cherry_r = _g("cherry", upstream, effective_branch)
    if cherry_r.returncode == 0 and cherry_r.stdout.strip():
        unmerged = [ln for ln in cherry_r.stdout.splitlines()
                    if ln.startswith("+")]
        if not unmerged:
            return WorktreeStateInfo(
                state=WorktreeState.COMPLETED,
                ahead=ahead, behind=behind, title=title,
                **_drift_fields,
            )

    # Fallback: direct blob comparison for cases git-cherry can't match
    # (e.g. content arrived on upstream via a different patch shape).
    merge_base = _merge_base()
    if merge_base is None:
        return WorktreeStateInfo(
            state=WorktreeState.ORPHAN, dirty=dirty_count,
            current_branch=actual_branch, branch_drift=drift,
        )
    diff_r = _g("diff", "--name-only", merge_base, effective_branch)
    changed_files = [f for f in diff_r.stdout.splitlines() if f.strip()]

    if not changed_files:
        return WorktreeStateInfo(
            state=WorktreeState.UNUSED,
            ahead=ahead, behind=behind, title=title,
            **_drift_fields,
        )

    # Compare file blobs between branch and master
    for file in changed_files:
        b_blob = _g("rev-parse", f"{effective_branch}:{file}")
        m_blob = _g("rev-parse", f"{upstream}:{file}")
        if b_blob.stdout.strip() != m_blob.stdout.strip():
            return WorktreeStateInfo(
                state=WorktreeState.WIP,
                ahead=ahead, behind=behind, title=title,
                **_drift_fields,
            )

    return WorktreeStateInfo(
        state=WorktreeState.COMPLETED,
        ahead=ahead, behind=behind, title=title,
        **_drift_fields,
    )


# --- High-level git operations for finalization ---


def has_remote(remote: str, *, cwd: str | Path) -> bool:
    """Return True if *remote* is configured in the repo."""
    result = git("remote", cwd=cwd, check=False)
    if result.returncode != 0:
        return False
    remotes = (result.stdout or "").split()
    return remote in remotes


#: Default bound (seconds) for a network ``fetch``. An unbounded fetch hangs the
#: whole flow when the remote is unreachable (Gitea down / offline) -- every
#: fetch caller here is best-effort or has a fast-path fallback, so a stalled
#: fetch should fail fast, not wedge push-changes / create-pr / pr-status /
#: sync. Generous enough for a slow-but-live remote; callers may override.
DEFAULT_FETCH_TIMEOUT = 30.0


def fetch(
    remote: str, *, cwd: str | Path, timeout: float | None = DEFAULT_FETCH_TIMEOUT
) -> None:
    """Fetch from a remote (auto-authenticating cross-account remotes; #29).

    Bounded by ``timeout`` (default :data:`DEFAULT_FETCH_TIMEOUT`) so an
    unreachable remote can't hang the caller indefinitely: a stall past the
    bound is surfaced as a :class:`GitError` (returncode 124, the timeout
    convention), which every current caller already handles as a best-effort
    fetch failure. Pass ``timeout=None`` for the historical unbounded behavior.
    """
    try:
        git(
            *_auth_config_args(remote, cwd=cwd), "fetch", remote, "--quiet",
            cwd=cwd, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(
            ["git", "fetch", remote, "--quiet"], 124,
            f"fetch from '{remote}' timed out after {timeout}s "
            "(remote unreachable?)",
        ) from exc


def rebase(onto: str, *, cwd: str | Path) -> bool:
    """Rebase the current branch onto a ref. Returns True on success."""
    result = git("rebase", onto, cwd=cwd, check=False, no_hooks=True)
    if result.returncode != 0:
        git("rebase", "--abort", cwd=cwd, check=False)
        return False
    return True


def checkout(branch: str, *, cwd: str | Path) -> None:
    """Checkout a branch."""
    git("checkout", branch, "--quiet", cwd=cwd)


def merge_ff(branch: str, *, cwd: str | Path) -> bool:
    """Fast-forward merge. Returns True on success."""
    result = git("merge", branch, "--ff-only", "--quiet", cwd=cwd, check=False)
    return result.returncode == 0


def fast_forward_worktree(
    worktree_path: str | Path,
    *,
    remote: str = "origin",
    default_branch: str = "master",
    do_fetch: bool = True,
) -> FastForwardResult:
    """Safely fast-forward a worktree's branch to its upstream default branch.

    Fast-forward *only*.  The branch is advanced to ``<remote>/<default_branch>``
    only when ALL of these hold:

    - the working tree is clean (no uncommitted changes),
    - the branch has zero commits ahead of upstream (no local work to lose),
    - the branch is strictly behind upstream.

    A dirty, ahead, diverged, or detached worktree is never touched -- this
    function never rebases, never creates a merge commit, and never discards
    local commits.  When ``do_fetch`` is True the remote is fetched first so
    the ahead/behind comparison and fast-forward target are against the
    freshest upstream; a fetch failure (e.g. offline) is non-fatal and the
    comparison falls back to the already-known upstream ref.
    """
    path = Path(worktree_path)
    if not path.exists() or not (path / ".git").exists():
        return FastForwardResult(updated=False, reason="gone")

    upstream = f"{remote}/{default_branch}"

    if do_fetch and has_remote(remote, cwd=path):
        try:
            fetch(remote, cwd=path)
        except Exception:
            # Offline or auth failure -- compare against the local upstream ref.
            pass

    if not ref_exists(upstream, cwd=path):
        return FastForwardResult(updated=False, reason="no-upstream")

    if not is_clean(cwd=path):
        return FastForwardResult(updated=False, reason="dirty")

    branch = _get_current_branch_safe(path)
    if branch is None:
        return FastForwardResult(updated=False, reason="detached")

    mb = git("merge-base", upstream, branch, cwd=path, check=False)
    if mb.returncode != 0:
        return FastForwardResult(updated=False, reason="orphan")
    merge_base = mb.stdout.strip()

    ahead = _rev_count(f"{merge_base}..{branch}", cwd=path)
    behind = _rev_count(f"{branch}..{upstream}", cwd=path)

    if ahead > 0:
        return FastForwardResult(
            updated=False,
            reason="diverged" if behind > 0 else "ahead",
            ahead=ahead, behind=behind,
        )
    if behind == 0:
        return FastForwardResult(updated=False, reason="up-to-date")

    if not merge_ff(upstream, cwd=path):
        return FastForwardResult(updated=False, reason="ff-failed", behind=behind)
    return FastForwardResult(updated=True, reason="updated", behind=behind)


def merge_squash(branch: str, worktree_id: str, *, cwd: str | Path) -> bool:
    """Squash merge with auto-commit. Returns True on success."""
    result = git("merge", branch, "--squash", "--quiet", cwd=cwd, check=False)
    if result.returncode != 0:
        return False
    # no_hooks: this is the plugin's own trusted landing commit of ALREADY-reviewed
    # worktree content into the anchor's default branch. It must not be blocked by
    # the client-side guard hooks (the anchor-commit / default-branch pre-commit
    # guard), which exist to stop *stray* human/agent commits -- not this
    # mechanical finalize step. Mirrors the sibling squash path + rebase/push.
    commit_r = git(
        "commit", "--no-edit", "-m", f"squash: merge worktree/{worktree_id}",
        cwd=cwd, check=False, no_hooks=True,
    )
    return commit_r.returncode == 0


@dataclass
class PushResult:
    """Outcome of :func:`push` -- truthy on success, but also carrying git's
    stderr and a retry classification so callers can surface the *real* reason
    a push failed instead of a generic "rejected".

    ``__bool__`` returns ``ok`` so every existing ``if git_ops.push(...)`` /
    ``pushed = git_ops.push(...)`` call keeps working unchanged.
    """

    ok: bool
    stderr: str = ""

    def __bool__(self) -> bool:
        return self.ok

    @property
    def retryable(self) -> bool:
        """True only when the failure is a **non-fast-forward race** (the remote
        advanced) -- the one case a fetch + rebase + re-push actually fixes.

        Everything else recurs identically on retry and must surface at once: a
        **pre-push hook decline** (e.g. a repo's version-consistency / lint
        gate), an **auth / permission** error (403, multi-account token
        mismatch), or a **protected-branch** block. Classified from git's
        stderr; an empty/unknown stderr is treated as non-retryable so a mystery
        failure is shown rather than silently retried 3x.
        """
        if self.ok:
            return False
        s = self.stderr.lower()
        return (
            "non-fast-forward" in s
            or "fetch first" in s
            or "tip of your current branch is behind" in s
            or "the remote contains work that you do" in s
        )


def push(
    remote: str,
    branch: str,
    *,
    cwd: str | Path,
    force_with_lease: bool = False,
) -> PushResult:
    """Push a branch to remote. Returns a :class:`PushResult` (truthy on success).

    Auto-authenticates when the remote is owned by a different ``gh`` account
    than the active one, without persisting a token in ``.git/config`` (#29).

    When *force_with_lease* is True, push with ``--force-with-lease`` -- used
    by the PR workflow to update a feature branch whose history was rewritten
    by the rebase chain, without clobbering unrelated remote updates.

    The result carries git's ``stderr`` and a ``retryable`` classification so a
    caller's retry loop can surface the real error (a pre-push hook decline, an
    auth 403, a protected-branch block) and fail fast instead of masking every
    failure as a generic "rejected" and retrying a doomed push (#993).
    """
    extra = ["--force-with-lease"] if force_with_lease else []
    auth_args = _auth_config_args(remote, cwd=cwd)
    result = git(
        *auth_args,
        "push", remote, branch, *extra, "--quiet",
        cwd=cwd, check=False, no_hooks=True,
    )
    if result.returncode == 0:
        return PushResult(ok=True)
    last_stderr = result.stderr or ""
    # Defense-in-depth: if we injected a cross-account token and the push
    # still failed, the injected gh OAuth token may lack push scope (#900).
    # Retry once *without* the override so the default credential helper
    # (git-credential-vault / GCM) can authenticate -- which often succeeds
    # where the OAuth token 403s.
    if auth_args:
        retry = git(
            "push", remote, branch, *extra, "--quiet",
            cwd=cwd, check=False, no_hooks=True,
        )
        if retry.returncode == 0:
            return PushResult(ok=True)
        last_stderr = retry.stderr or last_stderr
    return PushResult(ok=False, stderr=last_stderr)


# --- Cross-account authentication (#29) -------------------------------------
#
# push-changes / finalize run plain git push/fetch against ``origin``. When the
# repo is owned by a *different* GitHub account than the active ``gh`` account
# (e.g. a personal-account-owned repo while the active account is a work
# account), a plain push 403s. We resolve the owner from the remote URL and, if
# a ``gh`` account for that owner is authenticated, inject its token as a
# one-shot HTTP auth header -- never writing the token to ``.git/config``.
#
# For org-owned repos the owner is not a ``gh`` *account*, so ``gh auth token
# --user <org>`` fails and we transparently fall back to default git behavior.

def _remote_url(remote: str, *, cwd: str | Path) -> str | None:
    """Return the configured URL for *remote*, or None."""
    result = git("remote", "get-url", remote, cwd=cwd, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def resolve_remote_name(remote_or_url: str, *, cwd: str | Path) -> str:
    """Resolve a configured remote name from either its name or registry URL."""
    result = git("remote", cwd=cwd, check=False)
    if result.returncode != 0:
        return remote_or_url or "origin"
    names = [name.strip() for name in result.stdout.splitlines() if name.strip()]
    if remote_or_url in names:
        return remote_or_url
    expected = remote_or_url.rstrip("/")
    if expected:
        for name in names:
            actual = (_remote_url(name, cwd=cwd) or "").rstrip("/")
            if actual == expected:
                return name
    if "origin" in names:
        return "origin"
    return names[0] if names else (remote_or_url or "origin")


def _parse_github_owner(url: str) -> str | None:
    """Extract the owner from a github.com remote URL (https or ssh form)."""
    url = url.strip()
    m = re.match(r"https?://[^/]*github\.com/([^/]+)/", url)
    if m:
        return m.group(1)
    m = re.match(r"(?:ssh://)?git@[^:/]*github\.com[:/]([^/]+)/", url)
    if m:
        return m.group(1)
    return None


def slug_from_url(url: str | None) -> str | None:
    """Parse the hosting repo slug from a remote *URL*, or None.

    The provider-correct repo identifier -- GitHub ``owner/name`` or Azure
    DevOps ``project/repo``. The pure URL-parsing core of :func:`remote_slug`
    (which resolves a remote *name* to its URL first). Parses the last two path
    components (dropping a trailing ``.git``), so it works for both https and
    ssh forms and for self-hosted hosts with a path prefix, e.g.:

        https://host/gitea/example-user/test-chamber.git -> example-user/test-chamber
        git@github.com:owner/repo.git                -> owner/repo
        https://{org}.visualstudio.com/{proj}/_git/{repo} -> {proj}/{repo}

    This is the value a PR provider needs (the API is keyed on the provider
    repo slug), as opposed to the local project name. Accepts a URL string
    (callers that have only a remote *name* should use :func:`remote_slug`).
    """
    if not url:
        return None
    s = url.strip().rstrip("/")
    if s.endswith(".git"):
        s = s[:-4]
    s = s.rstrip("/")
    # Normalize scp-like ssh (git@host:owner/repo) by splitting on ':' too.
    parts = [p for p in re.split(r"[/:]", s) if p]
    if len(parts) < 2:
        return None
    # Azure DevOps https remotes carry a ``_git`` segment:
    #   https://{org}.visualstudio.com/{project}/_git/{repo}
    #   https://dev.azure.com/{org}/{project}/_git/{repo}
    # The provider API is keyed on ``{project}/{repo}`` -- the segment before
    # ``_git`` is the project, the one after is the repo. (ADO ssh remotes use
    # ``v3/{org}/{project}/{repo}`` with no ``_git`` and resolve fine below.)
    if "_git" in parts:
        gi = parts.index("_git")
        if 0 < gi < len(parts) - 1:
            return f"{parts[gi - 1]}/{parts[gi + 1]}"
    return f"{parts[-2]}/{parts[-1]}"


def remote_slug(remote: str, *, cwd: str | Path) -> str | None:
    """Return the hosting repo slug for the remote *named* ``remote``.

    The provider-correct repo identifier (GitHub ``owner/name`` or Azure DevOps
    ``project/repo``). Resolves the remote name (e.g. ``origin``) to its
    configured URL in ``cwd``, then defers to :func:`slug_from_url` for the
    parse. Callers that already hold a remote URL should call
    :func:`slug_from_url` directly.
    """
    return slug_from_url(_remote_url(remote, cwd=cwd))


@functools.cache
def _gh_token_for_owner(owner: str) -> str | None:
    """Return a ``gh`` token for the GitHub account *owner*, or None.

    Cached per-owner so a push/fetch pair makes at most one ``gh`` call.
    """
    if not owner or shutil.which("gh") is None:
        return None
    try:
        result = subprocess.run(
            ["gh", "auth", "token", "--user", owner],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def gh_token_for_account(account: str) -> str | None:
    """Public: mint a ``gh`` token for a specific account login, or None.

    The auth-provider seam for the repo-scoped identity layer -- ``gh`` itself
    is the token store/minter in v1.  Returns None when ``gh`` is unavailable
    or ``account`` is not an authenticated ``gh`` account (callers then fall
    back to the ambient credential).
    """
    return _gh_token_for_owner(account)


@functools.cache
def _active_gh_account() -> str | None:
    """Return the login of the **active** ``gh`` account, or None.

    Parsed from ``gh auth status`` (no network call). Used to decide whether
    the cross-account token override is needed: when the repo owner *is* the
    active account, the default credential helper already authenticates as
    that user, and overriding it with the account's ``gh`` OAuth token can
    instead cause a 403 (the device/web OAuth token may lack push scope).
    """
    if shutil.which("gh") is None:
        return None
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return None
    # `gh auth status` prints per-account blocks; the active one carries a
    # following "Active account: true" line. Track the most recent
    # "account <login>" and return it when the active marker appears. Fall
    # back to the sole logged-in account when no active marker is present
    # (older single-account `gh`).
    text = result.stdout + result.stderr
    accounts: list[str] = []
    current: str | None = None
    for line in text.splitlines():
        m = re.search(r"account\s+([A-Za-z0-9_](?:[A-Za-z0-9_-]*[A-Za-z0-9_])?)", line)
        if m:
            current = m.group(1)
            accounts.append(current)
        if "Active account: true" in line and current:
            return current
    return accounts[0] if len(accounts) == 1 else None


def active_gh_account() -> str | None:
    """Public accessor for the **active** ``gh`` account login (or None).

    Thin wrapper over the cached internal resolver so other modules -- notably
    the PR token path -- can apply the same "owner == active account" guard the
    git auth-args path uses, without reaching for a private symbol.
    """
    return _active_gh_account()


def list_gh_accounts() -> list[str]:
    """Return the logins of all authenticated ``gh`` accounts (may be empty).

    Parsed from ``gh auth status`` (no network call). Used at registration time
    to offer the operator a concrete set of accounts when a repo's owner is an
    org (not itself a ``gh`` account).
    """
    if shutil.which("gh") is None:
        return []
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return []
    text = result.stdout + result.stderr
    seen: list[str] = []
    for line in text.splitlines():
        if "Logged in to" not in line:
            continue
        m = re.search(r"account\s+([A-Za-z0-9_](?:[A-Za-z0-9_-]*[A-Za-z0-9_])?)", line)
        if m and m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def _auth_config_args(remote: str, *, cwd: str | Path) -> list[str]:
    """Build ``-c http.extraheader=...`` args to auth as the remote's owner.

    Returns ``[]`` when no override is needed or possible (non-GitHub remote,
    ``gh`` unavailable, owner is not an authenticated ``gh`` account, or the
    owner **is** the active ``gh`` account).

    The override exists for the cross-account case: the repo is owned by a
    different account than the active ``gh`` account, so a plain push would
    403. But when the owner *is* the active account, the default credential
    helper already authenticates correctly; injecting the active account's
    ``gh`` OAuth token would *override* that helper with a token that may lack
    push scope, turning a working push into a 403 (#900). So skip injection in
    that case and let the credential helper do its job.
    """
    url = _remote_url(remote, cwd=cwd)
    if not url:
        return []
    owner = _parse_github_owner(url)
    if not owner:
        return []
    # Honor an explicit repos.yaml ``account:`` override (owner != account is
    # possible for EMU accounts spanning orgs); absent an override the account
    # *is* the owner, preserving the derive-from-owner behavior (#29).
    try:
        from . import repos
        account = repos.account_for_github_owner(owner) or owner
    except Exception:
        account = owner
    active = _active_gh_account()
    if active and active.casefold() == account.casefold():
        return []
    token = _gh_token_for_owner(account)
    if not token:
        return []
    cred = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return ["-c", f"http.extraheader=AUTHORIZATION: basic {cred}"]


def ref_exists(ref: str, *, cwd: str | Path) -> bool:
    """Return True if a git ref/commit resolves in the repo."""
    result = git(
        "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}",
        cwd=cwd, check=False,
    )
    return result.returncode == 0


def remote_branch_exists(remote: str, branch: str, *, cwd: str | Path) -> bool:
    """Return True if *branch* exists on *remote* (via ls-remote)."""
    result = git(
        *_auth_config_args(remote, cwd=cwd),
        "ls-remote", "--heads", remote, branch,
        cwd=cwd, check=False,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def remote_branch_state(remote: str, branch: str, *, cwd: str | Path) -> str:
    """Return the tri-state presence of *branch* on *remote* (via ls-remote).

    Unlike :func:`remote_branch_exists` (which collapses "absent" and
    "unreachable" into ``False``), this distinguishes:

    - ``"present"``  -- the remote is reachable and advertises the branch.
    - ``"absent"``   -- the remote is reachable but has no such branch (e.g. it
      was auto-deleted after a merge).
    - ``"unknown"``  -- ``ls-remote`` failed (remote unreachable / auth error);
      the caller must not infer deletion from this.

    Callers that would take a destructive or irreversible action on the
    strength of a branch being *gone* (e.g. refusing to reuse it) must treat
    only ``"absent"`` as authoritative and fall back to their prior behavior on
    ``"unknown"``.
    """
    result = git(
        *_auth_config_args(remote, cwd=cwd),
        "ls-remote", "--heads", remote, branch,
        cwd=cwd, check=False,
    )
    if result.returncode != 0:
        return "unknown"
    return "present" if result.stdout.strip() else "absent"


def local_branch_exists(branch: str, *, cwd: str | Path) -> bool:
    """Return True if a local branch named *branch* exists."""
    result = git(
        "show-ref", "--verify", "--quiet", f"refs/heads/{branch}",
        cwd=cwd, check=False,
    )
    return result.returncode == 0


def resolve_start_point(
    remote: str, default_branch: str, *, cwd: str | Path
) -> str:
    """Pick the best start point for a new worktree branch.

    Prefers ``<remote>/<default_branch>`` (normal case), then a local
    ``<default_branch>``, then ``HEAD`` -- so a repo with no remote (or no
    fetched default branch) still works instead of failing with
    ``fatal: invalid reference: <remote>/<default_branch>``.
    """
    upstream = f"{remote}/{default_branch}"
    if ref_exists(upstream, cwd=cwd):
        return upstream
    if ref_exists(default_branch, cwd=cwd):
        return default_branch
    return "HEAD"


def prepare_worktree_base(
    anchor: str | Path,
    *,
    remote: str,
    default_branch: str,
    fast_forward_anchor: bool = True,
) -> WorktreeBaseResult:
    """Fetch a repository, safely refresh its anchor, and select a fresh base.

    Fetch failure is non-fatal: callers still receive the best last-known
    remote/local/HEAD start point. Anchor advancement reuses
    :func:`fast_forward_worktree` and is attempted only when the anchor is
    checked out on its configured default branch.
    """
    anchor_path = Path(anchor)
    fetched = False
    fetch_error: str | None = None
    if has_remote(remote, cwd=anchor_path):
        try:
            fetch(remote, cwd=anchor_path)
            fetched = True
        except Exception as exc:
            fetch_error = str(exc)

    start_point = resolve_start_point(
        remote,
        default_branch,
        cwd=anchor_path,
    )
    branch = current_branch(anchor_path)
    if not fast_forward_anchor:
        anchor_result = FastForwardResult(False, "disabled")
    elif branch is None:
        anchor_result = FastForwardResult(False, "detached")
    elif branch != default_branch:
        anchor_result = FastForwardResult(False, "non-default-branch")
    else:
        anchor_result = fast_forward_worktree(
            anchor_path,
            remote=remote,
            default_branch=default_branch,
            do_fetch=False,
        )
    return WorktreeBaseResult(
        start_point=start_point,
        fetched=fetched,
        fetch_error=fetch_error,
        anchor=anchor_result,
    )


def create_worktree(
    anchor: str | Path,
    worktree_path: str,
    branch: str,
    start_point: str,
) -> None:
    """Create a new git worktree on a new branch."""
    git(
        "worktree", "add", worktree_path, "-b", branch, start_point, "--quiet",
        cwd=anchor,
    )


def remove_worktree(anchor: str | Path, worktree_path: str) -> bool:
    """Remove a worktree. Returns True on success."""
    result = git(
        "worktree", "remove", worktree_path, "--force",
        cwd=anchor, check=False,
    )
    return result.returncode == 0


def delete_branch(name: str, *, cwd: str | Path, force: bool = False) -> bool:
    """Delete a local branch. Returns True on success."""
    flag = "-D" if force else "-d"
    result = git("branch", flag, name, cwd=cwd, check=False)
    return result.returncode == 0


def get_dirty_files(cwd: str | Path) -> list[str]:
    """Return list of uncommitted changes (porcelain format)."""
    result = git("status", "--porcelain", cwd=cwd, check=False)
    return [ln for ln in result.stdout.splitlines() if ln.strip()]


def get_commits_ahead(
    branch: str, upstream: str, *, cwd: str | Path
) -> list[str]:
    """Return one-line commit log of branch commits not in upstream."""
    result = git(
        "log", "--oneline", f"{upstream}..{branch}",
        cwd=cwd, check=False,
    )
    return [ln for ln in result.stdout.splitlines() if ln.strip()]


def is_clean(*, cwd: str | Path) -> bool:
    """Return True if the working tree has no uncommitted changes."""
    result = git("status", "--porcelain", cwd=cwd, check=False)
    return result.returncode == 0 and not result.stdout.strip()


def squash_branch(
    upstream: str, message: str, *, cwd: str | Path
) -> tuple[bool, str | None]:
    """Squash all commits ahead of *upstream* into one on the current branch.

    Uses soft reset to merge-base, then re-commits.  A backup ref is
    created before the reset and restored on failure.

    Returns ``(ok, reason)``:
      * ``(True, None)``  -- squashed (or the no-op case of 0-1 commits).
      * ``(False, reason)`` -- failed; *reason* is a human-readable diagnostic
        carrying the underlying git failure (stderr/stdout), and the branch has
        been restored to its original commits.  Callers MUST NOT proceed with a
        push on a ``False`` result -- doing so would push the unsquashed
        commits, violating the one-commit-per-worktree invariant.
    """
    mb = git("merge-base", upstream, "HEAD", cwd=cwd, check=False)
    if mb.returncode != 0:
        return False, _git_detail(
            f"could not compute merge-base with {upstream}", mb
        )
    merge_base = mb.stdout.strip()

    count_r = git("rev-list", "--count", f"{merge_base}..HEAD", cwd=cwd, check=False)
    if count_r.returncode != 0:
        return False, _git_detail("could not count commits to squash", count_r)
    count = int(count_r.stdout.strip())
    if count <= 1:
        return True, None  # nothing to squash

    # Save backup ref for rollback
    orig_head = git("rev-parse", "HEAD", cwd=cwd, check=False).stdout.strip()
    git("update-ref", "refs/pre-squash-backup", orig_head, cwd=cwd, check=False)

    reset_r = git("reset", "--soft", merge_base, cwd=cwd, check=False)
    if reset_r.returncode != 0:
        git("reset", "--hard", orig_head, cwd=cwd, check=False)
        git("update-ref", "-d", "refs/pre-squash-backup", cwd=cwd, check=False)
        return False, _git_detail(
            f"git reset --soft {merge_base[:12]} failed", reset_r
        )

    commit_r = git("commit", "-m", message, cwd=cwd, check=False, no_hooks=True)
    if commit_r.returncode != 0:
        git("reset", "--hard", orig_head, cwd=cwd, check=False)
        git("update-ref", "-d", "refs/pre-squash-backup", cwd=cwd, check=False)
        return False, _git_detail(
            "git commit of the squashed tree failed "
            "(a failing commit hook is the common cause)",
            commit_r,
        )

    return True, None


def _git_detail(summary: str, result: object) -> str:
    """Compose a one-line diagnostic from a git result's stderr/stdout."""
    parts = [summary]
    for stream in ("stderr", "stdout"):
        text = getattr(result, stream, "") or ""
        text = text.strip()
        if text:
            parts.append(text)
    return ": ".join(parts)


def delete_backup_ref(*, cwd: str | Path) -> None:
    """Remove the pre-squash backup ref after successful finalization."""
    git("update-ref", "-d", "refs/pre-squash-backup", cwd=cwd, check=False)


def restore_backup_ref(*, cwd: str | Path) -> bool:
    """Restore the worktree branch from the pre-squash backup ref.

    Returns True if restoration succeeded, False if no backup exists.
    """
    ref = git("rev-parse", "refs/pre-squash-backup", cwd=cwd, check=False)
    if ref.returncode != 0:
        return False
    git("reset", "--hard", ref.stdout.strip(), cwd=cwd, check=False)
    git("update-ref", "-d", "refs/pre-squash-backup", cwd=cwd, check=False)
    return True


def get_current_branch(cwd: str | Path) -> str:
    """Return the current branch name."""
    result = git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
    return result.stdout.strip()


def prune_worktrees(*, cwd: str | Path) -> None:
    """Prune stale worktree entries."""
    git("worktree", "prune", cwd=cwd, check=False)


def list_worktree_paths(
    *,
    cwd: str | Path,
    fail_on_error: bool = False,
) -> list[Path]:
    """Return the on-disk paths of every worktree registered on this repo.

    Parses ``git worktree list --porcelain`` -- one ``worktree <path>`` line per
    registered tree, including the main checkout. Returns ``[]`` if the command
    fails unless *fail_on_error* is true, in which case the Git error is raised.
    Used by the garbage collector to tell a real, registered worktree from an
    orphaned on-disk directory left behind by an interrupted/forced removal.
    """
    res = git("worktree", "list", "--porcelain", cwd=cwd, check=False)
    if res.returncode != 0:
        if fail_on_error:
            detail = (res.stderr or res.stdout or "git worktree list failed").strip()
            raise RuntimeError(detail)
        return []
    paths: list[Path] = []
    for line in (res.stdout or "").splitlines():
        if line.startswith("worktree "):
            paths.append(Path(line[len("worktree "):].strip()))
    return paths


def is_branch_merged(
    branch: str,
    target: str,
    *,
    cwd: str | Path,
) -> bool:
    """Check if all commits on *branch* are reachable from *target*.

    Compares tree content (blob-level), not commit identity, so this
    returns True for squash-merged branches too.  Falls back to
    commit-ancestry check when tree comparison isn't possible.
    """
    # Fast path: branch ref doesn't exist locally
    ref_check = git("rev-parse", "--verify", branch, cwd=cwd, check=False)
    if ref_check.returncode != 0:
        return True  # branch already gone -- nothing to protect

    # Check commit ancestry first
    result = git(
        "merge-base", "--is-ancestor", branch, target,
        cwd=cwd, check=False,
    )
    if result.returncode == 0:
        return True

    # Patch-id comparison via `git cherry` -- detects equivalent patches
    # even when commit SHAs differ (squash-merge) and even when the
    # target later modified the same files.
    cherry_r = git("cherry", target, branch, cwd=cwd, check=False)
    if cherry_r.returncode == 0 and cherry_r.stdout.strip():
        unmerged = [ln for ln in cherry_r.stdout.splitlines()
                    if ln.startswith("+")]
        if not unmerged:
            return True

    # Fallback: direct blob comparison for cases git-cherry can't match.
    diff_r = git("diff", "--name-only", branch, target, cwd=cwd, check=False)
    if diff_r.returncode != 0:
        return False  # can't determine -- assume not merged
    changed = [f for f in diff_r.stdout.splitlines() if f.strip()]
    if not changed:
        return True  # identical trees

    # Compare blobs for files the branch changed vs their state on target
    mb = git("merge-base", target, branch, cwd=cwd, check=False)
    if mb.returncode != 0:
        return False
    merge_base = mb.stdout.strip()

    branch_diff = git(
        "diff", "--name-only", merge_base, branch, cwd=cwd, check=False,
    )
    branch_files = [f for f in branch_diff.stdout.splitlines() if f.strip()]
    for file in branch_files:
        b_blob = git("rev-parse", f"{branch}:{file}", cwd=cwd, check=False)
        t_blob = git("rev-parse", f"{target}:{file}", cwd=cwd, check=False)
        if b_blob.stdout.strip() != t_blob.stdout.strip():
            return False

    return True
