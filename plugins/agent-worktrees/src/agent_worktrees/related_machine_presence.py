"""Eagerly record a related repo's presence on the current machine.

Motivated by a control plane's own "related-repo briefing" generator
(agent_worktrees.related_briefing): a repo getting checked out/set up on a
machine should immediately update the bound knowledge repo's own
``related.yaml`` machine-topology fact, so a later session on this same
machine never sees a stale "not available here" claim for a repo that is,
in fact, here -- exactly the class of bug observed live when a consuming
repo was checked out on a machine its own ``related.yaml`` entry never
listed.

Writes only to the **bound knowledge repo**'s own ``related.yaml`` -- never
a control plane's committed, name-free config, and never a repo's own
in-repo config. Never creates a new related entry; only extends an existing
one's ``locus.machines`` list, and only when the operator hasn't explicitly
opted the machine out via ``locus.excluded_machines``.
"""

from __future__ import annotations

from typing import Any

from . import related, state_root


def record_local_presence(
    config: Any,
    repo_name: str,
    machine: str,
    *,
    cwd: str | None = None,
) -> bool:
    """Ensure ``machine`` is recorded as hosting ``repo_name`` locally.

    Returns ``True`` only when a change was actually persisted. A no-op
    (``False``) in every other case:

    * no knowledge repo is bound for this launch repo;
    * ``repo_name``/``machine`` is empty;
    * ``repo_name`` has no existing related entry in the knowledge repo's
      ``related.yaml`` -- this never creates a new entry, only extends one
      the operator already declared;
    * ``machine`` is already listed;
    * ``machine`` is in the entry's ``locus.excluded_machines`` -- the
      operator's own "never add this machine" signal.

    Never raises; any internal failure (unreadable config, an unwritable
    knowledge checkout) degrades to "nothing recorded," matching this
    harness's fail-open posture for best-effort session-start/registration
    conveniences -- this is a courtesy update, never a gate on the caller's
    own success.
    """
    if not repo_name or not machine:
        return False
    try:
        root = state_root.resolve_state_root(config, cwd=cwd)
        if not root.bound or not root.path:
            return False
        anchor = root.path
        entry = related.get_related(anchor, repo_name)
        if entry is None:
            return False
        if machine in entry.locus.machines:
            return False
        if machine in entry.locus.excluded_machines:
            return False
        entry.locus.machines = [*entry.locus.machines, machine]
        related.upsert_related(anchor, entry)
        return True
    except Exception:
        return False


def record_on_adoption(project: str, repo_dir: str) -> bool:
    """Best-effort :func:`record_local_presence` for a repo-adoption CLI path.

    Resolves ``config``/``machine`` from ``repo_dir`` itself so a caller
    (``cmd_register``, ``cmd_register_project_entry``) needs only this one
    call rather than repeating the config-load/machine-detect/try-except
    boilerplate at every adoption call site. Never raises.

    Uses :func:`config.load_project_config`, not a bare
    ``load_config(project=project)``: the latter's ``project`` kwarg only
    overrides the *resolved repo name* used while parsing, not which config
    path gets read -- with no explicit ``path`` it still falls back to
    ``default_config_path()``, which resolves the *currently active*
    project, not ``project`` itself. ``register-project-entry`` (unlike
    ``register``) is intentionally called without an active-project context,
    so that mismatch would raise here and be silently swallowed, and this
    presence update would never happen. ``load_project_config`` sets the
    active project and loads that project's own config path explicitly.

    Prefers the loaded config's own resolved ``machine`` (its tiered
    machine-local > global > detected lookup) over a fresh
    :func:`config.detect_machine` -- redetecting from ``repo_dir`` alone can
    disagree with it (e.g. a canonical machine alias configured for this
    project, or no ``machines.yaml`` at all), and a later session's own
    presence check compares against ``config.machine``, not a raw hostname.
    """
    try:
        from . import config as cfg

        loaded = cfg.load_project_config(project)
        machine = getattr(loaded, "machine", "") or cfg.detect_machine(repo_dir)
        return record_local_presence(loaded, project, machine, cwd=repo_dir)
    except Exception:
        return False
