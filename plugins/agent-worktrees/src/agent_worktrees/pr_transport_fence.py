"""Transport invalidation and operation-scoped finalization observations."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
import hashlib
import inspect
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, ParamSpec, TypeVar
import uuid

if TYPE_CHECKING:
    from .git_ops import PushResult
    from .providers.base import PullResult

P = ParamSpec("P")
R = TypeVar("R")


@dataclass
class Qualification:
    proofs: dict[str, object] = field(default_factory=dict)
    heads: dict[str, tuple[Head, ...]] = field(default_factory=dict)
    protected: set[str] = field(default_factory=set)
    held_heads: set[Head] = field(default_factory=set)
    cleanup: bool = False


current: ContextVar[Qualification | None] = ContextVar("pr-finalization-qualification", default=None)


class QualificationRefused(ValueError):
    """Pre-mutation qualification failed; no native outcome is implied."""


@contextmanager
def qualification_lease(guard: AbstractContextManager[None]) -> Iterator[None]:
    with ExitStack() as lifetime:
        try:
            lifetime.enter_context(guard)
        except (OSError, ValueError, TimeoutError) as exc:
            raise QualificationRefused(str(exc)) from exc
        yield


@dataclass(frozen=True)
class Head:
    identity: str
    branch: str

    def __post_init__(self) -> None:
        from .pr_publish import canonical_head_identity

        object.__setattr__(self, "identity", canonical_head_identity(self.identity).casefold())

    @property
    def directory(self) -> Path:
        from . import config as cfg
        key = hashlib.sha256((self.identity.casefold() + "\0" + self.branch).encode("utf-8")).hexdigest()
        return cfg.install_dir() / "pr-publication-heads" / key

    @contextmanager
    def guard(self, *, timeout: float = 0) -> Iterator[None]:
        from . import pr_publish
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / "transport.lock").open("a+b") as handle:
            pr_publish._lock_publish_file(handle, timeout=timeout)
            try:
                yield
            finally:
                pr_publish._unlock_publish_file(handle)


def generation(head: Head, *, initialize: bool = False) -> str:
    """Caller holds this head's fence; the token is not publication permission."""
    from . import tracking

    path = head.directory / "generation"
    try:
        value = path.read_text(encoding="ascii")
    except FileNotFoundError:
        if not initialize:
            raise ValueError("Publication generation evidence is missing.") from None
        value = uuid.uuid4().hex
        tracking._atomic_write(path, value)
    if len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("Publication generation evidence is malformed.")
    return value


def _targets(remote: str, refspec: str, cwd: str, lifetime: ExitStack, *, timeout: float) -> tuple[tuple[Head, ...], str]:
    from . import git_acquisition_budget, git_ops, git_push_binding, pr_publish
    from .git_push_transport import captured_urls, pinned_destination
    branch = refspec.rpartition(":")[2] if ":" in refspec else refspec.removeprefix("+")
    if any(c.isspace() or c in "*?\0\r\n" for c in branch):
        raise ValueError("Publication fencing requires one exact ref.")
    if branch.startswith("refs/") and not branch.startswith("refs/heads/"):
        return (), refspec
    branch = branch.removeprefix("refs/heads/")
    if branch == "HEAD":
        branch = git_ops.git(
            "symbolic-ref", "--short", "HEAD", cwd=cwd, isolated_repository=True,
            timeout=10, kill_tree=True,
        ).stdout.strip()
    if not branch or branch.startswith("-") or any(c.isspace() or c in "*?\0\r\n" for c in branch):
        raise ValueError("Publication fencing requires one exact head.")
    urls = captured_urls(remote)
    if not urls:
        with pr_publish.publish_lock(cwd, acquire_timeout=git_acquisition_budget.remaining_timeout(timeout)):
            literal = "://" in remote or ":" in remote or Path(remote).is_absolute() or remote.startswith(("./", "../", ".\\", "..\\"))
            if not literal:
                fetch, pushes, _target = pr_publish._remote_snapshot(remote, cwd=cwd)
                literal = bool((fetch.returncode or pushes.returncode) and Path(cwd, remote).exists())
            if literal:
                alias = f"agent-worktrees-capture-{uuid.uuid4().hex}"
                prefix = ("-c", f"remote.{alias}.url={remote}")
                fetch = git_ops.git(*prefix, "remote", "get-url", alias, cwd=cwd,
                                    isolated_repository=True, timeout=10, kill_tree=True)
                pushes = git_ops.git(*prefix, "remote", "get-url", "--push", "--all", alias,
                                     cwd=cwd, isolated_repository=True, timeout=10, kill_tree=True)
                settings: tuple[tuple[str, str], ...] = ()
            else:
                settings = pr_publish._transport_settings(remote, cwd=cwd)
            urls = tuple(pushes.stdout.splitlines())
            if fetch.returncode or pushes.returncode or not fetch.stdout.strip() or not urls or any(not u.strip() for u in urls):
                raise ValueError("The exact push destinations are unavailable.")
        lifetime.enter_context(pinned_destination(remote, urls, settings, fetch_url=fetch.stdout.rstrip("\r\n")))
    destination = git_push_binding.destination(
        refspec.rpartition(":")[2] if ":" in refspec else refspec.removeprefix("+"),
        urls, cwd=cwd, timeout=timeout,
    )
    if destination.startswith("refs/") and not destination.startswith("refs/heads/"):
        return (), refspec.partition(":")[0] + ":" + destination
    if destination.startswith("refs/heads/"):
        branch = destination.removeprefix("refs/heads/")
    identities = {pr_publish._identity(url, cwd) for url in urls}
    if not identities or "" in identities:
        raise ValueError("Publication destination identity is unavailable.")
    source = refspec.partition(":")[0] if ":" in refspec else refspec
    resolved = f"{source}:refs/heads/{branch}"
    return tuple(sorted({Head(identity, branch) for identity in identities}, key=lambda h: h.directory)), resolved


def publication(function: Callable[P, PushResult]) -> Callable[P, PushResult]:
    signature = inspect.signature(function)
    @wraps(function)
    def fenced(*args: P.args, **kwargs: P.kwargs) -> PushResult:
        from . import git_acquisition_budget, publication_deadline, push_timeout, tracking
        from .git_ops import GitError, PushResult
        lifetime = ExitStack()
        try:
            invocation = signature.bind(*args, **kwargs)
            bound = invocation.arguments
            requested = bound.get("timeout")
            timeout = publication_deadline.validate(
                push_timeout.DEFAULT_PUSH_TIMEOUT if requested is None else requested,
            )
            with git_acquisition_budget.acquisition_budget(time.monotonic() + timeout):
                heads, bound["branch"] = _targets(
                    bound["remote"], bound["branch"], str(bound["cwd"]), lifetime, timeout=timeout,
                )
                for head in heads:
                    lifetime.enter_context(head.guard(
                        timeout=publication_deadline.validate(git_acquisition_budget.remaining_timeout(timeout)),
                    ))
        except (GitError, subprocess.SubprocessError):
            lifetime.close()
            return PushResult(ok=False, stderr="Cannot qualify the exact publication destination/head before transport.")
        except (OSError, ValueError, TimeoutError) as exc:
            lifetime.close()
            return PushResult(ok=False, stderr=f"Publication transport is fenced: {exc}")
        try:
            try:
                for head in heads:
                    tracking._atomic_write(head.directory / "generation", uuid.uuid4().hex)
            except OSError as exc:
                return PushResult(ok=False, stderr=f"Publication invalidation evidence is unavailable: {exc}")
            return function(*invocation.args, **invocation.kwargs)
        finally:
            lifetime.close()
    return fenced


def qualification_scope(function: Callable[P, R]) -> Callable[P, R]:
    @wraps(function)
    def scoped(*args: P.args, **kwargs: P.kwargs) -> R:
        token = current.set(Qualification())
        try:
            return function(*args, **kwargs)
        finally:
            current.reset(token)
    return scoped


def provider_creation(function: Callable[P, PullResult]) -> Callable[P, PullResult]:
    signature = inspect.signature(function)
    @wraps(function)
    def fenced(*args: P.args, **kwargs: P.kwargs) -> PullResult:
        from .pr_provider_admission import creation_guard
        from .providers.base import CreationRefused, ProviderError

        bound = signature.bind(*args, **kwargs).arguments
        with ExitStack() as lifetime:
            try:
                lifetime.enter_context(creation_guard(bound["self"], bound["scope"], token=bound.get("token")))
            except (ProviderError, OSError, ValueError, TimeoutError) as exc:
                raise CreationRefused(str(exc)) from exc
            return function(*args, **kwargs)
    return fenced


@contextmanager
def cleanup_lifecycle_guard(path: Path) -> Iterator[None]:
    """Re-enter only the current qualification's lease, never an unrelated launch."""
    from . import worktree_lifecycle

    scope = current.get()
    options = {"reentrant": True} if scope is not None and str(path.resolve()) in scope.protected else {}
    with worktree_lifecycle.guard(path, **options):
        yield


@contextmanager
def cleanup_guard(record: object, repo: object) -> Iterator[None]:
    """Never perform remote I/O while cleanup owns the ledger/finalization locks."""
    with qualification_lease(cleanup_guards([(record, repo)])):
        yield


@contextmanager
def cleanup_guards(owners: list[tuple[object, object]]) -> Iterator[None]:
    """Fence the union once, then locally revalidate every affected owner."""
    from . import pr_creation_reconcile, retirement_safety, tracking
    from .config import RepoConfig
    selected = []
    for record, repo in owners:
        if isinstance(record, tracking.WorktreeRecord):
            retirement_safety.require_publication_settled(record)
        if not isinstance(record, tracking.WorktreeRecord) or not any(
            pr_creation_reconcile.requires_qualification(pr) for pr in record.prs
        ):
            continue
        if not isinstance(repo, RepoConfig):
            raise ValueError("Retirement cleanup requires the exact repository policy.")
        selected.append((record, repo, str(record.yaml_path.resolve())))
    if not selected:
        yield
        return
    scope = current.get()
    if scope is None:
        raise ValueError("Retirement cleanup has no operation-scoped qualification.")
    heads: set[Head] = set()
    for _record, _repo, key in selected:
        captured = scope.heads.get(key)
        if not captured:
            raise ValueError("Retirement prequalification is missing; retry before cleanup.")
        heads.update(captured)
    acquired = heads - scope.held_heads
    with ExitStack() as lifetime:
        for head in sorted(acquired, key=lambda h: h.directory):
            lifetime.enter_context(head.guard())
        was_cleanup, was_protected = scope.cleanup, set(scope.protected)
        scope.held_heads.update(acquired)
        scope.cleanup = True
        try:
            for record, repo, key in selected:
                error = pr_creation_reconcile.finalization_error(record, repo)
                if error:
                    raise ValueError(error)
                scope.protected.add(key)
            yield
        finally:
            scope.held_heads.difference_update(acquired)
            scope.protected = was_protected
            scope.cleanup = was_cleanup
