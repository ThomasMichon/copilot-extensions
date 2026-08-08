"""Configuration for the Git-ref lease store (agent-worktrees core).

The lease store coordinates exclusive, cross-machine access to scarce shared
resources (CodeSpaces, cross-repo worktrees, containers, bridges) through atomic
compare-and-swap on Git refs in the **harness's own repository** -- no branches,
no file commits, no working-tree writes, no new service, no new credential.

This adapts David Michon's standalone ``agent-leases`` config
(ThomasMichon/copilot-extensions#180) to agent-worktrees:

* the store repo is **control-plane-derived** -- the bound knowledge/control-plane
  repo's origin when the launch project is a stateless sub-project (so headless
  projects coordinate through the shared harness repo), else the current
  project's default-repo remote -- instead of a separate ``origin`` config key,
  and
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

#: Optional operator override of the store origin URL (a pushable Git URL). When
#: unset the origin is derived from the current project's default-repo remote.
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
                "lease store origin is required; set a default-repo remote or "
                f"the {ORIGIN_ENV} env / --origin override"
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
    2. the bound **control-plane / knowledge repo** (``config.knowledge_repo``),
       if any -- so a *headless sub-project* driven from the control-plane (e.g.
       copilot-extensions bound to dotfiles, which has no store of its own)
       coordinates through the **shared** harness repo rather than its own
       project remote. Its checkout is resolved via the repos registry (the same
       redirect ``machines.yaml`` resolution uses) and its origin drives
       account-scoped auth. Guarded -- any resolution failure falls through;
    3. otherwise the **current project's default repo**: its anchor checkout +
       configured remote, whose URL is the store origin and whose (remote, cwd)
       drive account-scoped auth.

    Steps 2 and 3 coincide when the harness repo is launched **directly** (it is
    its own state home, so ``knowledge_repo`` is empty and the default repo *is*
    the control plane) -- the redirect only changes the target for a stateless
    sub-project bound to a separate knowledge repo.
    """
    override = origin or os.environ.get(ORIGIN_ENV)
    if override and override.strip():
        return override.strip(), None, None

    from . import config as cfg
    from . import git_ops

    conf = cfg.load_config()

    knowledge = (conf.knowledge_repo or "").strip()
    if knowledge:
        try:
            kanchor = cfg._resolve_anchor_from_registry(knowledge, conf.platform)
            if kanchor:
                kurl = git_ops._remote_url("origin", cwd=kanchor)
                if kurl:
                    return kurl, "origin", str(kanchor)
        except Exception:
            pass

    repo = conf.default_repo
    anchor = repo.anchor
    remote = repo.remote or "origin"
    url = git_ops._remote_url(remote, cwd=anchor)
    if not url:
        raise ConfigError(
            f"could not resolve the '{remote}' remote URL for the store repo at "
            f"{anchor}; set {ORIGIN_ENV} or --origin"
        )
    return url, remote, str(anchor)


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
