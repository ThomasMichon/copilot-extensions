"""Launch hook and owner environment construction."""

from __future__ import annotations

import os
from pathlib import Path

from . import config as cfg, tracking, worktree_identity


def _core():
    from . import __main__ as core

    return core


def _self_owner_ref(work_dir: str | None, *, config: cfg.Config | None = None) -> str | None:
    """Qualified ClaimRef of the worktree rooted at ``work_dir`` (None if not one).

    The identity a launched session advertises via ``AGENT_WORKTREES_OWNER_REF``
    so any resource it creates inherits this worktree as owner. A selected
    launch config avoids repeating discovery without changing the identity.
    """
    if not work_dir:
        return None
    try:
        wd = Path(work_dir)
        wid = _core()._worktree_id_from_git(wd) or tracking.find_worktree_id_by_cwd(str(wd))
        if not wid:
            return None
        if config is None:
            config = cfg.load_config()
        try:
            project = cfg.project_name()
        except Exception:
            project = config.repo_name
        session = os.environ.get("COPILOT_AGENT_SESSION_ID") or None
    except Exception:
        return None
    machine = worktree_identity.reference_machine(config, wid, project=project)
    return tracking.format_claim_ref(machine, project, wid, session)


def _build_env(
    profile: cfg.CopilotProfile | None,
    session_env: dict[str, str] | None = None,
    work_dir: str | None = None,
    *,
    config: cfg.Config | None = None,
) -> dict[str, str]:
    """Merge automatic launch env, repository session env and profile overrides.

    Path-list profile values append to automatic values. PR hooks and the
    launched worktree's owner identity use the selected config when available.
    """
    env: dict[str, str] = {}
    env["COPILOT_CUSTOM_INSTRUCTIONS_DIRS"] = str(cfg.project_dir())
    try:
        launch_config = config if config is not None else cfg.load_config()
        if launch_config.default_repo.pr.enabled:
            env["AGENT_WORKTREES_HOOKS"] = "1"
    except Exception:
        pass
    owner_self = (
        _core()._self_owner_ref(work_dir, config=config) if config is not None
        else _core()._self_owner_ref(work_dir)
    )
    if owner_self:
        env["AGENT_WORKTREES_OWNER_REF"] = owner_self
    if session_env:
        env.update(session_env)
    if profile and profile.env:
        path_list_keys = {"COPILOT_CUSTOM_INSTRUCTIONS_DIRS"}
        for key, value in profile.env.items():
            if key in path_list_keys and key in env:
                env[key] = env[key] + os.pathsep + value
            else:
                env[key] = value
    return env
