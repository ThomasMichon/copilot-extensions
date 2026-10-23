"""Ordered config-read overlays, separate from the personal-state write root."""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import config as cfg


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
    from . import state_root
    from .machine_config import machine_config_root

    base = base_anchor or state_root._git_toplevel(cwd) or _default_anchor(config)
    sources: list[ConfigSource] = []
    if base:
        sources.append(ConfigSource(anchor=base, origin="harness"))
    if local := machine_config_root(base, project=config.repo_name):
        sources.append(ConfigSource(anchor=local, origin="machine"))
    try:
        repo = config.default_repo
    except KeyError:
        return sources
    if not (
        getattr(repo, "requires_external_state_root", False)
        or getattr(repo, "stateless", False)
    ):
        return sources
    res = state_root.resolve_state_root(config, cwd=cwd)
    if res.requires_external and res.bound and res.path:
        if not base or os.path.abspath(res.path) != os.path.abspath(base):
            sources.append(ConfigSource(anchor=res.path, origin="knowledge"))
    return sources
