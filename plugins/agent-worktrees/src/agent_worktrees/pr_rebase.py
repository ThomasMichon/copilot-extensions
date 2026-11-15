"""Source-owned, verified PR replays; never a generic rewrite permission."""

from __future__ import annotations

import json
import hashlib
import re
import subprocess
from dataclasses import asdict, dataclass, replace
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
    transport_expected_head: str = ""


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


def _empty_upstream_commit(commit: str, parent: str, upstream: str, cwd: str) -> bool:
    if not upstream or not _ancestor(commit, upstream, cwd):
        return False
    trees = _git("rev-parse", f"{commit}^{{tree}}", f"{parent}^{{tree}}", cwd=cwd).splitlines()
    return len(trees) == 2 and trees[0] == trees[1] and bool(
        re.fullmatch(r"[0-9a-f]{40,64}", trees[0])
    )


def _commit_rows(base: str, head: str, cwd: str) -> list[list[str]]:
    return [line.split() for line in _git(
        "rev-list", "--reverse", "--parents", f"{base}..{head}", cwd=cwd,
    ).splitlines()]


def _series(
    base: str, head: str, cwd: str, *, upstream: str = "",
    rows: list[list[str]] | None = None,
) -> list[str] | None:
    """Ordered diagnostic patch IDs, including whitespace and binary changes.

    They do not bind hunk locations; tree reconstruction authorizes the replay.
    Only empty commits already reachable from the pinned upstream may lack IDs.
    Merge commits and other empty/unreadable patches are not replay evidence.
    """
    if rows is None:
        rows = _commit_rows(base, head, cwd)
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
    if len(pairs) != len(rows):
        represented = {pair[1] for pair in pairs if len(pair) == 2}
        rows = [
            row for row in rows
            if row[0].encode("ascii") in represented
            or not _empty_upstream_commit(row[0], row[1], upstream, cwd)
        ]
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


def _tree_ids(heads: tuple[str, ...], cwd: str) -> dict[str, str] | None:
    """Read the pinned commits' trees once for this replay decision."""
    if not heads or any(
        not isinstance(head, str) or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", head) is None
        for head in heads
    ):
        return None
    heads = tuple(dict.fromkeys(heads))
    output = _git_bytes(
        "log", "--stdin", "--no-walk=unsorted", "--format=%H %T",
        "--no-notes", "--no-show-signature", "--no-color", cwd=cwd,
        stdin=("".join(f"{head}\n" for head in heads)).encode("ascii"),
    )
    lines = output.split(b"\n")
    if len(lines) != len(heads) + 1 or lines[-1] != b"":
        return None
    rows = [line.split(b" ") for line in lines[:-1]]
    if any(
        len(row) != 2 or row[0] != head.encode("ascii")
        or re.fullmatch(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})", row[1]) is None
        for head, row in zip(heads, rows)
    ):
        return None
    return {head.decode("ascii"): tree.decode("ascii") for head, tree in rows}


def _conflict_lineage(
    base: str, original: str, onto: str, finished: str,
    old: list[str], new: list[str],
    steps: list[tuple[str, str]], cwd: str,
    *,
    old_rows: list[list[str]] | None = None,
    new_rows: list[list[str]] | None = None,
) -> tuple[tuple[str, str], ...] | None:
    """Reconstruct every source change at its real location on the new parent.

    Patch IDs are metadata, never permission: they discard hunk locations.
    A clean three-way replay must produce the actual new tree. A no-op tree
    proves already-applied work; only a real conflict with an explicit continue
    may differ, still preserving one-to-one raw author/message provenance.
    """
    if old_rows is None:
        old_rows = _commit_rows(base, original, cwd)
    if new_rows is None:
        new_rows = _commit_rows(onto, finished, cwd)
    if any(len(row) != 2 for row in [*old_rows, *new_rows]):
        return None
    if len(old_rows) != len(old):
        old_rows = [
            row for row in old_rows
            if not _empty_upstream_commit(row[0], row[1], onto, cwd)
        ]
    if (
        len(old_rows) != len(old) or len(new_rows) != len(new)
        or [sha for sha, _ in steps] != [row[0] for row in new_rows]
    ):
        return None
    trees = _tree_ids((onto, *(row[0] for row in new_rows)), cwd)
    if trees is None:
        return None
    conflicts: list[tuple[str, str]] = []
    current = onto
    index = 0
    for old_sha, old_parent in old_rows:
        # A stale recorded base can include upstream history later edited again.
        if _ancestor(old_sha, onto, cwd):
            continue
        tree, conflicted = _merge_tree(old_parent, current, old_sha, cwd)
        current_tree = trees[current]
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
        new_tree = trees[new_sha]
        if new_tree == current_tree:
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
           reasons: list[str] | None = None, owner: dict | None = None) -> RebaseProof | None:
    """Fail closed unless a tracked private head proves its entire source replay.

    *owner*, when given, receives the owned PR, destination and source head once
    every ownership check has passed, even when replay proof later fails."""
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
    suffix_bound = any(suffix and (
        dest.endswith("-" + suffix) or dest in (f"pr/{suffix}", f"feature/{suffix}")
    ) for suffix in suffixes)
    protected = {"main", "master", "dev", repo.default_branch, *hooks._protected_branches(cwd)}
    if (
        not pr.pr_id or pr.head_sha != expected
        or dest in protected or source in protected
        or not dest.startswith(("pr/", "feature/", "user/"))
    ):
        return refuse("The saved PR identity/head or protected/private destination is invalid.")
    from . import pr_publish
    if remote != (pr.remote or repo.remote) and (
        pr.remote or remote != pr_publish.push_remote(repo, pr, cwd)
    ):
        return refuse("The destination is not the verified remote holding this PR's head.")
    if not suffix_bound:
        from .pr_custom_head import verify_attested
        if not verify_attested(record, repo, pr, remote, expected, cwd=cwd):
            return refuse("The custom PR head lacks matching saved and live publication ownership.")
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
    if owner is not None:
        owner.update(pr=pr, dest=dest, head=head)
    recovery = pr_recovery.synced(record, pr, expected, head, cwd=cwd)
    replay = (
        (recovery.lineage_head, recovery.target_head, recovery.synced_head, [])
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
        _ancestor(base, expected, cwd) and _ancestor(expected, original, cwd)
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
    # Both consumers use the same pinned ranges during this one verification.
    old_rows = _commit_rows(base, original, cwd)
    new_rows = _commit_rows(onto, finished, cwd)
    old = _series(base, original, cwd, upstream=onto, rows=old_rows)
    new = _series(onto, finished, cwd, rows=new_rows)
    if old is None or new is None or not old:
        return refuse("The source series is empty, unreadable, or contains unsupported merge commits.")
    conflicts = () if recovery is not None else _conflict_lineage(
        base, original, onto, finished, old, new, steps, cwd,
        old_rows=old_rows, new_rows=new_rows,
    )
    if conflicts is None:
        return refuse("The manual replay did not preserve source trees and commit identity.")
    from .git_push_transport import captured_urls
    captured = captured_urls(remote)
    url = captured[0] if captured else _git("remote", "get-url", "--push", remote, cwd=cwd)
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
    source = _git("symbolic-ref", "--short", "HEAD", cwd=cwd)
    pr = pr_recovery.selected_pr(worktree_id, source, record)
    if pr is None:
        return
    repo = config.default_repo
    proof = verify(
        record, repo, pr.remote or repo.remote, f"{source}:refs/heads/{pr.branch}",
        pr.head_sha, cwd=cwd,
    )
    if proof:
        _save(proof, cwd)


def push(record, repo, remote: str, refspec: str, expected: str, *, cwd: str,
         timeout: float = push_timeout.DEFAULT_PUSH_TIMEOUT,
         reconcile_head: str = "") -> git_ops.PushResult:
    """The only non-ancestral publish path: a freshly verified owned-PR replay."""
    try:
        reasons: list[str] = []
        owned: dict = {}
        proof = verify(record, repo, remote, refspec, expected, cwd=cwd, reasons=reasons, owner=owned)
        if proof is None:
            if owned:
                return _owner_rewrite(
                    record, repo, remote, expected, owned, reasons[0],
                    cwd=cwd, timeout=timeout, reconcile_head=reconcile_head,
                )
            return git_ops.PushResult(
                ok=False, stderr="Refusing unproved PR rewrite: " + reasons[0],
            )
        lease = expected
        incremental = False
        if reconcile_head:
            from . import pr_publish
            pr = pr_publish._pr_for(record, proof.branch)
            incremental = _ancestor(reconcile_head, proof.source_head, cwd)
            if not (
                re.fullmatch(r"[0-9a-f]{40,64}", reconcile_head)
                and (incremental or (
                    _ancestor(expected, reconcile_head, cwd)
                    and _ancestor(reconcile_head, proof.old_source, cwd)
                ))
                and pr_publish._tip(remote, proof.branch, cwd) == reconcile_head
                and pr_publish._provider_head(
                    repo, pr, expected_ref=proof.branch,
                    expected_identity=pr_publish.push_identity(remote, cwd=cwd),
                ) == reconcile_head
            ):
                return _owner_rewrite(
                    record, repo, remote, expected, owned,
                    "remote head is not independently confirmed source lineage",
                    cwd=cwd, timeout=timeout, reconcile_head=reconcile_head,
                )
            lease = reconcile_head
            proof = replace(proof, transport_expected_head=lease)
        if not _require_objects(remote, proof.branch, (expected, lease, proof.source_head), cwd):
            return git_ops.PushResult(
                ok=False, stderr="Refusing PR rewrite: a replaced head is not available locally to back up.",
            )
        _save(proof, cwd)
        backup(proof.branch, {
            "published": expected, "remote": lease, "source": proof.source_head,
        }, cwd=cwd)
        # Pin the verified source object; never race a moving local branch.
        from .git_push_transport import push as transport
        result = transport(
            remote, f"{proof.source_head}:refs/heads/{proof.branch}", cwd=cwd,
            force_with_lease_expect=None if incremental else lease, timeout=timeout,
        )
        if not result and not reconcile_head and "stale info" in result.stderr:
            from . import pr_publish
            observed = pr_publish._tip(remote, proof.branch, cwd)
            if observed:
                return push(
                    record, repo, remote, refspec, expected,
                    cwd=cwd, timeout=timeout, reconcile_head=observed,
                )
        if result:
            result.rebase_base_sha = proof.new_base
            result.published_head_sha = proof.source_head
        return result
    except (OSError, ValueError, git_ops.GitError) as exc:
        return git_ops.PushResult(ok=False, stderr=f"Refusing unproved PR rewrite: {exc}")


BACKUP_NAMESPACE = "refs/agent-worktrees/backup"
BACKUP_LIMIT = 5
_STAMP = re.compile(r"[0-9]{8}T[0-9]{12}Z")


def _commit_exists(sha: str, cwd: str) -> bool:
    return bool(sha) and git_ops.git(
        "--no-replace-objects", "cat-file", "-e", f"{sha}^{{commit}}",
        cwd=cwd, check=False, isolated_repository=True,
    ).returncode == 0


def backup(branch: str, objects: dict[str, str], *, cwd: str,
           limit: int = BACKUP_LIMIT) -> str:
    """Retain local refs to every head a PR rewrite could orphan, before it runs.

    Writes ``refs/agent-worktrees/backup/<branch>/<UTC stamp>/<label>`` for each
    locally present commit in *objects* and prunes all but the newest *limit*
    stamps for *branch*. Returns the stamp prefix; raises ``GitError`` when a
    ref cannot be written so the rewrite fails closed instead of losing data."""
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    prefix = f"{BACKUP_NAMESPACE}/{branch}/{stamp}"
    for label, sha in objects.items():
        if _commit_exists(sha, cwd):
            git_ops.git("update-ref", f"{prefix}/{label}", sha, cwd=cwd, isolated_repository=True)
    root = f"{BACKUP_NAMESPACE}/{branch}/"
    refs = git_ops.git(
        "for-each-ref", "--format=%(refname)", root, cwd=cwd, check=False, isolated_repository=True,
    ).stdout.split()
    stamps: dict[str, list[str]] = {}
    for ref in refs:
        parts = ref[len(root):].split("/")
        if len(parts) == 2 and _STAMP.fullmatch(parts[0]):
            stamps.setdefault(parts[0], []).append(ref)
    for old in sorted(stamps)[:-limit] if limit > 0 else ():
        for ref in stamps[old]:
            git_ops.git("update-ref", "-d", ref, cwd=cwd, check=False, isolated_repository=True)
    return prefix


def _collision(record, pr, branch: str, remote: str, cwd: str) -> str:
    """Why another worktree may own this PR branch, or ``""`` when none does.

    Scans every adopted project ledger (not just this project's) through the
    shared PR authority check; the caller holds :func:`pr_authority.guard`."""
    from . import config as cfg, pr_authority, pr_publish
    from dataclasses import replace as _replace
    try:
        config = cfg.load_config(project=record.repo)
        identity = pr_publish.push_identity(remote, cwd=cwd)
        pr_authority.assert_exclusive(config, record, _replace(pr, branch=branch), identity=identity)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        return str(exc) or type(exc).__name__
    return ""


def _missing(shas, cwd: str) -> list[str]:
    return [sha for sha in shas if not _commit_exists(sha, cwd)]


def _require_objects(remote: str, branch: str, shas, cwd: str) -> bool:
    """Fetch the PR branch when any head to retain is absent; all must exist."""
    if _missing(shas, cwd):
        try:
            git_ops.fetch(remote, cwd=cwd, branch=branch)
        except (OSError, git_ops.GitError):
            return False
    return not _missing(shas, cwd)


def refreshed_only(tip: str, published: str, source: str, upstream: str, cwd: str) -> bool:
    """True when replacing *tip* cannot drop content the source lacks.

    Either the source already contains *tip*, or *tip* is exactly a clean
    refresh of the published head onto newer upstream content: its tree equals
    the three-way merge of the published head with the upstream point *tip*
    incorporates. Any extra edit -- a conflict resolution, a merge commit's
    manual change, or a pushed fixup -- changes the tree and is refused. This
    costs a constant number of git calls regardless of PR size."""
    if _ancestor(tip, source, cwd):
        return True
    if not (published and upstream and _commit_exists(published, cwd)):
        return False
    point = _git("merge-base", tip, upstream, cwd=cwd)
    base = _git("merge-base", published, point, cwd=cwd) if point else ""
    if not base:
        return False
    merged = _git("merge-tree", "--write-tree", f"--merge-base={base}", point, published, cwd=cwd)
    tree = _git("rev-parse", "--verify", f"{tip}^{{tree}}", cwd=cwd)
    return bool(tree) and merged.splitlines()[:1] == [tree]


def _owner_rewrite(record, repo, remote: str, expected: str, owned: dict, reason: str, *,
                   cwd: str, timeout: float, reconcile_head: str = "") -> git_ops.PushResult:
    """The owning worktree republishes its own rewritten PR branch.

    Replay proof is not required from the owner: every ownership check in
    :func:`verify` already passed. What remains guarded is the destination:
    no other worktree in any adopted project may claim the branch, the remote
    may only hold this worktree's published head (or a provider-confirmed
    clean refresh of it), the push is leased on that exact observed head, and
    every head the rewrite could orphan is retained under
    :data:`BACKUP_NAMESPACE` first."""
    from . import pr_authority

    def refuse(message: str) -> git_ops.PushResult:
        return git_ops.PushResult(ok=False, stderr=f"Refusing PR rewrite: {message} ({reason})")

    try:
        # The caller already holds this worktree's publish lock, and other
        # writers take the authority guard first; wait only briefly so the
        # inverted order can never deadlock, then fail closed.
        with pr_authority.guard(timeout=5):
            return _owner_rewrite_locked(
                record, repo, remote, expected, owned, refuse,
                cwd=cwd, timeout=timeout, reconcile_head=reconcile_head,
            )
    except pr_authority.PublicationAuthorityBusy:
        return refuse("PR ownership is being changed by another process; retry.")


def _owner_rewrite_locked(record, repo, remote, expected, owned, refuse, *,
                          cwd, timeout, reconcile_head) -> git_ops.PushResult:
    from . import pr_publish

    pr, branch, source = owned["pr"], owned["dest"], owned["head"]
    other = _collision(record, pr, branch, remote, cwd)
    if other:
        return refuse(
            f"branch '{branch}' may be owned by another worktree ({other}); "
            "never overwrite another worktree's PR branch."
        )
    upstream = _git("rev-parse", "--verify", f"{repo.remote}/{repo.default_branch}^{{commit}}", cwd=cwd)
    if not upstream:
        return refuse("the upstream default branch is unreadable.")
    if _ancestor(source, upstream, cwd):
        return refuse("the source has no commits beyond the upstream default branch.")
    tip = pr_publish._tip(remote, branch, cwd)
    if not tip:
        return refuse("the remote PR branch is missing or unreadable.")
    if reconcile_head and tip != reconcile_head:
        return refuse("the remote PR head moved during publication.")
    if not _require_objects(remote, branch, (expected, tip, source), cwd):
        return refuse("a head the rewrite would replace is not available locally to back up.")
    if tip != expected:
        if not refreshed_only(tip, expected, source, upstream, cwd):
            return refuse(
                "the remote PR head carries changes this worktree never published "
                "and its source does not contain; integrate them first."
            )
        if pr_publish._provider_head(
            repo, pr, expected_ref=branch,
            expected_identity=pr_publish.push_identity(remote, cwd=cwd),
        ) != tip:
            return refuse("the moved remote PR head is not confirmed as this PR's head.")
    retained = backup(branch, {"published": expected, "remote": tip, "source": source}, cwd=cwd)
    from .git_push_transport import push as transport
    result = transport(
        remote, f"{source}:refs/heads/{branch}", cwd=cwd,
        force_with_lease_expect=tip, timeout=timeout,
    )
    if result:
        result.rebase_base_sha = _git("merge-base", source, upstream, cwd=cwd)
        result.published_head_sha = source
        result.stdout = (result.stdout + "\n" if result.stdout else "") + (
            f"Owner rewrite of '{branch}' published; previous heads retained under {retained}."
        )
    return result
