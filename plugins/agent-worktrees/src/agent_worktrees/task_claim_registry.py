"""External (cross-machine/cross-consumer) discovery for ``task``-kind claims.

ThomasMichon/copilot-extensions#2584's remaining follow-up: the same-machine
half of "a suspended agent-dispatch task's worktree must not be finalized out
from under it" is done (:mod:`agent_dispatch.hibernation_claims` journals a
``task``-kind :class:`tracking.ResourceClaim`, and agent-worktrees'
finalize-preservation gate already blocks on any unsettled claim regardless of
kind). The gap this module closes: a ``task`` claim's holder is an
agent-dispatch task, which may not be inspectable from the machine running the
reclaim sweep -- unlike a ``worktree`` claim (same-machine tracking record) or
a ``codespace``/``container`` claim (already mirrored onto a cross-machine
lease, see :mod:`sweep`'s ``_LEASEABLE_KINDS``).

Rather than invent a new storage engine, a ``task`` claim's disposition is
mirrored onto the **same** Git-ref lease store already proven for
codespace/container (:mod:`lease_store`), just resolved through a **4-tier
origin chain** instead of :mod:`lease_config`'s stricter, bound-knowledge-repo-
only policy (which stays exactly as-is for codespace/container -- this module
never touches it). In priority order, first hit wins:

1. **The bound knowledge repo's remote** (:func:`lease_config._resolve_bound_store_target`,
   reused unmodified) -- a durable, cross-machine/cross-session-visible ledger
   any consumer that can reach that remote can read or write.
2. **The current project's own remote**, when this project is not a stateless
   harness (:attr:`config.RepoConfig.stateless` is false) -- committing a small
   lease ref into its own tracked remote does not violate any statelessness
   invariant there.
3. **A local, uncommitted bare mirror inside this project's own checkout**,
   when the project *is* stateless and no knowledge repo is bound -- a
   never-committed ``.git/agent-worktrees/task-claims.git`` (a plain bare repo;
   Git's remote transport accepts a filesystem path exactly like a URL), so a
   stateless harness's own tree stays clean under ``tools/statelessness_lint.py``
   while the claim is still discoverable *on this machine*.
4. **A machine-local bare mirror** outside any project checkout
   (``<install_dir>/task-claims.git``) -- the last-resort fallback so a fully
   unbound/standalone project still has *somewhere* deterministic to look.

Tiers 1-2 are genuinely cross-machine (any consumer that can reach the same Git
remote sees the same ref). Tiers 3-4 are same-machine-only fallbacks -- they
keep the mechanism deterministic and non-throwing even with no knowledge repo
and no writable project remote, at the cost of cross-machine visibility.
"""

from __future__ import annotations

import logging

from . import config as cfg
from . import git_ops
from .lease_config import ConfigError, DEFAULT_REF_PREFIX, LeaseSettings
from .lease_store import GitLeaseStore, LeaseLost

log = logging.getLogger(__name__)

#: Long-lived TTL for a task-claim lease: a suspended review-loop task can wait
#: on a human for days. The protocol's own maximum (7 days) -- an expired
#: lease's *disposition* is still readable via ``inspect`` regardless of
#: liveness (see :func:`sweep.leaseable_settled`), so expiry only affects
#: whether a subsequent write takes the ``acquire``(takeover) or ``renew``
#: path, never read-side safety.
_TASK_CLAIM_TTL_SECONDS = 604_800

#: Bare-repo mirror path components for the local-fallback tiers (3/4),
#: relative to the project checkout's ``.git`` dir (tier 3) or the shared
#: install root (tier 4).
_LOCAL_MIRROR_DIRNAME = "task-claims.git"


def _project_own_remote(config: cfg.Config) -> str | None:
    """Tier 2: the current project's own remote, iff it is not stateless."""
    try:
        repo = config.default_repo
    except Exception:
        return None
    if repo.stateless:
        return None
    try:
        return git_ops._remote_url(repo.remote, cwd=repo.anchor)
    except Exception:
        return None


def _ensure_bare_mirror(path) -> str | None:
    """Create ``path`` as a bare Git repo if absent; return it as a str origin.

    Idempotent (``git init --bare`` on an already-initialized path is a no-op
    beyond refreshing its own scaffolding) and best-effort -- any failure
    (permissions, missing git, a non-writable filesystem) yields ``None`` so
    the caller can fall through / degrade rather than raise.
    """
    from pathlib import Path

    p = Path(path)
    try:
        if not (p / "HEAD").exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            git_ops.git("init", "--quiet", "--bare", str(p))
    except Exception as exc:
        log.debug("task-claim local mirror init at %s failed: %s", p, exc)
        return None
    return str(p)


def _local_project_mirror(config: cfg.Config) -> str | None:
    """Tier 3: a local, uncommitted bare mirror inside a stateless project's
    own checkout -- only when no knowledge repo is bound (tier 1 unavailable).
    """
    try:
        repo = config.default_repo
    except Exception:
        return None
    if not repo.stateless:
        return None
    from pathlib import Path

    return _ensure_bare_mirror(
        Path(repo.anchor) / ".git" / "agent-worktrees" / _LOCAL_MIRROR_DIRNAME
    )


def _machine_local_mirror() -> str | None:
    """Tier 4: a machine-local bare mirror outside any project checkout."""
    return _ensure_bare_mirror(cfg.install_dir() / _LOCAL_MIRROR_DIRNAME)


def resolve_task_claim_origin(
    config: cfg.Config | None = None,
) -> tuple[str, str | None, str | None]:
    """Resolve ``(origin, auth_remote, auth_cwd)`` for the task-claim mirror.

    Walks the 4-tier chain documented on this module; the first tier that
    resolves wins. Never raises -- tier 4 (a machine-local bare mirror) always
    succeeds barring a genuinely unwritable filesystem, in which case this
    raises :class:`ConfigError` (there is nowhere left to fall back to).
    """
    conf = config or cfg.load_config()

    from . import lease_config

    try:
        return lease_config._resolve_bound_store_target(conf)
    except ConfigError:
        pass

    own_remote = _project_own_remote(conf)
    if own_remote:
        return own_remote, None, None

    local = _local_project_mirror(conf)
    if local:
        return local, None, None

    machine_local = _machine_local_mirror()
    if machine_local:
        return machine_local, None, None

    raise ConfigError(
        "task-claim registry: no tier (bound knowledge repo, this project's "
        "own remote, a local project mirror, or a machine-local mirror) could "
        "be resolved or created."
    )


def load_task_claim_settings(config: cfg.Config | None = None) -> LeaseSettings:
    """Build :class:`LeaseSettings` for the task-claim mirror (4-tier origin)."""
    origin, auth_remote, auth_cwd = resolve_task_claim_origin(config)
    return LeaseSettings(
        origin=origin,
        ref_prefix=DEFAULT_REF_PREFIX,
        default_ttl_seconds=_TASK_CLAIM_TTL_SECONDS,
        max_ttl_seconds=_TASK_CLAIM_TTL_SECONDS,
        auth_remote=auth_remote,
        auth_cwd=auth_cwd,
    )


def set_task_claim_status(
    ref: str,
    status: str,
    *,
    holder: str = "agent-dispatch",
    config: cfg.Config | None = None,
    settings: LeaseSettings | None = None,
) -> bool:
    """Mirror ``status`` (a disposition, e.g. ``active``/``at-rest``/``released``)
    for a ``task``-kind claim onto the resolved cross-machine mirror.

    Best-effort: re-inspects the current lease immediately before writing so no
    persisted fencing token is needed across process boundaries (the caller is
    always the claim's sole legitimate writer at any given moment) -- an absent
    or expired lease is acquired (or taken over); a live one is renewed in
    place. Returns ``True`` on success, ``False`` on any degraded outcome
    (never raises).
    """
    try:
        store = GitLeaseStore(settings or load_task_claim_settings(config))
        current = store.inspect("task", ref)
        if current is None or not current.live:
            store.acquire(
                "task", ref, holder,
                ttl_seconds=_TASK_CLAIM_TTL_SECONDS,
                context={"disposition": status},
            )
        else:
            store.renew(
                "task", ref, current.oid,
                ttl_seconds=_TASK_CLAIM_TTL_SECONDS,
                context={"disposition": status},
            )
        return True
    except (ConfigError, LeaseLost, Exception) as exc:  # noqa: BLE001 -- best-effort
        log.debug("task-claim status mirror write for %s degraded: %s", ref, exc)
        return False
