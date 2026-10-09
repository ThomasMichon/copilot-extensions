"""Source-owned, verified PR replays; never a generic rewrite permission."""

from __future__ import annotations

import json
import hashlib
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from agent_procutil import no_window_kwargs

from . import git_ops, hooks, pr_recovery, push_timeout, tracking


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
    result = git_ops.git("--no-replace-objects", *args, cwd=cwd, check=False, isolated_repository=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def _git_bytes(*args: str, cwd: str, stdin: bytes | None = None) -> bytes:
    cmd = ["git", "--no-replace-objects", *args]
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
    """Ordered diagnostic patch IDs, including whitespace and binary changes.

    They do not bind hunk locations; tree reconstruction authorizes the replay.
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


def _merge_tree(parent: str, onto: str, commit: str, cwd: str) -> tuple[str, bool]:
    args = ("--no-replace-objects", "merge-tree", "--write-tree", "--merge-base", parent, onto, commit)
    try:
        result = git_ops.git(*args, cwd=cwd, check=False, timeout=30, isolated_repository=True)
    except subprocess.TimeoutExpired as exc:
        raise git_ops.GitError(["git", *args], 124, f"timed out after {exc.timeout} seconds") from exc
    tree = result.stdout.splitlines()[0] if result.stdout else ""
    if result.returncode not in (0, 1) or not re.fullmatch(r"[0-9a-f]{40,64}", tree):
        raise git_ops.GitError(["git", *args], result.returncode, result.stderr or "No replay tree returned")
    return tree, result.returncode == 1


def _conflict_lineage(
    base: str, original: str, onto: str, finished: str,
    old: list[str], new: list[str],
    steps: list[tuple[str, str]], cwd: str,
) -> tuple[tuple[str, str], ...] | None:
    """Reconstruct every source change at its real location on the new parent.

    Patch IDs are metadata, never permission: they discard hunk locations.
    A clean three-way replay must produce the actual new tree. A no-op tree
    proves already-applied work; only a real conflict with an explicit continue
    may differ, still preserving one-to-one raw author/message provenance.
    """
    old_rows = [row.split() for row in _git(
        "rev-list", "--reverse", "--parents", f"{base}..{original}", cwd=cwd,
    ).splitlines()]
    new_rows = [row.split() for row in _git(
        "rev-list", "--reverse", "--parents", f"{onto}..{finished}", cwd=cwd,
    ).splitlines()]
    if (
        len(old_rows) != len(old) or len(new_rows) != len(new)
        or any(len(row) != 2 for row in [*old_rows, *new_rows])
        or [sha for sha, _ in steps] != [row[0] for row in new_rows]
    ):
        return None
    conflicts: list[tuple[str, str]] = []
    current = onto
    index = 0
    for old_sha, old_parent in old_rows:
        # A stale recorded base can include upstream history later edited again.
        if _ancestor(old_sha, onto, cwd):
            continue
        tree, conflicted = _merge_tree(old_parent, current, old_sha, cwd)
        current_tree = _git("rev-parse", f"{current}^{{tree}}", cwd=cwd)
        if not current_tree:
            return None
        if not conflicted and tree == current_tree:
            continue
        if index >= len(new_rows):
            return None
        new_sha, new_parent = new_rows[index]
        if new_parent != current:
            return None
        identity = "--format=%an%x00%ae%x00%aI%x00%B"
        before = _git_bytes("show", "--encoding=none", "-s", identity, old_sha, cwd=cwd)
        after = _git_bytes("show", "--encoding=none", "-s", identity, new_sha, cwd=cwd)
        if not before or before != after:
            return None
        new_tree = _git("rev-parse", f"{new_sha}^{{tree}}", cwd=cwd)
        if not new_tree or new_tree == current_tree:
            return None
        if conflicted:
            if not steps[index][1].startswith("rebase (continue): "):
                return None
            conflicts.append((old_sha, new_sha))
        elif tree != new_tree:
            return None
        current, index = new_sha, index + 1
    return tuple(conflicts) if index == len(new_rows) and current == finished else None


def verify(record, repo, remote: str, refspec: str, expected: str, *, cwd: str,
           reasons: list[str] | None = None) -> RebaseProof | None:
    """Fail closed unless a tracked private head proves its entire source replay."""
    def refuse(message: str):
        if reasons is not None:
            reasons.append(message)
        return None

    if record is None or repo is None or not repo.pr.enabled or not expected:
        return refuse("A tracked PR repository and its saved expected remote HEAD are required.")
    if Path(record.worktree_path).resolve() != Path(cwd).resolve():
        return refuse("The current checkout is not the owning worktree.")
    source, sep, dest = refspec.partition(":")
    dest = (dest if sep else source).removeprefix("refs/heads/")
    live = [p for p in record.prs if p.branch == dest and not tracking._pr_is_terminal(p)]
    if len(live) != 1:
        return refuse("The destination must match exactly one live PR in the owning record.")
    pr = live[0]
    suffixes = [git_ops.worktree_suffix(record.worktree_id), record.codename]
    protected = {"main", "master", "dev", repo.default_branch, *hooks._protected_branches(cwd)}
    if (
        not pr.pr_id or pr.head_sha != expected
        or dest in protected or source in protected
        or not dest.startswith(("pr/", "feature/", "user/"))
        or not any(suffix and (
            dest.endswith("-" + suffix) or dest in (f"pr/{suffix}", f"feature/{suffix}")
        ) for suffix in suffixes)
    ):
        return refuse("The saved PR identity/head or private suffix-bound destination is invalid.")
    from . import pr_publish
    if remote != (pr.remote or repo.remote) and (
        pr.remote or remote != pr_publish.push_remote(repo, pr, cwd)
    ):
        return refuse("The destination is not the verified remote holding this PR's head.")
    owner_branch = (
        f"worktree/{record.worktree_id}" if record.branch == record.worktree_id else record.branch
    )
    current = _git("symbolic-ref", "--short", "HEAD", cwd=cwd)
    if (
        owner_branch != f"worktree/{record.worktree_id}"
        or current not in (owner_branch, dest)
        or source not in (owner_branch, dest)
    ):
        return refuse("The source must be the canonical owning worktree branch or its tracked private head.")
    head = _git("rev-parse", "--verify", f"{source}^{{commit}}", cwd=cwd)
    if not head or head != _git("rev-parse", "HEAD", cwd=cwd):
        return refuse("The source ref does not identify the current checked-out commit.")
    recovery = pr_recovery.synced(record, pr, expected, head, cwd=cwd)
    replay = (
        (recovery.local_head, recovery.target_head, recovery.synced_head, [])
        if recovery is not None else _replay(current, head, cwd)
    )
    if replay is None:
        return refuse(
            "No completed backed sync or intact source-owned rebase journals. "
            "Run git sync to preserve recovery refs and checkpoint the supported operation."
        )
    original, onto, finished, steps = replay
    base = pr.base_sha or _git("merge-base", expected, onto, cwd=cwd)
    upstream = _git("rev-parse", f"{repo.remote}/{repo.default_branch}", cwd=cwd)
    if not (
        _ancestor(base, expected, cwd)
        and (recovery is not None or _ancestor(expected, original, cwd))
        and base != onto and _ancestor(base, onto, cwd)
        and _ancestor(onto, upstream, cwd) and _ancestor(onto, finished, cwd)
    ):
        return refuse("The recorded published/base objects do not connect to the newer upstream base.")
    # Older push-changes updated head_sha without refreshing its cached patch_id.
    # Reconstruct that head's actual patch from the pinned objects, and prove its
    # entire source replay below; the stale cache must never authorize a rewrite.
    published_diff = _git_bytes(
        "diff", "--no-ext-diff", "--no-textconv", f"{base}..{expected}", cwd=cwd,
    )
    published_ids = _git_bytes("patch-id", "--stable", cwd=cwd, stdin=published_diff).split()
    if not published_ids or not re.fullmatch(rb"[0-9a-f]{40,64}", published_ids[0]):
        return refuse("The saved published head has no reconstructible patch against its base.")
    published_patch = published_ids[0].decode("ascii")
    old = _series(base, original, cwd)
    new = _series(onto, finished, cwd)
    if old is None or new is None or not old:
        return refuse("The source series is empty, unreadable, or contains unsupported merge commits.")
    conflicts = () if recovery is not None else _conflict_lineage(
        base, original, onto, finished, old, new, steps, cwd,
    )
    if conflicts is None:
        return refuse("The manual replay did not preserve source trees and commit identity.")
    url = _git("remote", "get-url", "--push", remote, cwd=cwd)
    if not url:
        return refuse("The verified destination has no readable push URL.")
    return RebaseProof(
        record.worktree_id, pr.pr_id, dest, hashlib.sha256(url.encode("utf-8")).hexdigest(),
        expected, published_patch, pr.patch_id, base,
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
    source = _git("symbolic-ref", "--short", "HEAD", cwd=cwd)
    proof = verify(
        record, repo, pr.remote or repo.remote, f"{source}:refs/heads/{pr.branch}",
        pr.head_sha, cwd=cwd,
    )
    if proof:
        _save(proof, cwd)


def push(record, repo, remote: str, refspec: str, expected: str, *, cwd: str,
         timeout: float = push_timeout.DEFAULT_PUSH_TIMEOUT) -> git_ops.PushResult:
    """The only non-ancestral publish path: a freshly verified owned-PR replay."""
    try:
        reasons: list[str] = []
        proof = verify(record, repo, remote, refspec, expected, cwd=cwd, reasons=reasons)
        if proof is None:
            return git_ops.PushResult(
                ok=False, stderr="Refusing unproved PR rewrite: " + reasons[0],
            )
        _save(proof, cwd)
        # Pin the verified source object; never race a moving local branch.
        from .git_push_transport import push as transport
        result = transport(
            remote, f"{proof.source_head}:refs/heads/{proof.branch}", cwd=cwd,
            force_with_lease_expect=expected, timeout=timeout,
        )
        if result:
            result.rebase_base_sha = proof.new_base
            result.published_head_sha = proof.source_head
        return result
    except (OSError, ValueError, git_ops.GitError) as exc:
        return git_ops.PushResult(ok=False, stderr=f"Refusing unproved PR rewrite: {exc}")
