"""One read-only state-root request using the normal owner's resolution rules."""

from __future__ import annotations

from functools import partial

from . import registry_paths
from .registry_paths import _state_root_request_scope
from .registry_paths import StateRootRequestRefused as StateRootRequestRefused


def _project_for_anchor(anchor):
    from . import config, git_ops, projects_registry, repos

    return projects_registry.project_for_anchor(
        anchor,
        read_projects=lambda: projects_registry.read_projects_registry(
            registry_paths.registry_path("projects.yaml", legacy_root=config.install_dir()),
        ),
        read_repos=repos.read_registry,
        platform=config.detect_platform,
        normalize=git_ops._normalize_wt_path,
    )


def resolve():
    """Resolve cwd/project, layered config and state; revalidate before returning."""
    with _state_root_request_scope():
        from . import config, invocation_project, project_routing, state_root

        previous = config.active_project()
        config.set_active_project(None)
        try:
            invocation_project.resolve_invocation_project(
                ["state-root"],
                None,
                partial(
                    invocation_project._resolve_active_project, reverse_lookup=_project_for_anchor
                ),
                invocation_project._cwd_is_inside_project,
                project_routing._is_no_project_invocation,
            )
            value = state_root.resolve_state_root(
                config.load_config(
                    include_control_plane_related_pr=False,
                    include_knowledge_preferences=False,
                )
            )
        finally:
            config.set_active_project(previous)
    return value
