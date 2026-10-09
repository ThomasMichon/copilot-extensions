"""Source-owned, verified PR replays; never a generic rewrite permission."""

from __future__ import annotations

import json
import hashlib
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from agent_procutil import no_window_kwargs

from . import git_ops, hooks, tracking


@dataclass(frozen=True)
class RebaseProof:
    worktree_id: str
    pr_id: str
    branch: str
    remote_fingerprint: str
    expected_head: str
    published_patch_id: str
    recorded_patch_id: str
    old_base: str
    old_source: str
    new_base: str
    rebased_head: str
    source_head: str
    patches: tuple[str, ...]
    conflict_replays: tuple[tuple[str, str], ...]


def _git(*args: str, cwd: str) -> str:
    result = git_ops.git(*args, cwd=cwd, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def _git_bytes(*args: str, cwd: str, stdin: bytes | None = None) -> bytes:
    cmd = ["git", *args]
    try:
        result = subprocess.run(
            cmd, cwd=cwd, input=stdin, capture_output=True, timeout=30,
            env=git_ops.repository_identity_env(), **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise git_ops.GitError(cmd, 124, f"timed out after {exc.timeout} seconds") from exc
    if result.returncode:
        raise git_ops.GitError(cmd, result.returncode, result.stderr.decode("utf-8", errors="replace"))
    return result.stdout


def _ancestor(old: str, new: str, cwd: str) -> bool:
    return bool(old and new and git_ops.is_commit_ancestor(old, new, cwd=cwd))


def _series(base: str, head: str, cwd: str) -> list[str] | None:
    """Exact ordered patch IDs, including whitespace and binary changes.

    Merge commits and empty/unreadable patches are not evidence of a replay.
    """
    commits = _git("rev-list", "--reverse", "--parents", f"{base}..{head}", cwd=cwd)
    rows = [line.split() for line in commits.splitlines()]
    if any(len(row) != 2 for row in rows):
        return None
    if not rows:
        return []
    diff = _git_bytes(
        "log", "--reverse", "--format=commit %H", "--binary", "--full-index", "--encoding=none",
        "--no-ext-diff", "--no-textconv", "-p", f"{base}..{head}", cwd=cwd,
    )
    output = _git_bytes("patch-id", "--verbatim", cwd=cwd, stdin=diff)
    pairs = [line.split() for line in output.splitlines()]
    if (
        len(pairs) != len(rows)
        or any(
            len(pair) != 2 or pair[1] != row[0].encode("ascii")
            or not re.fullmatch(rb"[0-9a-f]{40,64}", pair[0])
            for pair, row in zip(pairs, rows)
        )
    ):
        return None
    return [pair[0].decode("ascii") for pair in pairs]


def _replay(branch: str, head: str, cwd: str) -> tuple[str, str, str, list[tuple[str, str]]] | None:
    """Git's branch AND HEAD journals must agree on the same completed replay.

    A reset, amend, cherry-pick, or second rewrite after it invalidates the
    evidence; ordinary feedback commits on top are safe.
    """
    branch_rows = _git("reflog", "show", "--format=%H%x09%gs", branch, cwd=cwd).splitlines()
    for index, row in enumerate(branch_rows):
        sha, _, message = row.partition("\t")
        finish = re.fullmatch(
            rf"rebase \(finish\): refs/heads/{re.escape(branch)} onto ([0-9a-f]{{40,64}})",
            message,
        )
        if not finish:
            if not message.startswith("commit: "):
                return None
            continue
        if index + 1 >= len(branch_rows) or not _ancestor(sha, head, cwd):
            return None
        original = branch_rows[index + 1].partition("\t")[0]
        onto = finish.group(1)
        head_rows = _git("reflog", "show", "--format=%H%x09%gs", "HEAD", cwd=cwd).splitlines()
        for i, entry in enumerate(head_rows):
            if entry != f"{sha}\trebase (finish): returning to refs/heads/{branch}":
                # A no-op reset may be journaled only on HEAD, not the branch.
                if not entry.partition("\t")[2].startswith("commit: "):
                    return None
                continue
            steps: list[tuple[str, str]] = []
            for j in range(i + 1, len(head_rows) - 1):
                start_sha, _, action = head_rows[j].partition("\t")
                if action.startswith("rebase (start): checkout "):
                    before = head_rows[j + 1].partition("\t")[0]
                    return (
                        original, onto, sha, list(reversed(steps))
                    ) if before == original and start_sha == onto else None
                if not action.startswith(("rebase (pick): ", "rebase (continue): ")):
                    return None
                steps.append((start_sha, action))
            return None
        return None
    return None


def _conflict_lineage(
    base: str, original: str, onto: str, finished: str,
    old: list[str], new: list[str], applied: list[str],
    steps: list[tuple[str, str]], cwd: str,
) -> tuple[tuple[str, str], ...] | None:
    """Verify every original commit was picked, explicitly continued, or already applied.

    Conflict continuation is a source-owned content decision, not patch equality.
    Only that Git sequencer action may change a patch; author/message identity
    and the one-to-one linear replay must still match. Reword/edit/squash/skip,
    synthesized journals lacking either endpoint, and unmapped commits fail closed.
    """
    old_shas = _git("rev-list", "--reverse", f"{base}..{original}", cwd=cwd).splitlines()
    new_shas = _git("rev-list", "--reverse", f"{onto}..{finished}", cwd=cwd).splitlines()
    if len(old_shas) != len(old) or len(new_shas) != len(new):
        return None
    candidates = list(zip(old_shas, old))
    for patch in applied:
        match = next((i for i, (_, p) in enumerate(candidates) if p == patch), None)
        if match is not None:
            candidates.pop(match)
    if len(candidates) != len(new) or [sha for sha, _ in steps] != new_shas:
        return None
    conflicts = []
    for (old_sha, old_patch), new_sha, new_patch, (_, action) in zip(candidates, new_shas, new, steps):
        identity = "--format=%an%x00%ae%x00%aI%x00%B"
        before = _git_bytes("show", "--encoding=none", "-s", identity, old_sha, cwd=cwd)
        after = _git_bytes("show", "--encoding=none", "-s", identity, new_sha, cwd=cwd)
        if not before or before != after:
            return None
        if old_patch != new_patch:
            if not action.startswith("rebase (continue): "):
                return None
            conflicts.append((old_sha, new_sha))
    return tuple(conflicts)


def verify(record, repo, remote: str, refspec: str, expected: str, *, cwd: str) -> RebaseProof | None:
    """Fail closed unless a tracked private head proves its entire source replay."""
    if record is None or repo is None or not repo.pr.enabled or not expected:
        return None
    if Path(record.worktree_path).resolve() != Path(cwd).resolve():
        return None
    source, sep, dest = refspec.partition(":")
    dest = (dest if sep else source).removeprefix("refs/heads/")
    live = [p for p in record.prs if p.branch == dest and not tracking._pr_is_terminal(p)]
    if len(live) != 1:
        return None
    pr = live[0]
    suffixes = [git_ops.worktree_suffix(record.worktree_id), record.codename]
    protected = {"main", "master", "dev", repo.default_branch, *hooks._protected_branches(cwd)}
    if (
        not pr.pr_id or pr.head_sha != expected or not pr.base_sha or not pr.patch_id
        or dest in protected or source in protected
        or not dest.startswith(("pr/", "feature/"))
        or not any(suffix and (
            dest.endswith("-" + suffix) or dest in (f"pr/{suffix}", f"feature/{suffix}")
        ) for suffix in suffixes)
        or remote != (pr.remote or repo.remote)
    ):
        return None
    owner_branch = record.branch
    current = git_ops._get_current_branch_safe(cwd)
    if (
        owner_branch != f"worktree/{record.worktree_id}"
        or current not in (owner_branch, dest)
        or source not in (owner_branch, dest)
    ):
        return None
    head = _git("rev-parse", "--verify", f"{source}^{{commit}}", cwd=cwd)
    if not head or head != _git("rev-parse", "HEAD", cwd=cwd):
        return None
    replay = _replay(current, head, cwd)
    if replay is None:
        return None
    original, onto, finished, steps = replay
    upstream = _git("rev-parse", f"{repo.remote}/{repo.default_branch}", cwd=cwd)
    if not (
        _ancestor(pr.base_sha, expected, cwd) and _ancestor(expected, original, cwd)
        and pr.base_sha != onto and _ancestor(pr.base_sha, onto, cwd)
        and _ancestor(onto, upstream, cwd) and _ancestor(onto, finished, cwd)
    ):
        return None
    # Older push-changes updated head_sha without refreshing its cached patch_id.
    # Reconstruct that head's actual patch from the pinned objects, and prove its
    # entire source replay below; the stale cache must never authorize a rewrite.
    from .pr_ops import _patch_id

    published_patch = _patch_id(pr.base_sha, expected, cwd=cwd)
    if not published_patch:
        return None
    old = _series(pr.base_sha, original, cwd)
    new = _series(onto, finished, cwd)
    applied = _series(pr.base_sha, onto, cwd)
    if old is None or new is None or applied is None or not old:
        return None
    # Git may omit already-applied patches, but never unexplained source work.
    remaining = list(old)
    for patch in applied:
        if patch in remaining:
            remaining.remove(patch)
    conflicts: tuple[tuple[str, str], ...] = ()
    if new != old and new != remaining:
        lineage = _conflict_lineage(
            pr.base_sha, original, onto, finished, old, new, applied, steps, cwd,
        )
        if lineage is None:
            return None
        conflicts = lineage
    url = _git("remote", "get-url", "--push", remote, cwd=cwd)
    if not url:
        return None
    return RebaseProof(
        record.worktree_id, pr.pr_id, dest, hashlib.sha256(url.encode("utf-8")).hexdigest(),
        expected, published_patch, pr.patch_id, pr.base_sha,
        original, onto, finished, head, tuple(old), conflicts,
    )


def _save(proof: RebaseProof, cwd: str) -> None:
    # Worktree-specific Git metadata, not the shared tracking record or checkout.
    gitdir = _git("rev-parse", "--absolute-git-dir", cwd=cwd)
    if not gitdir:
        raise OSError("Cannot resolve worktree Git metadata")
    tracking._atomic_write(
        Path(gitdir) / "agent-worktrees-pr-rebase.json",
        json.dumps(asdict(proof), sort_keys=True) + "\n",
    )


def record_synced(worktree_id: str, config, cwd: str) -> None:
    """Checkpoint proven syncs; manual rebase recovery verifies the same journals."""
    from . import config as cfg
    record = tracking.load_record(cfg.tracking_dir() / f"{worktree_id}.yaml")
    if record is None:
        return
    pr = record.active_pr()
    if pr is None:
        return
    repo = config.default_repo
    source = git_ops._get_current_branch_safe(cwd) or ""
    proof = verify(
        record, repo, pr.remote or repo.remote, f"{source}:refs/heads/{pr.branch}",
        pr.head_sha, cwd=cwd,
    )
    if proof:
        _save(proof, cwd)


def push(record, repo, remote: str, refspec: str, expected: str, *, cwd: str) -> git_ops.PushResult:
    """The only non-ancestral publish path: a freshly verified owned-PR replay."""
    try:
        proof = verify(record, repo, remote, refspec, expected, cwd=cwd)
        if proof is None:
            return git_ops.PushResult(
                ok=False, stderr="Refusing unproved PR rewrite: source-owned rebase and "
                "preserved patch/base lineage are required. Inspect the PR and rebase journals; "
                "resets, dropped or unexplained changed patches, shared heads and arbitrary refs "
                "are not authorized.",
            )
        _save(proof, cwd)
        # Pin the verified source object; never race a moving local branch.
        from .git_push_transport import push as transport
        result = transport(
            remote, f"{proof.source_head}:refs/heads/{proof.branch}", cwd=cwd,
            force_with_lease_expect=expected,
        )
        if result:
            result.rebase_base_sha = proof.new_base
            result.published_head_sha = proof.source_head
        return result
    except (OSError, git_ops.GitError) as exc:
        return git_ops.PushResult(ok=False, stderr=f"Refusing unproved PR rewrite: {exc}")
