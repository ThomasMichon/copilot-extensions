"""Configuration for the Git-ref lease store (agent-worktrees core).

The lease store coordinates exclusive, cross-machine access to scarce shared
resources (CodeSpaces, cross-repo worktrees, containers, bridges) through atomic
compare-and-swap on Git refs -- no branches, no file commits, no working-tree
writes, no new service, no new credential. The store repo must be **explicitly
selected**: either the bound knowledge repo or an operator-supplied origin. The
current project's source remote is never an implicit state store. See
:func:`_resolve_store_target`.

This adapts David Michon's standalone ``agent-leases`` config
(ThomasMichon/copilot-extensions#180) to agent-worktrees:

* the store repo is **control-plane-derived** -- the bound knowledge repo's
  origin when one is set (so a shared harness never collects per-user lease
  refs), or explicitly supplied -- instead of silently using the current
  project's source remote, and
* the ref namespace defaults to the **hidden** ``refs/agent-worktrees/leases/v1``
  (invisible to branch/tag UX) instead of a ``refs/heads/`` branch namespace.

``LeaseSettings`` keeps David's protocol-tuning fields verbatim so the CAS
engine (``lease_store.py``) and its ported tests are unchanged, and adds
``auth_remote``/``auth_cwd`` so network git ops authenticate as the store repo's
owner via agent-worktrees' existing cross-account ``http.extraheader`` injection
(the harness multi-account rule) rather than the ambient active gh account.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

#: Hidden ref namespace -- a non-``heads``/non-``tags`` namespace GitHub accepts
#: on push for repos you can write, so leases never appear as branches or tags.
DEFAULT_REF_PREFIX = "refs/agent-worktrees/leases/v1"

#: Optional operator override of the store origin URL (a pushable Git URL).
ORIGIN_ENV = "AGENT_WORKTREES_LEASE_ORIGIN"


class ConfigError(ValueError):
    """Lease configuration is absent or invalid."""


@dataclass(frozen=True)
class LeaseSettings:
    """Validated lease protocol + remote settings.

    ``origin`` is the pushable Git URL of the shared store repo; ``auth_remote``
    /``auth_cwd`` (a remote name + a checkout that has it) drive account-scoped
    auth for the network ops and are optional -- absent (e.g. in tests), the
    ambient credential helper is used.
    """

    origin: str
    ref_prefix: str = DEFAULT_REF_PREFIX
    default_ttl_seconds: int = 3600
    max_ttl_seconds: int = 86400
    clock_skew_seconds: int = 30
    acquire_retries: int = 3
    auth_remote: str | None = None
    auth_cwd: str | None = None

    def __post_init__(self) -> None:
        if not self.origin.strip():
            raise ConfigError(
                "lease store origin is required; bind a private knowledge repo "
                f"or set the {ORIGIN_ENV} env / --origin override"
            )
        # Accept any fully-qualified ref namespace (not only refs/heads/), so the
        # hidden refs/agent-worktrees/leases/* namespace is allowed; keep every
        # other Git-ref safety check from the upstream validator.
        if not self.ref_prefix.startswith("refs/"):
            raise ConfigError("ref_prefix must be a fully-qualified refs/ namespace")
        components = self.ref_prefix.split("/")
        if (
            self.ref_prefix.endswith("/")
            or not re.fullmatch(r"[A-Za-z0-9._/-]+", self.ref_prefix)
            or any(
                not component
                or component.startswith(".")
                or component.endswith(".")
                or component.endswith(".lock")
                for component in components
            )
            or any(token in self.ref_prefix for token in ("..", "@{"))
        ):
            raise ConfigError("ref_prefix is not a safe Git ref prefix")
        if not 1 <= self.default_ttl_seconds <= self.max_ttl_seconds:
            raise ConfigError("default_ttl_seconds must be between 1 and max_ttl_seconds")
        if not 1 <= self.max_ttl_seconds <= 604800:
            raise ConfigError("max_ttl_seconds must be between 1 and 604800")
        if not 0 <= self.clock_skew_seconds <= 3600:
            raise ConfigError("clock_skew_seconds must be between 0 and 3600")
        if not 0 <= self.acquire_retries <= 10:
            raise ConfigError("acquire_retries must be between 0 and 10")

    def ttl(self, requested: int | None) -> int:
        """Return a validated requested or default TTL."""
        ttl = self.default_ttl_seconds if requested is None else requested
        if not 1 <= ttl <= self.max_ttl_seconds:
            raise ConfigError(f"TTL must be between 1 and {self.max_ttl_seconds} seconds")
        return ttl


def _resolve_store_target(
    origin: str | None = None,
) -> tuple[str, str | None, str | None]:
    """Resolve ``(origin_url, auth_remote, auth_cwd)`` for the shared store.

    Resolution order:

    1. an explicit ``origin`` argument or the ``AGENT_WORKTREES_LEASE_ORIGIN``
       env -- a pushable URL used as-is (no auth context; the ambient credential
       helper authenticates);
    2. the bound **knowledge repo** (``config.knowledge_repo``), if any -- a
       stateless harness (``citadel-harness`` and the like) is **shared across
       users**, so its own repo must never accrue per-user lease refs; the
       machine-bound knowledge repo (the operator's private state repo, e.g.
       ``dotfiles``) is the per-user home for them. When a knowledge repo is
       bound it is therefore **authoritative** for the lease store: its checkout
       is resolved via the repos registry (the same redirect ``machines.yaml``
       resolution uses) and its origin drives account-scoped auth. If a bound
       knowledge repo cannot be resolved this **raises** rather than falling back
       to the launch/harness repo -- silently polluting a shared harness with
       per-user (and cross-user-mixed) lease refs is exactly what this redirect
       exists to prevent. (Cross-user coordination through a *shared* store is a
       deliberate future extension, not this fall-back.)
    3. otherwise, fail closed. A source repository's ordinary remote is not an
       implicit coordination-state backend. Self-hosted or single-user setups
       that deliberately want a Git-ref store must select it explicitly with
       ``AGENT_WORKTREES_LEASE_ORIGIN`` or ``--origin``.
    """
    override = origin or os.environ.get(ORIGIN_ENV)
    if override and override.strip():
        return override.strip(), None, None

    from . import config as cfg
    from . import git_ops

    conf = cfg.load_config()

    knowledge = (conf.knowledge_repo or "").strip()
    if knowledge:
        # A bound knowledge repo is AUTHORITATIVE: never silently fall back to
        # the launch/harness repo (a shared stateless harness must not collect
        # per-user lease refs). Any resolution failure raises with remediation.
        _hint = (
            f"register it (agent-worktrees repos add {knowledge} <path>) or set "
            f"{ORIGIN_ENV}/--origin. Refusing to fall back to the harness repo "
            "(a shared harness must not collect per-user lease refs)."
        )
        try:
            kanchor = cfg._resolve_anchor_from_registry(knowledge, conf.platform)
        except Exception as exc:
            raise ConfigError(
                f"lease store: the bound knowledge repo {knowledge!r} could not "
                f"be resolved from the repos registry ({exc}); {_hint}"
            ) from exc
        if not kanchor:
            raise ConfigError(
                f"lease store: the bound knowledge repo {knowledge!r} is not in "
                f"the repos registry on this machine; {_hint}"
            )
        kurl = git_ops._remote_url("origin", cwd=kanchor)
        if not kurl:
            raise ConfigError(
                f"lease store: the bound knowledge repo {knowledge!r} (at "
                f"{kanchor}) has no resolvable 'origin' remote URL; fix its "
                f"origin remote or set {ORIGIN_ENV}/--origin."
            )
        return kurl, "origin", str(kanchor)

    raise ConfigError(
        "lease store is not configured: bind a private knowledge repo or set "
        f"{ORIGIN_ENV}/--origin explicitly. Refusing to use the current "
        "project's source remote as a coordination-state store."
    )


def load_lease_settings(
    *,
    origin: str | None = None,
    default_ttl_seconds: int = 3600,
    max_ttl_seconds: int = 86400,
    clock_skew_seconds: int = 30,
    acquire_retries: int = 3,
    ref_prefix: str = DEFAULT_REF_PREFIX,
) -> LeaseSettings:
    """Build :class:`LeaseSettings` with an anchor-derived (or overridden) origin."""
    url, auth_remote, auth_cwd = _resolve_store_target(origin)
    return LeaseSettings(
        origin=url,
        ref_prefix=ref_prefix,
        default_ttl_seconds=default_ttl_seconds,
        max_ttl_seconds=max_ttl_seconds,
        clock_skew_seconds=clock_skew_seconds,
        acquire_retries=acquire_retries,
        auth_remote=auth_remote,
        auth_cwd=auth_cwd,
    )
