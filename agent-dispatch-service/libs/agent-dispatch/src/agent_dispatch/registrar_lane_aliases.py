"""Agent-backed-repo lane enforcement helpers: canonical pointer aliases,
the opt-in enforcement switch, and the hardened git-remote derivation probe.

Split out of :mod:`agent_dispatch.registrar_discovery` (module-size cap).
Depends on that module's :class:`~agent_dispatch.registrar_discovery.Pointer`
/ :func:`~agent_dispatch.registrar_discovery.load_pointers` for the pointer
registry itself; this module only adds the lane-identity layer on top.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Escape hatch for :func:`agent_backed_enforcement_enabled` -- set truthy to
#: refuse task creation against a repo lane with no registered ``repo``
#: pointer.
ENFORCE_REGISTERED_REPOS_ENV = "AGENT_DISPATCH_ENFORCE_REGISTERED_REPOS"


def agent_backed_enforcement_enabled() -> bool:
    """Default-off: whether a task's repo lane must match a registered ``repo``
    pointer.

    This is the enforcement switch for the facility's own rule that a dispatch
    task (and, by the same policy, an agent-bridge session target) may only
    name a repo that is actually **agent-backed** -- something on this machine
    has registered it (declared recipes/emitters/evaluators, or just claimed the
    lane) via ``registrar add-pointer ... --kind repo``. A repo with no such
    pointer has no coordinator/supervisor watching it, so a task queued against
    it can sit unclaimed indefinitely.

    **Default off**, opt in via :data:`ENFORCE_REGISTERED_REPOS_ENV` -- many
    legitimate deployments create tasks against ad-hoc/throwaway lanes with no
    registered pointer at all, so enforcing this unconditionally would reject
    otherwise-valid task creation. Enable it explicitly on a coordinator once
    every real lane it serves has a registered pointer.
    """
    value = os.environ.get(ENFORCE_REGISTERED_REPOS_ENV, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


#: Ambient Git environment variables that redirect git's notion of "which
#: repository" or override its config resolution regardless of an explicit
#: ``-C <root>`` -- a stale/leaked ``GIT_DIR`` (etc.), a ``GIT_CONFIG_KEY_*``/
#: ``GIT_CONFIG_VALUE_*`` pair overriding ``remote.origin.url`` directly, or a
#: ``GIT_CONFIG_GLOBAL``/``GIT_CONFIG_SYSTEM`` path override pointing at an
#: entirely different config file that itself defines ``remote.origin.url`` --
#: any of these could make this probe derive an alias for a different
#: repository than ``location``. Cleared on every invocation below; mirrors
#: the comprehensive set ``agent_worktrees.git_ops.repository_identity_env()``
#: already maintains for the same class of probe (not imported directly --
#: plugins don't take runtime dependencies on each other).
_AMBIENT_GIT_ENV_VARS = frozenset({
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CEILING_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_CONFIG",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_NOSYSTEM",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_SYSTEM",
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


def _scrubbed_git_env() -> dict[str, str]:
    """``os.environ`` with every ambient repo/config-selection Git variable
    removed, including the numerically-indexed ``GIT_CONFIG_KEY_*``/
    ``GIT_CONFIG_VALUE_*`` pairs (an exact-name set alone can't match those)."""
    env = os.environ.copy()
    for name in list(env):
        upper = name.upper()
        if (
            upper in _AMBIENT_GIT_ENV_VARS
            or upper.startswith("GIT_CONFIG_KEY_")
            or upper.startswith("GIT_CONFIG_VALUE_")
        ):
            env.pop(name, None)
    return env


def _derive_git_remote_alias(location: Path) -> str | None:
    """Best-effort: the canonical lane for ``location``'s own ``origin`` remote.

    Never raises -- a missing/unreadable git remote just means no alias gets
    auto-derived (the caller must pass ``aliases`` explicitly in that case;
    see :func:`agent_dispatch.registrar_discovery.add_pointer`). Short
    timeout, no shell, no window -- this runs at registration time only,
    never on the task-creation hot path.

    ``git -C <dir>`` walks *upward* to find an enclosing repo -- if
    ``location`` is not itself a repo root (e.g. a plain subdirectory, or one
    nested inside an unrelated outer checkout) this would otherwise silently
    derive that ancestor's remote instead of refusing. Guard against this by
    requiring ``git``'s own resolved toplevel to equal ``location`` exactly
    before trusting its remote. Ambient Git environment variables (including
    ``GIT_CONFIG_KEY_*``/``GIT_CONFIG_VALUE_*`` overrides, which can replace
    ``remote.origin.url`` directly without touching repo-location at all) are
    scrubbed on both calls -- see :func:`_scrubbed_git_env`.
    """
    import subprocess

    from agent_procutil import no_window_kwargs

    def _git(*args: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", "-C", str(location), *args],
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
                shell=False,
                env=_scrubbed_git_env(),
                **no_window_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        return completed.stdout.strip()

    toplevel = _git("rev-parse", "--show-toplevel")
    if not toplevel or Path(toplevel).resolve() != Path(location).resolve():
        return None  # not a repo root -- refuse rather than trust an ancestor repo's remote
    remote = _git("remote", "get-url", "origin")
    if not remote:
        return None
    from .identity import canonicalize_remote

    return canonicalize_remote(remote)


def known_lane_aliases(base: Path | None = None) -> frozenset[str]:
    """Canonical lane identities every registered ``kind: "repo"`` pointer backs.

    A ``repo`` pointer is this coordinator's own record that a repo has
    opted into agent-dispatch (declared recipes/emitters/evaluators, or was
    registered as a dispatch lane) -- i.e. it is **agent-backed**: something on
    this machine is actually watching that repo's tasks. A ``dir`` pointer
    (a bare declaration-document location with no repo identity) does not
    count, and neither does a pointer's own free-form ``name`` (the registrar
    CLI accepts any label there; it is never validated against the repo's
    real identity). Only the pointer's own ``aliases`` -- canonical lane
    strings, explicit or derived from its ``origin`` remote -- count. Used to
    gate task creation against an unregistered lane -- see
    :func:`agent_dispatch.queue_agent_backed_repo.AgentBackedRepoMixin._require_agent_backed_repo`.
    """
    from .registrar_discovery import load_pointers

    return frozenset(
        alias
        for p in load_pointers(base)
        if p.kind == "repo"
        for alias in p.aliases
    )
