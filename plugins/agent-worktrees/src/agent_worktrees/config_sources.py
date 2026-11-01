"""Ordered config-read overlays, separate from the personal-state write root."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from dropin_registry import ScanAuthority

from . import config as cfg
from .acquisition_report import AcquisitionReport, SourceObservation, failed, observe


@dataclass(frozen=True)
class ConfigSource:
    """One checkout that contributes ``.agent-*`` config for a launch context."""

    anchor: str
    """Absolute path to the checkout supplying config (``related.yaml``,
    ``machines.yaml``, ...)."""
    origin: str
    """``"harness"`` (base/launch repo), ``"machine"`` (machine-local project
    root), or ``"knowledge"`` (the bound knowledge repo's config overlay)."""
    tier: str | None = field(default=None, kw_only=True, compare=False)
    directory_form: bool = field(default=False, kw_only=True, compare=False)


def _default_anchor(config: cfg.Config) -> str | None:
    try:
        repo_cfg = config.default_repo
    except KeyError:
        return None
    return repo_cfg.anchor if repo_cfg else None


def config_source_anchors(
    config: cfg.Config,
    *,
    base_anchor: str | None = None,
    cwd: str | None = None,
    family: str = "configuration",
) -> list[ConfigSource]:
    """Ordered ``.agent-*`` config sources for the current launch context.

    Ordinary related configuration uses base -> explicitly bound knowledge ->
    validated machine-wide ``related.yaml`` directory -> exact project-local
    root. ``directory_form`` marks the native machine file; it is not a checkout.
    ``tier`` is read-data and never a policy-trust classification.

    This is the config-READ axis, distinct from the **state-root** (the personal-
    state WRITE destination): it only reuses the state-root resolver to LOCATE the
    knowledge checkout. A repo with neither a machine-local root nor a knowledge
    binding yields just its own anchor, like the pre-overlay single-anchor path.

    ``base_anchor`` is an explicit base (a ``--repo`` target or the control-plane
    anchor; defaults to the git root of ``cwd``, then the launch repo's anchor).
    Returns the sources base first; empty only when no base anchor resolves.
    ``family="registry"`` retains the existing protected machine-identity source
    selection; ordinary preference precedence does not reorder that authority.
    """
    return _config_source_anchors(config, base_anchor=base_anchor, cwd=cwd, family=family)


def knowledge_config_source(
    config: cfg.Config, *, base_anchor: str | None = None, cwd: str | None = None,
) -> ConfigSource | None:
    """Resolve the existing bound knowledge source without choosing a machine root."""
    return _knowledge_config_source(config, base_anchor=base_anchor, cwd=cwd)


def configuration_context(
    config: cfg.Config, *, base_anchor: str | None = None, cwd: str | None = None,
) -> dict:
    """Read owner-qualified composer context, without enrollment or state writes.

    Legacy registrations use the same normalized remote digest as namespaced
    registrations; an installation cell never supplies repository identity.
    """
    from configuration_layers import ConfigurationError, plan_layers

    from . import project_state, registry_paths, state_root

    if not isinstance(config.knowledge_repo, str):
        raise ConfigurationError("knowledge binding must be an owner-resolved repository name")
    if config.knowledge_repo:
        resolved = state_root.resolve_state_root(
            config, repo_override=config.knowledge_repo, cwd=cwd,
        )
        if not resolved.bound or not resolved.path:
            raise ConfigurationError("explicit knowledge binding is unresolved")
    else:
        resolved = None
    name = config.repo_name
    normalized = project_state._normalized_repository_remote(name)
    repository_id = "repo-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    installation = registry_paths.installation_context()
    cell = str(installation["cellRoot"]) if installation else str(registry_paths.registry_root())
    base = base_anchor or config.default_repo.anchor
    context = {
        "repository_id": repository_id,
        "execution_machine": config.machine,
        "installation_cell": cell,
        "binding": "unbound",
        "base_root": os.path.abspath(base),
    }
    if resolved is not None and os.path.abspath(resolved.path) != os.path.abspath(base):
        context["binding"] = "bound"
        context["knowledge_root"] = os.path.abspath(resolved.path)
    plan_layers(context, [])
    return context


def configuration_report(
    config: cfg.Config, *, base_anchor: str | None = None, cwd: str | None = None,
) -> dict:
    """Project only resolved configuration data; never custody or credentials."""
    context = configuration_context(config, base_anchor=base_anchor, cwd=cwd)
    return {
        "schema": "agent-worktrees.configuration",
        "version": 1,
        "context": context,
        "repository_machine_root": str(cfg.project_dir(config.repo_name)),
    }


def compose_ordinary_preferences(
    config, base, knowledge, global_values, local_values, selected, *,
    global_path=None, local_path=None,
):
    """Use the shared plan for registered ordinary launch preferences only."""
    from configuration_layers import ConfigurationError, compose

    from . import project_state

    try:
        context = configuration_context(config)
    except ConfigurationError:
        raise
    except (ValueError, TypeError):
        if project_state.namespaced():
            raise
        # Explicit legacy config files historically need no repository registry.
        # Preserve that native unscoped path, without manufacturing scoped IDs.
        return None
    roots = {
        "base": context["base_root"],
        "knowledge": context.get("knowledge_root"),
    }
    layers = [{
        "id": "defaults", "tier": "defaults", "origin": "defaults",
        "values": {"headless": False, "auto_fast_forward": True},
    }]
    for layer_id, tier, origin, raw in (
        ("base", "repository", "base", base),
        ("knowledge", "repository", "knowledge", knowledge),
        ("machine-wide", "machine", "local_machine", global_values),
        ("project-local", "repository_machine", "repository_machine", local_values),
        ("selected-repository", "repository_machine", "repository_machine", selected),
    ):
        if origin == "knowledge" and context["binding"] != "bound":
            continue
        layer = {
            "id": layer_id, "tier": tier, "origin": origin,
            "values": {
                key: raw[key] for key in cfg._KNOWLEDGE_OVERLAY_TOP_KEYS
                if key != "profile_assignment" and key in raw
            },
            "installation_cell": context["installation_cell"],
        }
        if tier in ("repository", "repository_machine"):
            layer["repository_id"] = context["repository_id"]
        if tier in ("machine", "repository_machine"):
            layer["execution_machine"] = context["execution_machine"]
        if roots.get(origin):
            root = Path(roots[origin])
            candidates = (
                cfg.inrepo_config_path(root), cfg.legacy_inrepo_config_path(root),
                root / cfg.INREPO_CONFIG_FILENAME,
            )
            source_path = next((path for path in candidates if path.is_file()), None)
            if source_path is not None:
                layer["source"] = {
                    "root": str(root), "path": str(source_path.relative_to(root)),
                }
        elif global_path is not None and layer_id == "machine-wide":
            source_path = Path(global_path).absolute()
            layer["source"] = {
                "root": str(source_path.parent), "path": source_path.name,
            }
        elif local_path is not None and tier == "repository_machine":
            source_path = Path(local_path).absolute()
            layer["source"] = {
                "root": str(source_path.parent), "path": source_path.name,
            }
        layers.append(layer)
    return compose(context, layers)


def ordinary_preference_values(*layers):
    """Defaults < repository/knowledge < machine < exact preferences.

    Profile-assignment admission remains in its existing native policy parser.
    """
    merged = {}
    for layer in layers:
        merged = cfg._deep_merge(merged, {
            key: layer[key] for key in cfg._KNOWLEDGE_OVERLAY_TOP_KEYS if key in layer
        })
    return merged


def apply_scoped_preferences(config, scoped):
    if scoped is None:
        return config
    values = scoped["values"]
    return replace(
        config, headless=bool(values.get("headless", False)),
        auto_fast_forward=bool(values.get("auto_fast_forward", True)),
        ordinary_provenance=scoped["provenance"],
    )


def configuration_request_valid(args):
    import sys

    if args.pair or args.conduct or args.repo or not args.json:
        print("--configuration requires --json and excludes --pair/--conduct/--repo", file=sys.stderr)
        return False
    return True


def emit_configuration_report(config, *, cwd=None):
    """Emit the versioned read-data report with the existing refusal exit codes."""
    import json
    import sys

    try:
        report = configuration_report(config, cwd=cwd)
    except ValueError as error:
        print(f"Configuration context unavailable: {error}", file=sys.stderr)
        return 126 if config.knowledge_repo else 3
    print(json.dumps(report, indent=2))
    return 0


def _config_source_anchors(
    config, *, base_anchor=None, cwd=None, observations=None, family="configuration",
):
    from configuration_layers import ConfigurationError

    from . import state_root
    from .machine_config import machine_config_root, machine_config_root_report

    if family not in {"configuration", "registry"}:
        raise ValueError("unsupported configuration source family")
    if base_anchor:
        base = base_anchor
    elif observations is None:
        base = state_root._git_toplevel(cwd) or _default_anchor(config)
    else:
        root_report = state_root._git_toplevel_report(cwd)
        observations.extend(root_report.observations)
        base = root_report.value or _default_anchor(config)
    sources = []
    if base:
        sources.append(ConfigSource(anchor=base, origin="harness", tier="repository"))
        observe(observations, base, "harness-source", ScanAuthority.COMPLETE)
    else:
        failed(observations, "base-anchor", "config-sources", "base-unavailable", "no base anchor resolved")
    selected = config if family == "registry" else _configuration_for_base(config, base)
    if family != "registry" and selected is not None:
        from . import project_state

        if project_state.namespaced():
            try:
                cfg.project_dir(selected.repo_name)
            except (OSError, ValueError) as error:
                if observations is None:
                    raise ConfigurationError(
                        "selected repository configuration root is unavailable"
                    ) from error
                failed(observations, selected.repo_name, "config-sources",
                       "source-selection-failed", type(error).__name__)
    try:
        if observations is None:
            local = machine_config_root(base, project=selected.repo_name) if selected else None
        else:
            local = None
            if selected is not None:
                local_report = machine_config_root_report(base, project=selected.repo_name)
                local = local_report.value
                observations.extend(local_report.observations)
    except Exception as exc:
        if observations is None:
            raise
        failed(observations, "machine-config", "config-sources", "source-selection-failed", type(exc).__name__)
        local = None
    read_knowledge = (
        _knowledge_registry_source if family == "registry" else _knowledge_config_source
    )
    if selected is None:
        knowledge = None
    elif observations is None:
        knowledge = read_knowledge(selected, base_anchor=base, cwd=cwd)
    else:
        try:
            knowledge = read_knowledge(
                selected, base_anchor=base, cwd=cwd, observations=observations,
            )
        except Exception as exc:
            failed(observations, "knowledge", "config-sources", "source-selection-failed", type(exc).__name__)
            knowledge = None
    if knowledge:
        sources.append(knowledge)
    if family != "registry":
        try:
            global_root = cfg.install_dir()
            candidate = global_root / "related.yaml"
            present = candidate.is_file()
        except (OSError, ValueError) as error:
            failed(observations, "machine-wide", "config-sources", "source-selection-failed",
                   type(error).__name__)
            if observations is None:
                raise ConfigurationError(
                    "machine-wide configuration root is unavailable"
                ) from error
        else:
            observe(observations, str(candidate), "machine-wide-source",
                    ScanAuthority.COMPLETE if present else ScanAuthority.ABSENT)
            if present:
                sources.append(ConfigSource(
                    anchor=str(global_root), origin="machine", tier="machine", directory_form=True,
                ))
    if local:
        sources.append(ConfigSource(anchor=local, origin="machine", tier="repository_machine"))
    return sources


def _knowledge_config_source(config, *, base_anchor=None, cwd=None, observations=None):
    from configuration_layers import ConfigurationError

    from . import state_root

    binding = config.knowledge_repo
    if not isinstance(binding, str):
        raise ConfigurationError("knowledge binding must name a registered repository")
    if not binding:
        observe(observations, "knowledge", "knowledge-source", ScanAuthority.ABSENT)
        return None
    res = state_root.resolve_state_root(config, repo_override=binding, cwd=cwd)
    if not res.bound or not res.path:
        failed(observations, binding, "knowledge-source", "knowledge-unavailable",
               "explicit knowledge source is unavailable")
        raise ConfigurationError("explicit knowledge binding is unresolved")
    observe(observations, res.path, "knowledge-source", ScanAuthority.COMPLETE)
    if not base_anchor or Path(res.path).resolve() != Path(base_anchor).resolve():
        return ConfigSource(anchor=res.path, origin="knowledge", tier="repository")
    return None


def _configuration_for_base(config, base):
    """Use existing exact checkout/project owners; never infer a project name."""
    default = _default_anchor(config)
    if base is None or default is None:
        return config if base is None else None
    if Path(base).resolve() == Path(default).resolve():
        return config
    from . import git_ops

    anchor = git_ops.resolve_to_anchor(Path(base))
    if Path(anchor).resolve() == Path(default).resolve():
        return config
    from .front_door_cli import _reverse_lookup_project

    name = _reverse_lookup_project(Path(anchor))
    if name is None:
        return None
    from configuration_layers import ConfigurationError

    try:
        return cfg.load_config(
            cfg.project_dir(name) / "config.yaml", project=name,
            include_control_plane_related_pr=False, include_knowledge_preferences=False,
        )
    except (OSError, ValueError) as error:
        raise ConfigurationError("selected repository configuration is unavailable") from error


def _knowledge_registry_source(config, *, base_anchor=None, cwd=None, observations=None):
    from . import state_root

    try:
        repo = config.default_repo
    except KeyError:
        failed(observations, "default-repo", "knowledge-source", "source-selection-failed",
               "default repo is unavailable; knowledge requirement is unknown")
        return None
    if not (getattr(repo, "requires_external_state_root", False) or getattr(repo, "stateless", False)):
        observe(observations, "knowledge", "knowledge-source", ScanAuthority.ABSENT)
        return None
    res = state_root.resolve_state_root(config, cwd=cwd)
    if res.requires_external and res.bound and res.path:
        observe(observations, res.path, "knowledge-source", ScanAuthority.COMPLETE)
        if not base_anchor or os.path.abspath(res.path) != os.path.abspath(base_anchor):
            return ConfigSource(anchor=res.path, origin="knowledge")
    elif observations is not None:
        failed(observations, res.repo or "knowledge", "knowledge-source", "knowledge-unavailable",
               "required knowledge source is unavailable")
    return None


def config_source_anchors_report(
    config: cfg.Config, *, base_anchor: str | None = None, cwd: str | None = None,
    family: str = "configuration",
) -> AcquisitionReport[list[ConfigSource]]:
    """Report anchor-selection coverage; supplied Config has no full read closure."""
    observations: list[SourceObservation] = []
    value = _config_source_anchors(
        config, base_anchor=base_anchor, cwd=cwd, observations=observations, family=family,
    )
    return AcquisitionReport("config-source-anchors", value, tuple(observations))
