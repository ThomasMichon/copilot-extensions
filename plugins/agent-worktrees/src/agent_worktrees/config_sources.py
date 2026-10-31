"""Ordered config-read overlays, separate from the personal-state write root."""

from __future__ import annotations

import os
from dataclasses import dataclass

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
) -> list[ConfigSource]:
    """Ordered ``.agent-*`` config sources for the current launch context.

    This is the **knowledge overlay** (config-graft) seam (E1e): agent-* tools
    that read harness config (``related.yaml``, ``machines.yaml``, ...) union
    across these anchors, in **overlay order** (later wins): the base (harness /
    launch) anchor, then the machine-local project root (``get config-dir``,
    where harness setup writes machine-specific entries) when it holds agent
    config, then the bound **knowledge repo** when the launch repo requires one.

    This is the config-READ axis, distinct from the **state-root** (the personal-
    state WRITE destination): it only reuses the state-root resolver to LOCATE the
    knowledge checkout. A repo with neither a machine-local root nor a knowledge
    binding yields just its own anchor, like the pre-overlay single-anchor path.

    ``base_anchor`` is an explicit base (a ``--repo`` target or the control-plane
    anchor; defaults to the git root of ``cwd``, then the launch repo's anchor).
    Returns the sources base first; empty only when no base anchor resolves.
    """
    return _config_source_anchors(config, base_anchor=base_anchor, cwd=cwd)


def knowledge_config_source(
    config: cfg.Config, *, base_anchor: str | None = None, cwd: str | None = None,
) -> ConfigSource | None:
    """Resolve the existing bound knowledge source without choosing a machine root."""
    return _knowledge_config_source(config, base_anchor=base_anchor, cwd=cwd)


def _config_source_anchors(config, *, base_anchor=None, cwd=None, observations=None):
    from . import state_root
    from .machine_config import machine_config_root, machine_config_root_report

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
        sources.append(ConfigSource(anchor=base, origin="harness"))
        observe(observations, base, "harness-source", ScanAuthority.COMPLETE)
    else:
        failed(observations, "base-anchor", "config-sources", "base-unavailable", "no base anchor resolved")
    try:
        if observations is None:
            local = machine_config_root(base, project=config.repo_name)
        else:
            local_report = machine_config_root_report(base, project=config.repo_name)
            local = local_report.value
            observations.extend(local_report.observations)
    except Exception as exc:
        if observations is None:
            raise
        failed(observations, "machine-config", "config-sources", "source-selection-failed", type(exc).__name__)
        local = None
    if local:
        sources.append(ConfigSource(anchor=local, origin="machine"))
    if observations is None:
        knowledge = knowledge_config_source(config, base_anchor=base, cwd=cwd)
    else:
        try:
            knowledge = _knowledge_config_source(config, base_anchor=base, cwd=cwd, observations=observations)
        except Exception as exc:
            failed(observations, "knowledge", "config-sources", "source-selection-failed", type(exc).__name__)
            knowledge = None
    if knowledge:
        sources.append(knowledge)
    return sources


def _knowledge_config_source(config, *, base_anchor=None, cwd=None, observations=None):
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
) -> AcquisitionReport[list[ConfigSource]]:
    """Report anchor-selection coverage; supplied Config has no full read closure."""
    observations: list[SourceObservation] = []
    value = _config_source_anchors(config, base_anchor=base_anchor, cwd=cwd, observations=observations)
    return AcquisitionReport("config-source-anchors", value, tuple(observations))
