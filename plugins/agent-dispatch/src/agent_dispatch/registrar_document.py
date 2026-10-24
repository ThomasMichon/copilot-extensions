"""Shared decoded-document expansion for discovery and configuration validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import os
from pathlib import Path, PurePosixPath, PureWindowsPath

from .registrar import ProfileDeclaration, RegistrarError, load_declaration


def _identity_origin_root(origin: Path | str, leaf_root: Path) -> str:
    """Select identity tiers at the defining source, not the execution root."""
    from . import repo_config
    from .repository_resources import is_repository_resource, split_repository_resource

    if is_repository_resource(origin):
        name, _ = split_repository_resource(origin)
        return f"repo:{name}:."
    directory = Path(origin).expanduser().resolve()
    surface_root = repo_config.repo_root_from_surface_path(directory, "registrar")
    if surface_root is not None:
        return str(surface_root)
    for candidate in (directory, *directory.parents):
        if (
            candidate == leaf_root or (candidate / ".git").exists()
            or (candidate / "plugin.json").is_file()
        ):
            return str(candidate)
    return str(directory)


def qualify_supplier_context(
    declaration: ProfileDeclaration, repository: str, root: Path,
    *, implicit: bool = True, strict_commands: bool = False,
) -> ProfileDeclaration:
    """Keep read-only supplier context named until execution admission."""
    from .repository_resources import is_repository_resource

    def qualify(value: str | None, *, implicit: bool = implicit) -> str | None:
        if is_repository_resource(value):
            return value
        if value is None and not implicit:
            return value
        path = Path(value) if value is not None else root
        if not path.is_absolute():
            if value is not None and (
                PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()
            ):
                return value
            if not implicit:
                return value
            path = root / path
        lexical = Path(os.path.normpath(path))
        try:
            canonical = path.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise RegistrarError(f"could not resolve supplier execution cwd {value!r}: {exc}") from exc
        # Keep source-local links named even when their targets escape the root.
        for candidate in (canonical, lexical):
            try:
                relative = candidate.relative_to(root)
            except ValueError:
                continue
            return f"repo:{repository}:{relative.as_posix()}"
        return value  # An explicit cwd outside the supplier is not its context.

    def qualify_command(command, *, emitter: bool = False, strict: bool = strict_commands):
        if not isinstance(command, (list, tuple)) or not command or not all(
            isinstance(part, str) for part in command
        ):
            return command
        from .command_operands import script_operand_index

        roles = command
        if emitter:
            from .producers.emitter import _render_command

            roles = _render_command(command, change_ref="{change_ref}")
        try:
            dependencies = {0, script_operand_index(roles, strict=strict)}
        except RegistrarError:
            if strict:
                raise
            # Qualification preserves source fences without admitting a Save.
            return [qualify(part, implicit=False) for part in command]
        return [
            qualify(part, implicit=False) if index in dependencies else part
            for index, part in enumerate(command)
        ]

    spec = dict(declaration.spec)
    if declaration.kind == "emitter":
        if "cwd" in spec or implicit:
            spec["cwd"] = qualify(spec.get("cwd"))
        if "command" in spec:
            spec["command"] = qualify_command(spec["command"], emitter=True)
        side_load = spec.get("side_load")
        if isinstance(side_load, Mapping) and "command" in side_load:
            spec["side_load"] = {
                **side_load, "command": qualify_command(side_load["command"], emitter=True),
            }
        if "worker_identity_cwd" in spec:
            spec["worker_identity_cwd"] = qualify(spec["worker_identity_cwd"])
        loop = spec.get("repository_issue_loop")
        if isinstance(loop, Mapping) and isinstance(loop.get("forge"), Mapping):
            forge = dict(loop["forge"])
            if "cwd" in forge:
                forge["cwd"] = qualify(forge["cwd"], implicit=False)
            if "command" in forge:
                forge["command"] = qualify_command(forge["command"])
            spec["repository_issue_loop"] = {**loop, "forge": forge}
    elif declaration.kind == "evaluator":
        evaluator = spec.get("evaluator_spec")
        if not isinstance(evaluator, Mapping) or "scripts" not in evaluator:
            return declaration
        qualified = dict(evaluator)
        if "cwd" in evaluator or implicit:
            qualified["cwd"] = qualify(evaluator.get("cwd"))
        if isinstance(evaluator["scripts"], Mapping):
            qualified["scripts"] = {
                name: qualify_command(
                    command, strict=strict_commands and name == spec.get("evaluator_ref"),
                )
                for name, command in evaluator["scripts"].items()
            }
        spec["evaluator_spec"] = qualified
    else:
        return declaration
    return replace(declaration, spec=spec)


def expand_declaration_document(
    document: Mapping,
    *,
    directory: Path,
    repo_root: str | Path | None = None,
    allow_plugin_companion: bool = False,
    activation=None,
    snapshot=None,
    expected_context: Mapping | None = None,
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
    path_origins: dict[str, Path | str] = {}
    if "extends" in data:
        from .repository_resources import RepositoryResourceSnapshot
        from .registrar_recipe_io import RecipeRevisionError

        snapshot = snapshot or RepositoryResourceSnapshot(execution=False)
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
                    activation=activation,
                    expected_context=expected_context,
                ):
                    inherited_script_fields.add(field)
        except RecipeRevisionError:
            raise
        except (OSError, RuntimeError, ValueError, RegistrarError):
            inherited_script_fields = {
                field for field in ("command", "cwd") if field not in leaf_forge
            }
        from .registrar_recipes import resolve_extends

        base_dir = Path(repo_root).expanduser() if repo_root is not None else directory
        data = resolve_extends(
            data, base_dir=base_dir, snapshot=snapshot, path_origins=path_origins,
            activation=activation,
            expected_context=expected_context,
        )
    if data.get("kind") == "reviewer-loop":
        from .registrar_recipes import _absolutize_emitter_cwd
        from .reviewer_loops import expand_reviewer_loop

        leaf_emitter = document.get("emitter")
        if (
            "emitter.cwd" in path_origins
            and not (isinstance(leaf_emitter, Mapping) and "cwd" in leaf_emitter)
        ):
            normalized = _absolutize_emitter_cwd(
                {"kind": "emitter", "spec": data["emitter"]},
                directory=path_origins["emitter.cwd"],
            )
            data["emitter"] = normalized["spec"]
        declarations = expand_reviewer_loop(data)
    elif data.get("kind") == "repository-issue-loop":
        from .repository_issue_loops import expand_repository_issue_loop

        declarations = expand_repository_issue_loop(
            data, repo_root=repo_root,
            inherited_script_fields=frozenset(inherited_script_fields),
            worker_identity_cwd=(
                _identity_origin_root(path_origins["worker_identity"], this_repo_root)
                if "worker_identity" in path_origins and "worker_identity" not in document
                else None
            ),
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
