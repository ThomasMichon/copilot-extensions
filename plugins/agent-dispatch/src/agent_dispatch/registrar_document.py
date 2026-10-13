"""Shared decoded-document expansion for discovery and configuration validation."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from .registrar import ProfileDeclaration, RegistrarError, load_declaration


def expand_declaration_document(
    document: Mapping,
    *,
    directory: Path,
    repo_root: str | Path | None = None,
    allow_plugin_companion: bool = False,
) -> tuple[ProfileDeclaration, ...]:
    """Validate/expand decoded data without persisting or activating it."""
    from .registrar_discovery import (
        _field_defining_hop_is_cross_repo, _resolve_declaration_paths,
    )

    data = dict(document)
    leaf_forge = data.get("forge")
    leaf_forge = leaf_forge if isinstance(leaf_forge, Mapping) else {}
    this_repo_root = Path(repo_root).expanduser() if repo_root is not None else directory
    inherited_script_fields = set()
    if "extends" in data:
        from .repository_resources import RepositoryResourceSnapshot

        snapshot = RepositoryResourceSnapshot(execution=False)
        try:
            this_repo_root = this_repo_root.resolve()
            # Check the nearest defining hop for each field: an unrelated
            # cross-repo ancestor must not change relative-path provenance.
            for field in ("command", "cwd"):
                if field in leaf_forge:
                    continue
                if _field_defining_hop_is_cross_repo(
                    field, data.get("extends"),
                    base_dir=this_repo_root, repo_root=this_repo_root,
                    snapshot=snapshot,
                ):
                    inherited_script_fields.add(field)
        except (OSError, RuntimeError, ValueError, RegistrarError):
            inherited_script_fields = {
                field for field in ("command", "cwd") if field not in leaf_forge
            }
        from .registrar_recipes import resolve_extends

        base_dir = Path(repo_root).expanduser() if repo_root is not None else directory
        data = resolve_extends(data, base_dir=base_dir, snapshot=snapshot)
    if data.get("kind") == "reviewer-loop":
        from .reviewer_loops import expand_reviewer_loop

        declarations = expand_reviewer_loop(data)
    elif data.get("kind") == "repository-issue-loop":
        from .repository_issue_loops import expand_repository_issue_loop

        declarations = expand_repository_issue_loop(
            data, repo_root=repo_root,
            inherited_script_fields=frozenset(inherited_script_fields),
        )
    elif data.get("kind") == "effort-driver-loop":
        from .effort_driver_loops import expand_effort_driver_loop

        declarations = expand_effort_driver_loop(data, repo_root=repo_root)
    else:
        declarations = (
            load_declaration(data, allow_plugin_companion=allow_plugin_companion),
        )
    return tuple(
        _resolve_declaration_paths(declaration, directory)
        for declaration in declarations
    )
