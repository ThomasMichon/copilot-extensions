"""Launch hook and configuration environment construction."""

from __future__ import annotations

import json
import os
from . import config as cfg


def _build_env(
    profile: cfg.CopilotProfile | None,
    session_env: dict[str, str] | None = None,
    work_dir: str | None = None,
    *,
    config: cfg.Config | None = None,
) -> dict[str, str]:
    """Merge automatic launch env, repository session env and profile overrides.

    Path-list profile values append to automatic values. PR hooks use the
    selected config when available; ownership is resolved from CWD or argv.
    """
    env: dict[str, str] = {}
    launch_config = config
    env["COPILOT_CUSTOM_INSTRUCTIONS_DIRS"] = str(cfg.project_dir())
    try:
        launch_config = config if config is not None else cfg.load_config()
        if launch_config.default_repo.pr.enabled:
            env["AGENT_WORKTREES_HOOKS"] = "1"
    except Exception:
        pass
    if session_env:
        env.update(session_env)
    if profile and profile.env:
        path_list_keys = {"COPILOT_CUSTOM_INSTRUCTIONS_DIRS"}
        for key, value in profile.env.items():
            if key in path_list_keys and key in env:
                env[key] = env[key] + os.pathsep + value
            else:
                env[key] = value
    # This is read-data, not authority, and cannot be replaced by repo/profile env.
    from configuration_context import CONTEXT_ENV

    from . import project_state
    from .config_sources import configuration_report

    env[CONTEXT_ENV] = ""
    if launch_config is not None:
        try:
            report = configuration_report(launch_config, base_anchor=work_dir or None)
        except (ValueError, TypeError):
            if launch_config.knowledge_repo or project_state.namespaced():
                report = {
                    "schema": "agent-worktrees.configuration", "version": 1,
                    "error": "configuration-context-unavailable",
                }
            else:
                report = None
        if report is not None:
            env[CONTEXT_ENV] = json.dumps(report, separators=(",", ":"))
    return env
