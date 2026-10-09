"""Publishing a tracked PR's head from ``push-changes`` and ``create-pr``.

A PR's head can live on a remote other than the repo's own: the role-aware
fork-PR flow publishes it to the caller's fork (``pr.fork.remote``), and
``create-pr`` records that remote on the PR (``PRRecord.remote``). Every later
update of the head must go to that same remote -- pushing it to the repo's own
remote instead creates a second branch the PR never sees, and the PR is left
on its old head.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import threading
import time
import urllib.parse
from dataclasses import dataclass, replace
from pathlib import Path

from . import git_ops, output, push_timeout, tracking
from .config import Config, PRConfig, SourceAttribution

#: Why ``push-changes``/``create-pr`` refuse when :func:`push_remote` can't tell.
UNREADABLE_REMOTE = (
    "Couldn't tell which remote holds this PR's head: the remote it was published to "
    "is gone from this checkout, a fork or the repo's own remote couldn't be read in "
    "time, or the branch is on several of them and the provider's PR head didn't single "
    "one out. Nothing was pushed."
)
#: How long one ``ls-remote`` probe may take (``fetch``'s own bound).
PROBE_TIMEOUT = 30.0
#: Long enough for another worktree to finish the longest bounded push it can make
#: while holding the publication lock: ``git_ops.push`` retries once without its auth
#: override after a failure, each attempt bounded by the push timeout; plus buffer
#: for scheduling/cleanup.
PUBLISH_LOCK_ACQUIRE_TIMEOUT_S = 2 * push_timeout.DEFAULT_PUSH_TIMEOUT + 30.0
PUBLISH_LOCK_RETRY_INTERVAL_S = 0.1
#: A scheme's own port: naming it explicitly is still the same destination.
_DEFAULT_PORTS = {"http": 80, "https": 443, "ssh": 22, "git": 9418}


class PublishLockTimeout(TimeoutError):
    """The PR publication lock stayed held past the bounded acquisition budget."""


@dataclass(frozen=True)
class PushTarget:
    remote: str
    head_repo: str = ""
    head_identity: str = ""


def _fork_remotes(repo) -> list[str]:
    """Every fork remote this repo's config can publish through: the base
    ``pr.fork`` and each role override's (``create-pr`` publishes through the
    role-resolved one) that is *enabled* -- ``remote`` defaults to ``fork`` even
    when forking is off, and an unrelated local ``fork`` remote must not count --
    deduplicated in that order."""
    prcfg = getattr(repo, "pr", None)
    roles = sorted((getattr(prcfg, "roles", None) or {}).items())
    blocks = [getattr(prcfg, "fork", None)] + [getattr(o, "fork", None) for _r, o in roles]
    names = [getattr(b, "remote", "") or "" for b in blocks
             if b is not None and getattr(b, "enabled", False)]
    return [n for i, n in enumerate(names) if n and n != repo.remote and n not in names[:i]]


def _identity(url: str, cwd: str) -> str:
    """A remote URL's full destination: ``host[:port]/owner/name`` (scheme, user and
    a scheme's default port aside, so a repo's ssh and https forms agree), or a
    local repository's path."""
    u = url.strip()
    head, sep, rest = u.partition(":")
    if "://" in u:
        parts = urllib.parse.urlsplit(u)
        if parts.scheme == "file":
            try:
                port = parts.port
            except ValueError:
                return "invalid:" + u
            host = (parts.hostname or "").lower()
            if parts.netloc and host != "localhost":
                if parts.username or parts.password or port is not None or not host:
                    return "invalid:" + u
                path = os.path.normcase(os.path.abspath(urllib.parse.unquote(parts.path)))
                return f"file:{host}:{path}"
            return "path:" + os.path.normcase(os.path.abspath(urllib.parse.unquote(parts.path)))
        host, path = (parts.hostname or "").lower(), parts.path
        if parts.scheme.lower() in ("http", "git"):  # plaintext: never the same destination as a secure one
            host = f"{parts.scheme.lower()}://{host}"
        try:
            port = parts.port
        except ValueError:  # not a port: never the same destination as anything
            return "invalid:" + u
        if port is not None and port != _DEFAULT_PORTS.get(parts.scheme.split("+")[-1]):
            host = f"{host}:{port}"
    elif sep and len(head) > 1 and "/" not in head and "\\" not in head:  # [user@]host:path
        host, path = head.rpartition("@")[2].lower(), rest
    else:  # a local path (Windows drive letters included)
        return "path:" + os.path.normcase(os.path.abspath(os.path.join(cwd, u)))
    path = path.strip("/")
    return f"{host}/{path[:-4] if path.endswith('.git') else path}".lower()


def push_slug(remote: str, *, cwd: str) -> str | None:
    """The repo slug ``git push <remote>`` writes to, or None when it can't be read
    or any push URL names another destination than the fetch URL (a separate
    ``pushurl``, even one with the same ``owner/name`` on another host): a tip,
    lease or credential resolved from one must not guard a push to another."""
    fetch = git_ops.git("remote", "get-url", remote, cwd=cwd, check=False)
    push = git_ops.git("remote", "get-url", "--push", "--all", remote, cwd=cwd, check=False)
    url = fetch.stdout.strip() if fetch.returncode == 0 else ""
    slug = git_ops.slug_from_url(url) if url else None
    if not slug or push.returncode != 0:
        return None
    pushes = {_identity(u, cwd) for u in push.stdout.splitlines() if u.strip()}
    return slug if pushes == {_identity(url, cwd)} else None


def push_identity(remote: str, *, cwd: str) -> str:
    """``host/owner/name`` of where ``git push <remote>`` writes (see :func:`push_slug`),
    or "" when it can't be read: the same ``owner/name`` on another host is another
    repository."""
    if not push_slug(remote, cwd=cwd):
        return ""
    fetch = git_ops.git("remote", "get-url", remote, cwd=cwd, check=False)
    return _identity(fetch.stdout.strip(), cwd) if fetch.returncode == 0 else ""


def _tip(remote: str, branch: str, worktree_path: str) -> str | None:
    """*branch*'s tip on *remote*: "" when it isn't there, ``None`` when the
    remote couldn't be read (or not within :data:`PROBE_TIMEOUT`). Probes the
    fully qualified ref and keeps only an exact match: ``ls-remote`` treats its
    pattern as a tail glob (``feature/x`` also matches ``archive/feature/x``)."""
    ref = f"refs/heads/{branch}"
    try:
        out = git_ops.git(*git_ops._auth_config_args(remote, cwd=worktree_path),
                          "ls-remote", remote, ref, cwd=worktree_path,
                          check=False, timeout=PROBE_TIMEOUT, kill_tree=True)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode != 0:
        return None
    for line in out.stdout.splitlines():
        sha, _, name = line.partition("\t")
        if name.strip() == ref:
            return sha.strip()
    return ""


def _provider_head(repo, pr) -> str:
    """The PR's actual head commit, from the provider; "" when it can't be read."""
    number, slug = getattr(pr, "number", None), getattr(pr, "repo", "") or ""
    if not number or "/" not in slug:
        return ""
    try:
        from . import providers

        prcfg = repo.pr
        provider = providers.get_provider(getattr(pr, "provider", "") or prcfg.provider)
        result = provider.get_pull(slug, int(number),
                                   api_base=getattr(prcfg, "api_base", "") or "",
                                   token=providers.account_token_for_slug(slug, prcfg))
        return (getattr(result, "head_sha", "") or "").strip()
    except Exception:
        return ""


def _own_remote(repo, worktree_path: str) -> PushTarget | None:
    """The repo's own remote as the push target, only while ``git push`` writes where
    it fetches from: the push's lease and credential come from the fetch URL, so a
    ``pushurl`` naming another destination is refused (``None``) like a fork's."""
    return PushTarget(repo.remote) if push_slug(repo.remote, cwd=worktree_path) else None


def push_target(repo, pr, worktree_path: str) -> PushTarget | None:
    """The git remote holding *pr*'s head, to push its updates to, read under
    :func:`publish_lock`: the branch tips it compares and the identity it returns
    come from one snapshot, never across a fork setup that repoints the remote
    in between. ``None`` too when the lock can't be had."""
    try:
        with publish_lock(worktree_path):
            return _select_push_target(repo, pr, worktree_path)
    except PublishLockTimeout:
        return None


def _select_push_target(repo, pr, worktree_path: str) -> PushTarget | None:
    """The git remote holding *pr*'s head, to push its updates to.

    The remote ``create-pr`` recorded on the PR, when it isn't the repo's own
    (a fork-headed PR): that one, as long as it still exists in this checkout --
    a config edit or a removed remote never redirects the PR's updates to the
    repo's own remote. A record from before that was kept is resolved from the
    branch's presence: a configured fork remote (base or role override) holding
    it while the repo's own remote doesn't; on several of them -- including the
    state an earlier ``push-changes`` left by pushing a fork-headed PR's update
    to the repo's own remote -- the one whose tip is the PR's actual head, read
    from the provider. ``None`` when it can't be decided (the recorded remote
    is gone, a deciding remote or the provider couldn't be read, no tip
    matches, or the chosen remote's push URL names another repo than its fetch
    URL): guessing could keep pushing updates to a branch the PR never sees.
    """
    recorded = (getattr(pr, "remote", "") or "").strip()
    if recorded:
        if not git_ops.has_remote(recorded, cwd=worktree_path):
            return None
        # The name alone isn't proof: fork setup repoints `fork` when the identity changes,
        # and a repoint to another host can keep the same owner/name.
        want = (getattr(pr, "head_repo", "") or "").lower()
        got = (push_slug(recorded, cwd=worktree_path) or "").lower()
        want_identity = getattr(pr, "head_identity", "") or ""
        identity = push_identity(recorded, cwd=worktree_path) if got else ""
        if not got or (want and got != want) or (want_identity and identity != want_identity):
            return None
        return PushTarget(recorded, got, identity)
    branch = getattr(pr, "branch", "") or ""
    configured = _fork_remotes(repo)
    forks = [f for f in configured if git_ops.has_remote(f, cwd=worktree_path)]
    if not branch or not configured:
        return _own_remote(repo, worktree_path)
    if len(forks) < len(configured):
        # A configured fork remote is gone from this checkout (removed or renamed):
        # the PR's head may live there. A matching SHA on the repo's own remote
        # doesn't prove the head is there (a stray copy can hold the same commit),
        # so nothing is decided until the remote is back.
        return None
    tips = {f: _tip(f, branch, worktree_path) for f in forks}
    if None in tips.values():
        return None
    holding = [f for f, tip in tips.items() if tip]
    if not holding:
        return _own_remote(repo, worktree_path)
    tips[repo.remote] = _tip(repo.remote, branch, worktree_path)
    if tips[repo.remote] is None:
        return None
    if not tips[repo.remote] and len(holding) == 1:
        found = holding[0]
    else:
        head = _provider_head(repo, pr)
        matches = [r for r in [*holding, repo.remote] if head and tips[r] == head]
        found = matches[0] if len(matches) == 1 else None
    # The tips (and the push's lease) were read from the fetch URL: the push must land there too.
    slug = push_slug(found, cwd=worktree_path) if found else ""
    fork = found != repo.remote
    return PushTarget(found, slug if fork else "", push_identity(found, cwd=worktree_path) if fork else "") \
        if found and slug else None


def push_remote(repo, pr, worktree_path: str) -> str | None:
    target = push_target(repo, pr, worktree_path)
    return target.remote if target else None


def head_branch_gone(repo, pr, worktree_path: str) -> bool:
    """Whether *pr*'s head branch is confirmed absent from the remote holding it
    (a fork-headed PR's fork): the remote and the absence check are one snapshot
    under :func:`publish_lock`, since a fork setup repointing the remote between
    them would find the branch "absent" on another fork and retire a live PR.
    Unreadable or undecided is never "gone"."""
    try:
        with publish_lock(worktree_path):
            where = push_remote(repo, pr, worktree_path)
            # The exact ref: `ls-remote <branch>` is a tail glob, so an unrelated
            # `archive/<branch>` would keep a deleted head looking present.
            return where is not None and _tip(where, pr.branch, worktree_path) == ""
    except PublishLockTimeout:
        return False


def _pr_for(record, branch: str):
    """The live tracked PR whose head is *branch*, if any."""
    return next((p for p in (record.prs if record is not None else [])
                 if p.branch == branch and not tracking._pr_is_terminal(p)), None)


#: Why a push is refused when the recorded fork remote was repointed after it was chosen.
REPOINTED = ("The remote holding this PR's head was repointed at another repository "
             "while the push was being prepared. Nothing was pushed; re-run to resolve it again.")


def _publish_lock_timeout_message(timeout: float) -> str:
    return (
        f"Timed out waiting {timeout:g}s for the PR publication lock; "
        "another worktree may still be updating the fork remote or pushing "
        "through it. Nothing was pushed."
    )


def _try_publish_lock(fh) -> bool:
    fh.seek(0, os.SEEK_END)
    if fh.tell() == 0:
        fh.write(b"\0")
        fh.flush()
    fh.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _lock_publish_file(fh, *, timeout: float = PUBLISH_LOCK_ACQUIRE_TIMEOUT_S) -> None:
    deadline = time.monotonic() + timeout
    while True:
        if _try_publish_lock(fh):
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PublishLockTimeout(_publish_lock_timeout_message(timeout))
        time.sleep(min(PUBLISH_LOCK_RETRY_INTERVAL_S, remaining))


def _unlock_publish_file(fh) -> None:
    fh.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


#: The publication locks this thread holds: a nested acquisition (a decision made
#: under the lock that reads a push target, which takes it too) joins the outer one.
_held = threading.local()


@contextlib.contextmanager
def publish_lock(cwd: str, *, acquire_timeout: float | None = None):
    """Serialize changing a remote's URL with pushing through it, across every
    worktree of the clone (they share one ``.git/config``): the lock lives in the
    common git dir. Re-entrant within a thread; other threads and processes wait."""
    common = git_ops.git("rev-parse", "--git-common-dir", cwd=cwd, check=False).stdout.strip()
    path = Path(cwd, common) / "agent-worktrees-publish.lock"
    key = os.path.normcase(str(path.resolve()))
    held = getattr(_held, "paths", None)
    if held is None:
        held = _held.paths = set()
    if key in held:
        yield
        return
    with path.open("a+b") as fh:
        _lock_publish_file(fh, timeout=(
            PUBLISH_LOCK_ACQUIRE_TIMEOUT_S if acquire_timeout is None else acquire_timeout
        ))
        held.add(key)
        try:
            yield
        finally:
            held.discard(key)
            _unlock_publish_file(fh)


def metadata_lock(worktree_id: str, *, project: str | None = None):
    """Serialize explicit publication and manual PR reassignment, not Picker stamps."""
    from . import config as cfg
    path = (cfg.tracking_dir(project) if project else cfg.tracking_dir()) / f"{worktree_id}.pr-authority.yaml"
    return tracking._RecordLock(path, timeout=PUBLISH_LOCK_ACQUIRE_TIMEOUT_S, require_sidecar=True)


def record_rewrite_ownership(config, record: tracking.WorktreeRecord, pr: tracking.PRRecord,
                             branch: str, head_sha: str, identity: str) -> str:
    """Stamp a successful create-pr publication through a fresh, generation-bound RMW."""
    from . import config as cfg
    if not identity:
        return "Publication had no attested destination; rewrite ownership was not recorded."
    path = cfg.tracking_dir(config.repo_name) / f"{record.worktree_id}.yaml"
    try:
        with metadata_lock(record.worktree_id, project=config.repo_name), tracking._RecordLock(path, require_sidecar=True):
            fresh = tracking.load_record(path)
            current = _publication_pr(fresh, pr)
            if (current is None or current.branch != branch or current.head_sha != head_sha
                    or current.state != "open" or current.pr_revision != pr.pr_revision
                    or current.number != pr.number or current.repo.lower() != pr.repo.lower()
                    or current.provider != pr.provider):
                return "Tracked PR changed since publication; rewrite ownership was not recorded."
            owner = f"{record.worktree_id}:{branch}"
            if current.rewrite_owner != owner or current.rewrite_identity != identity:
                current.rewrite_owner = owner
                current.rewrite_identity = identity
                current.pr_revision += 1
                tracking.save_record(fresh)
            pr.rewrite_owner = current.rewrite_owner
            pr.rewrite_identity = current.rewrite_identity
            pr.pr_revision = current.pr_revision
            return ""
    except (OSError, ValueError, TimeoutError) as exc:
        return f"Could not record rewrite ownership: {exc}"


def push_checked(record, remote: str, refspec: str, *, cwd: str,
                 expected_head_repo: str = "", expected_head_identity: str = "",
                 force_with_lease: bool = False,
                 force_with_lease_expect: str | None = None,
                 allow_history_rewrite: bool = False, repo=None) -> git_ops.PushResult:
    """``git_ops.push`` for a PR head, under :func:`publish_lock`: when it goes to
    the PR's recorded fork, that remote must still name the fork's repo
    (``head_repo``) at push time -- another worktree's fork setup could have
    repointed it since it was chosen."""
    pr = _pr_for(record, refspec.rpartition(":")[2].removeprefix("refs/heads/"))
    want = (expected_head_repo or getattr(pr, "head_repo", "") or "").lower()
    # The full host/owner/name, read when the target was chosen (or recorded at
    # publish): the same owner/name on another host is another repository. Every
    # fork push must know it -- a fresh or legacy target as much as a recorded one.
    want_identity = expected_head_identity or (
        getattr(pr, "head_identity", "") if remote == getattr(pr, "remote", "") else "")
    try:
        with publish_lock(cwd):
            got = identity = ""
            if expected_head_repo or remote == getattr(pr, "remote", ""):
                got = (push_slug(remote, cwd=cwd) or "").lower()
                identity = push_identity(remote, cwd=cwd) if got else ""
                if not want or got != want or not want_identity or identity != want_identity:
                    return git_ops.PushResult(ok=False, stderr=REPOINTED)
            elif record is not None:
                got = (push_slug(remote, cwd=cwd) or "").lower()
                identity = push_identity(remote, cwd=cwd) if got else ""
            if getattr(pr, "rewrite_identity", "") and pr.rewrite_identity != identity:
                return git_ops.PushResult(ok=False, stderr=REPOINTED)
            rewrite_args = {"allow_history_rewrite": True} if allow_history_rewrite else {}
            result = git_ops.push(remote, refspec, cwd=cwd, force_with_lease=force_with_lease,
                                  force_with_lease_expect=force_with_lease_expect, **rewrite_args)
            if repo is not None and force_with_lease_expect and result.stderr == (
                f"Refusing: {force_with_lease_expect} not an ancestor."
            ):
                from . import pr_rebase
                result = pr_rebase.push(
                    record, repo, remote, refspec, force_with_lease_expect, cwd=cwd,
                )
            if result:
                result.head_repo = got
                result.head_identity = identity
            return result
    except PublishLockTimeout as exc:
        return git_ops.PushResult(ok=False, stderr=str(exc))


def update_remote(repo, record, branch: str, worktree_path: str, default: str,
                  owner: str = "", head_repo: str = "",
                  head_identity: str = "") -> tuple[str | None, str, str, str]:
    """Where ``create-pr`` pushes *branch*, the fork owner for its ``<owner>:<branch>``
    PR head ("" for the repo's own remote), and that fork's ``head_repo`` and full
    ``host/owner/name`` identity ("" for the repo remote) -- the identity the push
    must find, read when the target was chosen, never re-read later: while a live tracked PR on it has been published,
    the remote holding its head (:func:`push_target`) -- today's role/fork config may
    name another remote, and pushing there would leave the PR on its old head;
    otherwise *default*, *owner*, *head_repo*, and *head_identity*, resolved from config.
    ``(None, "", "", "")`` when the PR's remote can't be told, or a fork can't be fetched."""
    pr = _pr_for(record, branch)
    if pr is None or not (pr.remote or pr.number or pr.head_sha):
        return default, owner, head_repo, head_identity
    target = push_target(repo, pr, worktree_path)
    if target is None:
        return None, "", "", ""
    where = target.remote
    if where == repo.remote:
        return where, "", "", ""
    try:  # create-pr fetched only the repo's own remote; the lease below compares against this one
        git_ops.fetch(where, cwd=worktree_path)
    except git_ops.GitError:
        return None, "", "", ""
    slug = target.head_repo
    # The PR head's owner recorded at publish (an explicit pr.fork.owner survives reruns
    # and later config changes); else the configured one; else the fork's own owner.
    return (where, getattr(pr, "head_owner", "") or owner or slug.partition("/")[0], slug,
            target.head_identity)


def record_remote(pr, remote: str, repo_remote: str, worktree_path: str) -> None:
    """Record on *pr* the remote its head was published to and, for a fork, that fork's
    ``owner/name``, which :func:`push_remote` checks the remote still points at."""
    fork = remote != repo_remote
    pr.remote = remote if fork else ""
    pr.head_repo = (push_slug(remote, cwd=worktree_path) or "") if fork else ""
    pr.head_identity = push_identity(remote, cwd=worktree_path) if fork else ""


def record_remote_identity(pr, remote: str, repo_remote: str, head_repo: str = "",
                           head_identity: str = "", head_owner: str = "") -> None:
    fork = remote != repo_remote
    pr.remote = remote if fork else ""
    pr.head_repo = head_repo if fork else ""
    pr.head_identity = head_identity if fork else ""
    pr.head_owner = (head_owner or pr.head_owner) if fork else ""


def push_history_message(pushed, feature: str, source: str) -> str:
    if getattr(pushed, "rebase_base_sha", ""):
        return f"Published a verified source-owned PR rebase on '{feature}' from '{source}'."
    return (
        f"Preserved the published PR tip on '{feature}' and pushed "
        f"incremental updates from '{source}'."
    )


def record_pushed_head(
    config, record: tracking.WorktreeRecord, worktree_id: str, pushed_pr, head_sha: str,
    *, remote: str = "", head_repo: str = "", head_identity: str = "", rebase_base_sha: str = "",
) -> None:
    """After a successful head push to *remote*: record the new head (and the
    remote, when it isn't the repo's own -- an older record's fork, found by
    :func:`push_remote`), save, then refresh the provider's head observation and
    the source attribution, warning on either failure."""
    if pushed_pr is not None:
        expected = replace(pushed_pr)
        if rebase_base_sha:
            pushed_pr.base_sha = rebase_base_sha
        if pushed_pr.base_sha:
            from .pr_ops import _patch_id
            pushed_pr.patch_id = _patch_id(pushed_pr.base_sha, head_sha, cwd=record.worktree_path)
        pushed_pr.head_sha = head_sha
        pushed_pr.head_observed_at = ""
        pushed_pr.head_observed_api_base = ""
        if pushed_pr.state in ("", "creating"):
            pushed_pr.state = "open"
        if remote and remote != config.default_repo.remote:
            record_remote_identity(pushed_pr, remote, config.default_repo.remote, head_repo, head_identity)
    if pushed_pr is not None:
        persist_publication(config, record, pushed_pr, expected=expected)
    from . import pr_ops

    observation_error = pr_ops.refresh_head_observation(config, record, pushed_pr, head_sha)
    if observation_error:
        output.warn(
            "PR head was pushed, but authoritative provider observation "
            f"failed: {observation_error}"
        )
    attribution_error = pr_ops.refresh_source_attribution(
        worktree_id, config, record, pushed_pr, head_sha,
    )
    if attribution_error:
        output.warn(
            f"PR head was pushed, but source attribution publication "
            f"failed: {attribution_error}"
        )


def persist_publication(config: Config, record: tracking.WorktreeRecord, pr: tracking.PRRecord,
                        *, expected: tracking.PRRecord | None = None) -> None:
    """Persist a successful push's lease through a fresh, revision-checked RMW."""
    from . import config as cfg, pr_publication_state

    path = cfg.tracking_dir(config.repo_name) / f"{record.worktree_id}.yaml"
    fields = ("state", "base_sha", "head_sha", "patch_id", "remote", "head_repo",
              "head_identity", "head_owner", "head_observed_at", "head_observed_api_base")
    with metadata_lock(record.worktree_id, project=config.repo_name), tracking._RecordLock(path, require_sidecar=True):
        fresh = tracking.load_record(path)
        current = _publication_pr(fresh, pr)
        if (current is None or tracking._pr_is_terminal(current)
                or (not pr_publication_state.matches(current, expected) if expected is not None
                    else current.pr_revision != pr.pr_revision)
                or (current.branch, current.repo, current.provider, current.number)
                != (pr.branch, pr.repo, pr.provider, pr.number)):
            raise ValueError("PR head was pushed, but tracking authority changed; inspect and reconcile its lease.")
        if any(getattr(current, field) != getattr(pr, field) for field in fields):
            for field in fields:
                setattr(current, field, getattr(pr, field))
            current.pr_revision += 1
            tracking.save_record(fresh)
        pr_publication_state.copy_pr(pr, current)


def _publication_pr(record: tracking.WorktreeRecord, pr: tracking.PRRecord) -> tracking.PRRecord | None:
    matches = [p for p in record.prs if tracking._pr_identity_match(p, pr)]
    return matches[0] if len(matches) == 1 else None


def prepare_existing_publication(
    record: tracking.WorktreeRecord | None, existing: tracking.PRRecord | None,
    branch: str, prcfg: PRConfig, attribution: SourceAttribution | None,
    *, remote: str = "", cwd: str = "",
) -> tracking.PRRecord | None:
    """Persist a fresh association before a legacy feature-branch push starts."""
    if record is None or existing is not None:
        return existing
    target = tracking.PRRecord(
        branch=branch, provider=prcfg.provider, repo=record.repo or "",
        state="creating", opened_at=tracking._now_iso(),
    )
    previous = next((p for p in reversed(record.prs)
                     if p.branch == branch and tracking._pr_is_terminal(p) and p.head_sha), None)
    if previous is not None:
        identity = previous.rewrite_identity or previous.head_identity
        if not identity or identity != push_identity(remote, cwd=cwd):
            raise ValueError("Prior terminal PR destination is not attested here; reconcile before reusing its branch.")
        target.head_sha, target.base_sha, target.patch_id = previous.head_sha, previous.base_sha, previous.patch_id
        target.remote, target.head_repo, target.head_identity = previous.remote, previous.head_repo, previous.head_identity
        target.head_owner = previous.head_owner
    tracking.stamp_frozen_attribution(
        target, attribution=prcfg.source_attribution if attribution is None else attribution,
        explicit=attribution is not None or prcfg.source_attribution_configured,
    )
    record.prs.append(target)
    tracking.save_record(record)
    return target
